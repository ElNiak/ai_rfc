"""Ask a model whether a hunk of code really does what a claim says it does.

The rest of the score is deterministic; this is the one term that needs
reading comprehension, because "the claim is anchored to code" and "the claim
describes that code" are different properties and only the first can be
checked mechanically. A claim can pin a verified commit and still say
something the diff does not do.

The rubric is deliberately kept out of the candidate's reach and out of the
feedback the search backend reads. A candidate that could see how it is being
graded would be rewritten to satisfy the grader rather than to reconstruct
the specification, and the measurement would stop meaning anything.

The transport is ``urllib`` on purpose: the harness runs in a 3.10 virtual
environment that cannot import litellm, and one POST needs no SDK.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Callable, MutableMapping

from .. import ExperimentError
from .scoring import ClaimHunk, Judge, Judgement

#: Turns one prompt into one raw model reply.
Transport = Callable[[str], str]

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

_PERMITTED_SCORES = (0.0, 0.5, 1.0)
_REPLY_EXCERPT = 80
_BODY_EXCERPT = 200

RUBRIC = """\
You are grading one reconstructed requirement against the code it cites.

You will be shown a single claim — its identifier, its RFC 2119 level, and its
text — followed by the evidence for it in two parts: first the cluster's own
change to one file, as a unified diff, and then, under a "context" heading, a
slice of that file as it stood at the pinned commit.

Grade the CHANGE. The context is there to make the diff readable and nothing
more; code that appears only in the context was already there, and a claim
resting on it is a claim about work this change did not do. Judge only what
you are shown: do not assume behaviour living elsewhere in the file or the
project, and do not reward a claim for being plausible.

Grade on exactly three values:

1   — the change plainly implements or alters exactly the behaviour claimed.
      Someone reading the diff alone would write this claim.
0.5 — the change concerns that behaviour, but the claim misstates it: the
      level is stronger or weaker than the code enforces, the condition
      differs, or the claim asserts detail the change does not show.
0   — the change does not support the claim. It is unrelated code, it is test
      scaffolding or configuration rather than the behaviour itself, the claim
      describes something the change does not do, or the claim describes code
      that is only in the context and so predates this change.

Reply with ONLY a JSON object, no prose before or after it, in exactly this
shape:

{"score": 0 | 0.5 | 1, "rationale": "<one sentence naming the deciding line>"}\
"""


class JudgeError(ExperimentError):
    """Raised when the judge's transport cannot deliver a reply."""


