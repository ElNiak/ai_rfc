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
from its working directory alone, with nothing in its prompt to say so. That
last part is the one a caller can undo by handing in its own CLI call, so
:func:`judge_draft` refuses one :func:`judge_transport` did not build.

The transport enforces the fourth part itself: a session that reports tools or
MCP servers it was not launched with is refused rather than read, since what
the flags asked for is not evidence of what the session got.

What the judge quotes is checked in the same call that asked for the grades,
against the body that call sent. Left to the caller it is a step a caller can
skip, and a report whose quotes were never checked reads exactly like one
whose quotes all checked out -- so a judge that cited text the draft does not
contain would come back with a usable-looking score.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, replace
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

#: The grading scale, stated once. ``RUBRIC`` asks for it and :func:`_parse`
#: admits nothing else, and they are the same constant because a prompt that
#: asks for one range beside a parser that accepts another records a number
#: nobody graded on.
_SCORE_SCALE = range(1, 6)

#: What :func:`judge_transport` stamps on the call it builds and
#: :func:`judge_draft` looks for. Provenance rather than a check on the
#: directory itself, because "this path names no project" is not decidable:
#: ``TMPDIR`` can sit under a project tree and ``~/ai-rfc-experiments`` is a
#: home-directory layout. Who chose the directory is decidable, and it is the
#: claim that actually carries the blinding.
_NEUTRAL_CWD_MARK = "_judge_neutral_cwd"

#: Prefix for the directory a judge call runs in. Two characters and a dash:
#: the whole point is that the path says nothing, and a longer name is another
#: chance to say something.
_CWD_PREFIX = "j-"

#: What stands where a harness token did. It marks the citation as present --
#: whether a normative sentence carries one is a quality property, so deleting
#: it outright would change what the judge sees the draft doing -- while
#: naming neither the harness nor the claim.
_PLACEHOLDER = "[citation]"

#: The harness's name, in whatever case and with whichever of its two
#: separators it is written: ``ai_rfc`` is the package and the citation form,
#: ``ai-rfc`` is the prose form this codebase already ships -- ``config.py``
#: defaults a draft's author to "ai-rfc harness" -- ``a_rfc`` is the legacy
#: citation spelling, and a sentence or a heading capitalises any of them.
#: One pattern over the name rather than one per spelling, so a spelling
#: nobody listed is not the one that leaks.
#:
#: The separator stops at ``_`` and ``-`` deliberately. Widening it to any
#: non-word character costs a real sentence: one space is a non-word
#: character, so "an AI RFC" would match and be cut out of the prose the
#: judge is grading. What holds the name together as one token is exactly
#: what belongs in this class, and a space does not.
_NAME = r"ai?[_-]rfc"

#: The name inside backticks: the citation form ``ai_rfc:<claim-id>`` and the
#: bare ``ai_rfc`` the Conventions section names when it explains the
#: convention.
_BACKTICKED_TOKEN = re.compile(rf"`\s*{_NAME}[^`]*`", re.IGNORECASE)

#: The same name outside backticks, which is how the structure-block
#: delimiters inside ``{::comment}`` blocks spell it, and how prose naming the
#: harness spells it.
_BARE_TOKEN = re.compile(rf"\b{_NAME}\S*", re.IGNORECASE)

RUBRIC = """\
You are grading one Internet-Draft for the quality of its specification prose.

You will be shown the body of a draft: its abstract, its middle sections and
its back matter, as the author wrote them. Judge what you are shown. Do not
assume the document says anything elsewhere, do not reward it for a topic that
sounds important, and do not reward or penalise it for the system it describes
rather than for how it describes it.

Grade each dimension you are asked for on an integer scale of 1 to 5, where 1
is unusable and 5 is what a reader would accept from a published standard. A
number outside that scale is not a grade on it, and the whole reply is refused.

Support your grades with short verbatim quotations from the document. Every
quotation must be copied from the document below exactly as it appears there.
Each one is searched for in that document, and any that is not found there is
returned alongside your grades as a quotation the document does not contain.
So quote nothing you cannot find, and quote nothing at all rather than
paraphrase.

Reply with ONLY a JSON object, no prose before or after it, in exactly this
shape, scoring every dimension you were asked for and no others:

{"scores": {"<dimension>": 4}, "quotes": ["a verbatim span from the text"]}\
"""


