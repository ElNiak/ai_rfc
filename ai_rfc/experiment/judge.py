"""Ask a model how good one draft's prose is, without telling it whose it is.

The lint is the deterministic half of draft quality: it counts sections,
citations, keywords and findings, and none of those say whether the document
reads like a specification. That judgement needs reading comprehension, so it
needs a model, and a model that knows which harness wrote the draft is not
grading the draft any more.

Blinding is by construction rather than by instruction, in three parts. The
prompt carries the draft's body and never its path, because the path names the
author and the target outright. The body has the front matter taken off it,
because a kramdown ``docname`` says ``draft-<author>-<target>`` and the
``author:`` block says the rest. And the call runs from a directory outside
any project tree, because the first probe for this design named the project
from its working directory alone, with nothing in its prompt to say so.

The transport enforces the fourth part itself: a session that reports tools or
MCP servers it was not launched with is refused rather than read, since what
the flags asked for is not evidence of what the session got.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from ai_rfc.draft.lint import _parts

from . import ExperimentError
from .optimize.claude_cli import ClaudeCliCall

#: Turns one prompt into one raw model reply. A :class:`ClaudeCliCall` is one;
#: so is any callable of the same shape, which is what the tests drive.
Transport = Callable[[str], str]

#: How much of an unreadable reply an error carries.
_REPLY_EXCERPT = 200

#: Prefix for the directory a judge call runs in. Two characters and a dash:
#: the whole point is that the path says nothing, and a longer name is another
#: chance to say something.
_CWD_PREFIX = "j-"

#: What stands where a harness token did. It marks the citation as present --
#: whether a normative sentence carries one is a quality property, so deleting
#: it outright would change what the judge sees the draft doing -- while
#: naming neither the harness nor the claim.
_PLACEHOLDER = "[citation]"

#: The harness's own name inside backticks: the citation form
#: ``ai_rfc:<claim-id>``, its legacy ``a_rfc:`` spelling, and the bare
#: ``ai_rfc`` the Conventions section names when it explains the convention.
#: One pattern over the name rather than three over the forms it is written
#: in, so a fourth form is not the one that leaks.
_BACKTICKED_TOKEN = re.compile(r"`\s*ai?_rfc[^`]*`")

#: The same name outside backticks, which is how the structure-block
#: delimiters inside ``{::comment}`` blocks spell it.
_BARE_TOKEN = re.compile(r"\bai?_rfc\S*")

RUBRIC = """\
You are grading one Internet-Draft for the quality of its specification prose.

You will be shown the body of a draft: its abstract, its middle sections and
its back matter, as the author wrote them. Judge what you are shown. Do not
assume the document says anything elsewhere, do not reward it for a topic that
sounds important, and do not reward or penalise it for the system it describes
rather than for how it describes it.

Grade each dimension you are asked for on an integer scale of 1 to 5, where 1
is unusable and 5 is what a reader would accept from a published standard.

Support your grades with short verbatim quotations from the document. Every
quotation must be copied from the document below exactly as it appears there.
A quotation that is not in the document invalidates the whole reply, so quote
nothing you cannot find, and quote nothing at all rather than paraphrase.

Reply with ONLY a JSON object, no prose before or after it, in exactly this
shape, scoring every dimension you were asked for and no others:

{"scores": {"<dimension>": 4}, "quotes": ["a verbatim span from the text"]}\
"""


class JudgeError(ExperimentError):
    """Raised when a judge call yields no report.

    Either the draft could not be put in front of a judge -- no body survives
    the blinding, or no dimension was asked for -- or the reply came back
    outside the pinned shape. A reply outside the shape is a fault and never a
    low score: a model that answered something else was not grading on this
    contract, and reading a number out of it anyway would put a figure in a
    manifest that nothing produced.
    """


@dataclass(frozen=True)
class JudgeReport:
    """One judge's grades for one draft, and what it quoted to support them.

    Attributes:
        scores: The grade per dimension, as the judge gave it. The pinned
            shape is an integer per dimension and says nothing about a range,
            so a grade outside the rubric's scale reaches a caller as itself.
        quotes: The spans the judge says it copied out of the draft, in reply
            order. Nothing here is verified; :func:`verify_quotes` is what
            checks them, and it is a separate step because an unverified quote
            is a finding about the judge rather than a term in a score.
        model: The model the session reported answering as, which is not
            necessarily the one the transport asked for. ``None`` when the
            transport reported no init event to read it from -- unmeasured,
            never defaulted to the id somebody requested.
    """

    scores: Mapping[str, int]
    quotes: tuple[str, ...] = ()
    model: str | None = None


def blinded_body(text: str) -> str:
    """One draft's body, with what identifies its harness taken out of it.

    Two things come off. The front matter goes because it is where a draft
    says whose it is: ``docname`` spells ``draft-<author>-<target>`` and the
    ``author:`` block names a person, an organisation and an email. The
    harness's citation tokens go because the body carries them in three
    places the front matter does not -- a backticked ``ai_rfc:<claim-id>``
    beside a normative sentence, the bare delimiters of a structure block
    inside a comment, and the Conventions sentence that explains the
    convention by naming it.

    Comment blocks are kept. They are what the author left standing, and a
    draft still carrying its own authoring instructions is a draft in that
    state; the tokens inside them are neutralised by the sweep above, so
    nothing is riding on dropping them.

    What this promises is exactly those two removals. It does not promise that
    author-written prose names no project: a mechanical filter cannot, and a
    draft whose subject really is this harness would have to be judged some
    other way.

    Args:
        text: The whole kramdown-rfc draft, front matter included.

    Returns:
        The abstract, middle and back, joined in that order, with every
        harness token replaced by a neutral placeholder. Empty or whitespace
        when the text has no body to grade.
    """
    parts = _parts(text)
    body = "\n".join(parts[name] for name in ("abstract", "middle", "back"))
    body = _BACKTICKED_TOKEN.sub(f"`{_PLACEHOLDER}`", body)
    return _BARE_TOKEN.sub(_PLACEHOLDER, body)


def _prompt(text: str, *, dimensions: tuple[str, ...]) -> str:
    """The rubric, the dimensions asked for, and the blinded body.

    Args:
        text: The whole draft.
        dimensions: What to grade, in the order the prompt lists them.

    Returns:
        The prompt.

    Raises:
        JudgeError: If no dimension was asked for, or if nothing survives the
            blinding. Grading an empty body would come back with numbers that
            read like a verdict on a draft.
    """
    if not dimensions:
        raise JudgeError("a judge call must name at least one dimension to grade")
    body = blinded_body(text)
    if not body.strip():
        raise JudgeError(
            "nothing survives the blinding: the draft has no body to grade "
            "under its abstract, middle or back; nothing was sent to a judge"
        )
    asked = "\n".join(f"- {name}" for name in dimensions)
    return (
        f"{RUBRIC}\n\nGrade these dimensions:\n\n{asked}\n\nThe document:\n\n{body}\n"
    )


def _parse(
    reply: str, *, dimensions: tuple[str, ...], model: str | None
) -> JudgeReport:
    """Read one reply, tolerating prose around the object but nothing inside it.

    The ``raw_decode`` idiom is the optimize judge's, copied rather than
    imported: that parser is welded to a different unit, rubric and score
    domain, and sharing it would tie two contracts that have no reason to move
    together.

    Args:
        reply: The raw model reply.
        dimensions: Exactly the dimensions the prompt asked for.
        model: What the session reported answering as, or ``None``.

    Returns:
        The report.

    Raises:
        JudgeError: If the reply carries no JSON object, or one outside the
            pinned shape.
    """
    try:
        payload, _ = json.JSONDecoder().raw_decode(reply, reply.index("{"))
    except ValueError:
        raise JudgeError(
            f"the judge's reply carries no JSON object: {reply[:_REPLY_EXCERPT]}"
        ) from None
    if not isinstance(payload, dict):
        raise JudgeError(
            f"the judge replied with a {type(payload).__name__}, not an object: "
            f"{reply[:_REPLY_EXCERPT]}"
        )
    scores = payload.get("scores")
    if not isinstance(scores, dict):
        raise JudgeError(
            f"the judge's reply has no 'scores' mapping: {reply[:_REPLY_EXCERPT]}"
        )
    if set(scores) != set(dimensions):
        raise JudgeError(
            f"the judge graded {sorted(scores)} for the dimensions "
            f"{sorted(dimensions)}; a reply must score every one and no others"
        )
    graded: dict[str, int] = {}
    for name in dimensions:
        value = scores[name]
        # ``True == 1`` in Python, so a boolean would otherwise pass as a one.
        if isinstance(value, bool) or not isinstance(value, int):
            raise JudgeError(
                f"the judge scored {name!r} as {value!r}, which is not an integer"
            )
        graded[name] = value
    # Absent means no quotes; an explicit null is a different claim and is
    # refused, because a model that sent one was not answering this shape.
    quotes = payload.get("quotes", [])
    if not isinstance(quotes, list) or not all(
        isinstance(quote, str) for quote in quotes
    ):
        raise JudgeError(f"the judge's 'quotes' is {quotes!r}, not a list of strings")
    return JudgeReport(scores=graded, quotes=tuple(quotes), model=model)


def judge_draft(
    text: str, transport: Transport, *, dimensions: tuple[str, ...]
) -> JudgeReport:
    """Grade one draft on the dimensions asked for, in one transport call.

    Args:
        text: The whole draft, front matter included; the blinding needs it
            whole to be able to take the front matter off.
        transport: Sends one prompt and returns the raw reply. A
            :class:`~.optimize.claude_cli.ClaudeCliCall` also reports which
            session answered, and that is read when it is there.
        dimensions: What to grade.

    Returns:
        The report, with its quotes unverified -- see :func:`verify_quotes`.

    Raises:
        JudgeError: If the draft cannot be put in front of a judge, or the
            reply comes back outside the pinned shape.
        ExperimentError: Whatever the transport raises; a
            :class:`ClaudeCliCall` raises its own subclasses for a call that
            failed and for a session that reported a surface it was not given.
    """
    dimensions = tuple(dimensions)
    reply = transport(_prompt(text, dimensions=dimensions))
    init = getattr(transport, "last_init", None)
    reported = init.get("model") if isinstance(init, dict) else None
    return _parse(
        reply,
        dimensions=dimensions,
        model=reported if isinstance(reported, str) else None,
    )


def _normalised(text: str) -> str:
    """Whitespace collapsed to single spaces, so a wrap is not a difference.

    A draft is hard-wrapped and a model quotes it back on one line, so a check
    that compared the two as written would fail every quote longer than a
    line.
    """
    return " ".join(text.split())


def verify_quotes(report: JudgeReport, text: str) -> tuple[str, ...]:
    """The report's quotes that are not in the text, in report order.

    A judge that cites text the draft does not contain has not graded the
    draft, so a non-empty return is a finding about the report rather than a
    term to fold into a score.

    Args:
        report: The report to check.
        text: What the judge was shown, which :func:`blinded_body` produces.
            Passing the raw draft instead would check against text the judge
            never saw.

    Returns:
        The unverified quotes. A quote that is only whitespace is always one:
        it cites nothing, and as a substring it would otherwise be found in
        every document there is.
    """
    haystack = _normalised(text)
    unverified = []
    for quote in report.quotes:
        needle = _normalised(quote)
        if not needle or needle not in haystack:
            unverified.append(quote)
    return tuple(unverified)


def judge_transport(
    claude_bin: str,
    profile_dir: Path,
    model: str,
    *,
    effort: str = "high",
    timeout_s: int = 120,
) -> ClaudeCliCall:
    """A transport for :func:`judge_draft`, running where nothing is named.

    The working directory is a fresh temporary one, and that is the point: a
    call made from inside the project tree leaks the project through the path
    alone, which is how the first probe for this design came back naming a
    project its prompt had never mentioned. The directory is left behind when
    the transport goes; it is empty, and the alternative is a lifetime the
    caller has to manage for a judge that may be called once or a thousand
    times.

    Args:
        claude_bin: The CLI to launch.
        profile_dir: The authenticated ``CLAUDE_CONFIG_DIR`` to call under.
        model: The model to ask for. What answers is reported separately, on
            the call's ``last_init``.
        effort: The CLI's ``--effort`` level.
        timeout_s: Seconds before the child is killed and the call raises.

    Returns:
        The transport, in the judge's argv regime.
    """
    return ClaudeCliCall(
        claude_bin,
        profile_dir,
        model,
        cwd=Path(tempfile.mkdtemp(prefix=_CWD_PREFIX)),
        effort=effort,
        timeout_s=timeout_s,
    )


__all__ = [
    "RUBRIC",
    "JudgeError",
    "JudgeReport",
    "Transport",
    "blinded_body",
    "judge_draft",
    "judge_transport",
    "verify_quotes",
]