def anthropic_transport(
    model: str,
    *,
    api_key_env: str = "ANTHROPIC_API_KEY",
    temperature: float = 0.0,
    max_tokens: int = 300,
    timeout_s: float = 60.0,
) -> Transport:
    """One POST to the messages API, returning its first text block.

    Args:
        model: The model id to grade with.
        api_key_env: Environment variable holding the credential.
        temperature: Sampling temperature; zero so a rerun reproduces.
        max_tokens: Cap on the reply, which is one small JSON object.
        timeout_s: Socket timeout for the request.

    Returns:
        A transport closing over the credential.

    Raises:
        JudgeError: If the credential is not set. The returned transport
            raises the same error for an HTTP failure, an unreachable host, or
            a reply carrying no text block.
    """
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise JudgeError(
            f"{api_key_env} is not set; the judge has no credential to call "
            f"{model} with"
        )

    def send(prompt: str) -> str:
        request = urllib.request.Request(
            ANTHROPIC_URL,
            data=json.dumps(
                {
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ).encode(),
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read() or b""
            raise JudgeError(
                f"anthropic returned HTTP {error.code}: "
                f"{body.decode(errors='replace')[:_BODY_EXCERPT]}"
            ) from None
        except (urllib.error.URLError, OSError) as error:
            raise JudgeError(f"anthropic could not be reached: {error}") from None
        except ValueError as error:
            raise JudgeError(f"anthropic returned unreadable JSON: {error}") from None

        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
        raise JudgeError(
            f"anthropic returned no text block: "
            f"{json.dumps(payload)[:_BODY_EXCERPT]}"
        )

    return send


def _cache_key(hunk: ClaimHunk) -> str:
    """What makes two judgements the same question.

    The level is part of the question, not a label on it: the rubric docks a
    claim whose level is stronger or weaker than the code enforces. Keying on
    the text and the code alone would let a relabelled claim hit the earlier
    verdict, and that penalty — the only thing bounding a mislabelled claim —
    would never fire.
    """
    return hashlib.sha256(
        f"{hunk.level}\0{hunk.text}\0{hunk.hunk}".encode()
    ).hexdigest()


def _prompt(hunk: ClaimHunk) -> str:
    """The rubric, the claim, and the code, in that order."""
    at = "" if hunk.line is None else f", line {hunk.line}"
    return (
        f"{RUBRIC}\n\n"
        f"Claim id: {hunk.claim_id}\n"
        f"Level: {hunk.level}\n"
        f"Claim: {hunk.text}\n\n"
        f"Evidence for {hunk.path}{at} at commit {hunk.commit}:\n\n"
        f"{hunk.hunk}\n"
    )


def _parse(claim_id: str, reply: str) -> Judgement:
    """Read one reply, tolerating prose around the object but nothing inside it.

    A score outside the three permitted values is a broken contract, not a
    number to round: a model that answered 0.7 was not grading on this scale,
    and averaging its answer in would quietly change what the term measures.
    """
    unparseable = Judgement(
        claim_id, 0.0, f"unparseable judge reply: {reply[:_REPLY_EXCERPT]}"
    )
    try:
        payload, _ = json.JSONDecoder().raw_decode(reply, reply.index("{"))
    except ValueError:
        return unparseable
    if not isinstance(payload, dict):
        return unparseable
    score = payload.get("score")
    # ``True == 1`` in Python, so a boolean would otherwise pass as a score.
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return unparseable
    if float(score) not in _PERMITTED_SCORES:
        return unparseable
    return Judgement(claim_id, float(score), str(payload.get("rationale", "")).strip())


def build_judge(
    transport: Transport,
    *,
    cache: MutableMapping[str, Judgement] | None = None,
) -> Judge:
    """A judge that grades each hunk with one transport call.

    Neither a transport failure nor an unreadable reply aborts the batch: the
    claim scores zero with the reason in its rationale and the rest are still
    graded, because one dropped connection should not void a whole campaign's
    relevance term.

    Only a parsed judgement is remembered. Caching a failure would freeze a
    transient outage into a verdict that never gets retried.

    Args:
        transport: Sends one prompt and returns the raw reply.
        cache: Judgements by claim text and hunk. Caching is opt-in: without
            one the judge sends exactly one call per hunk, so a caller that
            wants a repeated question asked afresh gets it.

    Returns:
        The judge.
    """

    def judge(hunks: list[ClaimHunk]) -> list[Judgement]:
        judgements: list[Judgement] = []
        for hunk in hunks:
            key = _cache_key(hunk)
            remembered = None if cache is None else cache.get(key)
            if remembered is not None:
                # Re-labelled: the key is the question, not the claim asking
                # it, so two ids sharing a text and a hunk share an entry.
                judgements.append(
                    Judgement(hunk.claim_id, remembered.score, remembered.rationale)
                )
                continue
            try:
                reply = transport(_prompt(hunk))
            except Exception as error:  # noqa: BLE001 - any transport may fail
                judgements.append(
                    Judgement(hunk.claim_id, 0.0, f"judge error: {error}")
                )
                continue
            judgement = _parse(hunk.claim_id, reply)
            if cache is not None and not judgement.rationale.startswith(
                "unparseable judge reply: "
            ):
                cache[key] = judgement
            judgements.append(judgement)
        return judgements

    return judge