class JudgeError(ExperimentError):
    """Raised where a figure or a verdict would otherwise be recorded that
    nothing produced.

    That is the predicate, and it is deliberately not a list of the places it
    is raised from: a judge call that cannot be made, a reply that did not
    answer this contract, and a check that cannot be carried out are different
    events with one thing in common worth refusing -- a number in a manifest,
    or a clean bill on a report's quotes, with no measurement behind it. A
    reply outside the pinned shape is a fault and never a low score: a model
    that answered something else was not grading on this contract, and reading
    a number out of it anyway produces exactly the figure this refuses.
    """


@dataclass(frozen=True)
class JudgeReport:
    """One judge's grades for one draft, what it quoted, and what checked out.

    Attributes:
        scores: The grade per dimension, on the scale the rubric states. A
            number off that scale never reaches here -- the reply is refused
            -- because a number graded on some other scale is not a
            measurement on this one, and recording it as one would leave a
            reader worse off than an absent figure.
        quotes: The spans the judge says it copied out of the draft, in reply
            order, as it sent them. What became of them is ``unverified``.
        model: The model the session reported answering as, which is not
            necessarily the one the transport asked for. ``None`` when the
            transport reported no init event to read it from -- unmeasured,
            never defaulted to the id somebody requested.
        body: The blinded text the judge was shown, and so the text its
            quotes are checked against. ``None`` when the report did not come
            from :func:`judge_draft` -- unmeasured, and :func:`verify_quotes`
            refuses such a report rather than answering from no haystack.
        unverified: The quotes that are not in ``body``, in reply order. An
            empty tuple means every quote checked out and ``None`` means no
            check was run, which is a different claim and never defaulted
            into the first. A non-empty one is a finding about the judge
            rather than a term to fold into a score: a judge that cites text
            the draft does not contain has not graded the draft.
    """

    scores: Mapping[str, int]
    quotes: tuple[str, ...] = ()
    model: str | None = None
    body: str | None = None
    unverified: tuple[str, ...] | None = None


def blinded_body(text: str) -> str:
    """One draft's body, with what identifies its harness taken out of it.

    Two things come off. The front matter goes because it is where a draft
    says whose it is: ``docname`` spells ``draft-<author>-<target>`` and the
    ``author:`` block names a person, an organisation and an email. The
    harness's citation tokens go because the body carries them in three
    places the front matter does not -- a backticked ``ai_rfc:<claim-id>``
    beside a normative sentence, the bare delimiters of a structure block
    inside a comment, and the Conventions sentence that explains the
    convention by naming it. The sweep is over the name rather than over a
    list of its spellings, so the case it is written in and which of its two
    separators it carries do not decide whether it is caught.

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


def _prompt(body: str, *, dimensions: tuple[str, ...]) -> str:
    """The rubric, the dimensions asked for, and the blinded body.

    Args:
        body: What the judge is to be shown, as :func:`blinded_body` produces
            it. Taking the body rather than the draft is what lets one call
            send and verify against the same text.
        dimensions: What to grade, in the order the prompt lists them.

    Returns:
        The prompt.
    """
    asked = "\n".join(f"- {name}" for name in dimensions)
    return (
        f"{RUBRIC}\n\nGrade these dimensions:\n\n{asked}\n\nThe document:\n\n{body}\n"
    )


def _parse(
    reply: str, *, dimensions: tuple[str, ...], model: str | None, body: str
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
        body: What the judge was shown, carried onto the report so its quotes
            have a haystack that no caller had to pick.

    Returns:
        The report, with its quotes not yet checked.

    Raises:
        JudgeError: If the reply carries no JSON object, or one outside the
            pinned shape, which includes a grade off the rubric's scale.
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
        if value not in _SCORE_SCALE:
            raise JudgeError(
                f"the judge scored {name!r} as {value}, off the "
                f"{_SCORE_SCALE.start} to {_SCORE_SCALE[-1]} scale the rubric "
                f"states; a number off the scale is not a grade on it"
            )
        graded[name] = value
    # Absent means no quotes; an explicit null is a different claim and is
    # refused, because a model that sent one was not answering this shape.
    quotes = payload.get("quotes", [])
    if not isinstance(quotes, list) or not all(
        isinstance(quote, str) for quote in quotes
    ):
        raise JudgeError(f"the judge's 'quotes' is {quotes!r}, not a list of strings")
    return JudgeReport(scores=graded, quotes=tuple(quotes), model=model, body=body)


def judge_draft(
    text: str, transport: Transport, *, dimensions: tuple[str, ...]
) -> JudgeReport:
    """Grade one draft on the dimensions asked for, in one transport call.

    Args:
        text: The whole draft, front matter included; the blinding needs it
            whole to be able to take the front matter off.
        transport: Sends one prompt and returns the raw reply. A
            :class:`~.optimize.claude_cli.ClaudeCliCall` also reports which
            session answered, and that is read when it is there; it must be
            one :func:`judge_transport` built, since only that one is known
            to run where nothing is named. Any other callable starts no child
            process, so it has no working directory to leak and is asked for
            nothing.
        dimensions: What to grade.

    Returns:
        The report, with its quotes already checked against the body this
        call sent: ``unverified`` names the ones that are not in it.

    Raises:
        JudgeError: If the transport is a CLI call this module did not
            build, or if the draft cannot be put in front of a judge -- no
            dimension was asked for, or nothing survives the blinding. In
            none of the three is anything sent. Also if the reply comes back
            outside the pinned shape.
        ExperimentError: Whatever the transport raises; a
            :class:`ClaudeCliCall` raises its own subclasses for a call that
            failed and for a session that reported a surface it was not given.
    """
    if isinstance(transport, ClaudeCliCall) and not getattr(
        transport, _NEUTRAL_CWD_MARK, False
    ):
        raise JudgeError(
            "this judge call would run from a working directory nothing "
            "vouched for, and a CLI call made inside the project tree names "
            "the project through its path alone; build the transport with "
            "judge_transport()"
        )
    dimensions = tuple(dimensions)
    if not dimensions:
        raise JudgeError("a judge call must name at least one dimension to grade")
    body = blinded_body(text)
    if not body.strip():
        raise JudgeError(
            "nothing survives the blinding: the draft has no body to grade "
            "under its abstract, middle or back; nothing was sent to a judge"
        )
    reply = transport(_prompt(body, dimensions=dimensions))
    init = getattr(transport, "last_init", None)
    reported = init.get("model") if isinstance(init, dict) else None
    report = _parse(
        reply,
        dimensions=dimensions,
        model=reported if isinstance(reported, str) else None,
        body=body,
    )
    return replace(report, unverified=verify_quotes(report))


def _normalised(text: str) -> str:
    """Whitespace collapsed to single spaces, so a wrap is not a difference.

    A draft is hard-wrapped and a model quotes it back on one line, so a check
    that compared the two as written would fail every quote longer than a
    line.
    """
    return " ".join(text.split())


def verify_quotes(report: JudgeReport) -> tuple[str, ...]:
    """The report's quotes that are not in the body it was shown, in order.

    A judge that cites text the draft does not contain has not graded the
    draft, so a non-empty return is a finding about the report rather than a
    term to fold into a score.

    The haystack comes off the report rather than from the caller. A caller
    that chose it could pass the raw draft, whose front matter the judge never
    saw, and verify a quote against text that was never in front of it -- and
    nothing in the signature could tell the two apart.

    Args:
        report: The report to check.

    Returns:
        The unverified quotes. A quote that is only whitespace is always one:
        it cites nothing, and as a substring it would otherwise be found in
        every document there is.

    Raises:
        JudgeError: If the report carries no body. Answering "every quote is
            unverified" would be the same value a real body gives for a judge
            that invented every span, and one value for two causes leaves a
            reader misinformed rather than uninformed.
    """
    if report.body is None:
        raise JudgeError(
            "the report carries no body to check its quotes against; only a "
            "report from judge_draft has one"
        )
    haystack = _normalised(report.body)
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
        The transport, in the judge's argv regime, marked as one whose
        working directory this module chose. :func:`judge_draft` refuses a
        CLI call without that mark.
    """
    call = ClaudeCliCall(
        claude_bin,
        profile_dir,
        model,
        cwd=Path(tempfile.mkdtemp(prefix=_CWD_PREFIX)),
        effort=effort,
        timeout_s=timeout_s,
    )
    setattr(call, _NEUTRAL_CWD_MARK, True)
    return call


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
