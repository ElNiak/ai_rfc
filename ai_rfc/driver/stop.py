"""Why a sweep stopped, what it cost the cluster, and what to type next.

Three vocabularies meet here, and keeping them apart is the point of the
module.

:class:`StopReason` names why a *sweep* stopped — one member per stopping row
of spec §5's state machine, plus four events its rows cannot spell:
``session_failed`` and ``consolidation_failed`` (named in §5's prose),
``bound_reached`` and ``action_performed`` (the two stops the *operator*
asked for, through ``--until`` and ``ai-rfc next``); see their members.
Its members are the only stop names production
uses; before this module there were two unrelated ones (``budget`` as a
``budget_hit`` substring test in the campaign runner, ``surface_shortfall`` as
an ``outcome=`` value in the per-cluster loop) and ``wall_clock`` existed only
as a display string.

:data:`CLASSIFICATIONS` names what one *session* did. There are four rather
than a boolean because they differ in whether they consume one of a cluster's
attempts, and that difference has already been got wrong once: MARK's ordinal
38 halted on a $0, one-turn launch error with $12.62 of its budget still to
spend, and D38 recorded it as a budget stop (D61 corrects this). A launch
failure never reached the model, so charging the cluster an attempt for it
spends the retries meant for a model that answered badly on a session that did
not answer at all.

:func:`exit_code` and :func:`resume_line` are what an operator sees: done 0,
stopped with work outstanding 1, strict findings 3, and one line to copy.
"""

from __future__ import annotations

import shlex
from collections.abc import Collection
from enum import Enum
from pathlib import Path

from . import DriverError
from .session import SessionResult
from .stream import result_events

#: The root door's program name. Written rather than imported from
#: ``ai_rfc.cli``: that module is the top of the tree and this package is the
#: bottom, so importing it here would invert the layering for one string.
#: ``tests/driver/test_stop.py`` asserts the two still agree.
PROG = "ai-rfc"

#: The session worked — it reached the model and ended on its own terms — and
#: the cluster is not done. **The only classification that consumes one of the
#: cluster's attempts.**
REFUSED = "refused"
#: ``is_error`` with at most one turn: a launch or API failure. The session
#: never reached the model, so it consumes no attempt (D61).
ERRORED = "errored"
#: The timeout killed the process group. It did not end on its own terms, so
#: it consumes no attempt (D61).
#:
#: Spec §5 says "timeout or interrupt", but only the timeout can reach here:
#: ``spawn.py:97-108`` re-raises on an interrupt, so ``run_session`` never
#: returns and there is no result to classify. That is the design working — an
#: interrupted run writes no ``status.json`` and is moved aside on the next
#: resume — not a branch this function is missing.
KILLED = "killed"
#: The session stopped because ``--max-budget-usd`` was reached. A cap stopped
#: it, exactly as the wall clock stops a killed one, so it consumes no attempt.
BUDGET_HIT = "budget_hit"

#: Everything :func:`classify` can return, in the order spec §5 lists them.
CLASSIFICATIONS: tuple[str, ...] = (REFUSED, ERRORED, KILLED, BUDGET_HIT)

#: The classifications that spend one of a cluster's attempts.
#:
#: One member, and the whole reason the four exist. D61's rule is that attempts
#: count only sessions that *ended on their own*: a killed session was stopped
#: by the clock, a budget-hit one by the cap, and an errored one never started.
#: Only a refusal is a session the model was given and did not finish the
#: cluster with.
#:
#: **Ruling (Task 9): ``budget_hit`` consumes no attempt.** D61's "ended on its
#: own" is genuinely ambiguous for a session the budget cap stopped, so the
#: tiebreaker is consequence rather than wording. The sweep stops on
#: ``StopReason.budget`` either way, so an attempt consumed here could only
#: ever be spent against a *later, better-funded resume* — halting a cluster
#: permanently because the operator's cap ran out twice, not because the
#: cluster ever failed. That punishes the wrong thing.
CONSUMES_ATTEMPT: frozenset[str] = frozenset({REFUSED})

#: Exit code of a sweep that did what it was asked: ``done`` finished
#: everything, ``bound_reached`` stopped where ``--until`` told it to, and
#: ``action_performed`` performed the one action ``next`` asked for. See
#: :data:`_ZERO_EXIT`.
DONE_EXIT = 0
#: Exit code of a sweep that stopped with work outstanding — every stop
#: outside :data:`_ZERO_EXIT`, and the code the operator's resume line answers.
STOPPED_EXIT = 1
#: Exit code when the build gate's ``check --strict`` reported findings, which
#: is what ``ai-rfc check --strict`` itself returns (``check/cli.py:126``).
STRICT_FINDINGS_EXIT = 3


class StopReason(Enum):
    """Why a sweep stopped: spec §5's nine rows, and four it does not spell.

    ``session_failed`` and ``consolidation_failed`` are two of the four. Spec
    §5 says an *errored* session "stops with the resume line" and D59 says a
    failing sweep-end consolidation exits 1, but neither is a row of the table,
    so reporting them meant either a new name or a borrowed one.

    ``bound_reached`` is the third, added by Task 11 when ``--until`` was wired
    into ``ai-rfc run``: a bound is a place the operator asked the sweep to
    stop at, which no row describes and which ``done`` would misreport.

    ``action_performed`` is the fourth, added by Task 12 for ``ai-rfc next``.
    Spec §5 licenses it in one sentence — "``next`` performs one row" — and
    that is a stop the table cannot spell either, since the table says why a
    sweep *cannot go on* and this one stops because it was asked for one
    thing and did it.

    What borrowing would have cost differs by candidate, and only one of them
    is repair-level. ``cluster_halted`` would print ``--retry <id>``, telling
    the operator to reset attempts an errored session never spent — a wrong
    *repair*. ``stage_failed`` resumes with ``run`` exactly as these two do, so
    borrowing it would have cost only the **diagnosis**: it is documented as a
    deterministic stage exiting non-zero, and a session is not a stage.

    The value of every member is a bare identifier, because a stop name is
    interpolated into places that cannot escape it: a directory name through
    :func:`~ai_rfc.driver.record.move_aside`, whose ``_CAUSE`` grammar refuses
    non-ASCII and a leading ``-``, a report line, and a progress line a model
    reads in the optimize track.
    """

    #: The clone is not pinned; ``init`` does the clone and the forge (D34).
    needs_init = "needs_init"
    #: A deterministic stage the sweep performed exited non-zero.
    stage_failed = "stage_failed"
    #: The timeline or the views are stale and a checkpoint exists —
    #: re-clustering would renumber what those checkpoints pin.
    stale_substrate = "stale_substrate"
    #: A cluster reached its attempt cap without finishing.
    cluster_halted = "cluster_halted"
    #: The lifetime budget is spent.
    budget = "budget"
    #: The sweep's wall-clock cap is reached.
    wall_clock = "wall_clock"
    #: The first judgeable session mounted no ``ai_rfc`` MCP surface, so the
    #: rest of the window could not checkpoint, gate or tag either.
    surface_shortfall = "surface_shortfall"
    #: A session ended in a launch or API failure. Spec §5 says an *errored*
    #: session "stops with the resume line", and none of the nine rows can
    #: spell that: it is not a cluster reaching its attempt cap (an errored
    #: session consumes none), not a deterministic stage exiting non-zero, and
    #: not a surface shortfall. Added by Task 10 rather than mapped onto
    #: ``cluster_halted``, whose line would have told the operator to reset
    #: attempts that were never spent and cannot fix a launch failure.
    session_failed = "session_failed"
    #: The sweep-end consolidation round did not record its revision. D59
    #: makes exactly this exit 1 while a mid-sweep failure is only noted, so
    #: the sweep needs a name for it. Added by Task 10 alongside
    #: ``session_failed``; a consolidation is a session, not a stage, so
    #: ``stage_failed`` would have named the wrong performer.
    consolidation_failed = "consolidation_failed"
    #: The build gate (``check --strict``, ``lint``, ``build``) had findings.
    build_failed = "build_failed"
    #: A ``--until`` bound was reached. Not a failure, and **not** ``done``:
    #: the operator asked the sweep to stop somewhere and it got there, which
    #: says nothing about whether the reconstruction is finished — a bounded
    #: stop normally leaves clusters outstanding and never runs the build
    #: gate. So it exits 0 like ``done`` and carries a resume line unlike it.
    #: Added by Task 11, which wired ``--until`` into ``ai-rfc run``: before
    #: that the bound returned early, wrote no ``status.json``, and left a run
    #: directory the next invocation renamed as interrupted.
    bound_reached = "bound_reached"
    #: ``mode="one"`` performed its one action. Minted only by ``ai-rfc
    #: next``, and **not** ``done``: ``done`` means nothing outstanding and no
    #: round due, while one action out of a window's twenty normally leaves
    #: everything else to do — and ``done`` is refused a resume line, which is
    #: the one thing an operator stepping through a reconstruction needs
    #: most. Nor ``bound_reached``: no bound need have been given, and a
    #: status record claiming one would be a false account of the run.
    #:
    #: Exits 0 for the reading D59 and the settled table already fixed:
    #: "stopped with work outstanding 1" means the sweep *could not go on*,
    #: not that work exists. The no-sessions boundary stop returns 0 with
    #: every cluster outstanding, and so does ``--until <stage>``.
    #:
    #: Added by Task 12. Before it, ``mode="one"`` returned
    #: ``exit_code_for(done)`` directly, bypassing ``_finish`` — so a ``next``
    #: that launched a session wrote no ``status.json``, said neither what it
    #: had done nor what to type next, and left a run directory the following
    #: ``next`` renamed ``.interrupted-interrupt``.
    action_performed = "action_performed"
    #: Nothing outstanding and no round due. The only reason with no resume
    #: line: a finished reconstruction has nothing to resume.
    done = "done"


#: The verb each reason's resume line calls. ``run`` is the resume for anything
#: the operator fixes and retries; the three exceptions each send the operator
#: somewhere ``run`` would only stop again:
#:
#: ``needs_init``
#:     There is no workspace to resume into yet.
#: ``stale_substrate``
#:     ``run`` refuses a stale substrate under checkpoints (spec §7), so a
#:     resume line naming it would reproduce the stop verbatim. ``status``
#:     prints the stage states and the drift the operator has to rule on.
#: ``surface_shortfall``
#:     Spec §5 asks for a doctor hint: what failed is the environment the
#:     session was launched into, which is what ``doctor`` reports on.
#: ``action_performed``
#:     Only ``ai-rfc next`` mints this, so the operator who asked for one
#:     action is told how to ask for the next one. ``run`` would also carry
#:     the reconstruction forward, but it would carry it *all* the way, which
#:     is not what this operator asked for even once.
_VERB: dict[StopReason, str] = {
    StopReason.needs_init: "init",
    StopReason.stage_failed: "run",
    StopReason.stale_substrate: "status",
    StopReason.cluster_halted: "run",
    StopReason.budget: "run",
    StopReason.wall_clock: "run",
    StopReason.surface_shortfall: "doctor",
    StopReason.session_failed: "run",
    StopReason.consolidation_failed: "run",
    StopReason.build_failed: "run",
    StopReason.bound_reached: "run",
    StopReason.action_performed: "next",
}

#: The reasons that are not a failure. ``done`` is a finished reconstruction;
#: ``bound_reached`` is a sweep that did exactly what ``--until`` asked of it;
#: ``action_performed`` is a sweep that did exactly the one thing ``next``
#: asked of it. The last two leave work outstanding and still exit 0, because
#: spec §5's "stopped with work outstanding 1" is about a sweep that *could
#: not go on* — the no-sessions boundary stop has always returned 0 with every
#: cluster outstanding.
#:
#: Written as a set rather than tested against ``done`` alone so that adding a
#: member means saying so here, where the meaning of the exit code lives.
_ZERO_EXIT: frozenset[StopReason] = frozenset(
    {StopReason.done, StopReason.bound_reached, StopReason.action_performed}
)


def classify(result: SessionResult, *, seen: int = 0) -> str:
    """What one session did, as one of :data:`CLASSIFICATIONS`.

    Read in the order the evidence is trustworthy in. A kill is a fact about
    the process, so it is read before the transcript: a killed session emits no
    result event of its own, and the tail of the shared transcript is then
    still the *previous* session's success.

    The budget subtype is read before the turn count for the same reason D38
    was wrong the other way round. ``is_error`` with one turn is the shape of a
    launch failure, but a launch failure cannot produce a subtype naming the
    budget — so testing the subtype first keeps a first-turn budget stop and a
    one-turn launch error distinguishable, while testing the turn count first
    would file the budget stop as an error that consumed nothing.

    Args:
        result: What :func:`~ai_rfc.driver.session.run_session` returned.
        seen: How many result events the transcript held **before** this
            session — the same value handed to ``run_session``. Leaving it at 0
            on a transcript that already holds results classifies this session
            on an earlier one's result event, which for a session that died
            without saying anything returns ``refused`` and charges the cluster
            an attempt it never spent. That is D61's defect exactly.

    Returns:
        One of :data:`CLASSIFICATIONS`. Pass it to :func:`consumes_attempt`
        rather than comparing it by hand.

    Raises:
        DriverError: If ``seen`` is negative. Python would slice that from the
            tail instead — ``seen=-1`` reads the last result event whatever the
            transcript holds, which is a wrong answer rather than an error.
    """
    if seen < 0:
        raise DriverError(
            f"seen={seen} is not a count of result events; a negative one "
            "slices from the end of the transcript and classifies this session "
            "on somebody else's result"
        )
    if result.timed_out:
        return KILLED
    mine = result_events(list(result.events))[seen:]
    if not mine:
        # The process returned without a result event of its own: the CLI never
        # got far enough to report. Nothing about the model can be read off
        # this, which is precisely what "errored" says.
        return ERRORED
    final = mine[-1]
    # The substring test, not an equality: the subtype carries the reason as
    # part of a longer name. `experiment/runner.py:292` is the precedent, and
    # it cannot be imported — this package may not depend on `ai_rfc.experiment`.
    if "budget" in str(final.get("subtype", "")).lower():
        return BUDGET_HIT
    turns = final.get("num_turns")
    # A session that failed before its first turn omits the count entirely, so
    # this must not be a comparison against None.
    counted = int(turns) if isinstance(turns, (int, float)) else 0
    if final.get("is_error") and counted <= 1:
        return ERRORED
    return REFUSED


def consumes_attempt(classification: str) -> bool:
    """Whether this session spent one of its cluster's attempts.

    Args:
        classification: One of :data:`CLASSIFICATIONS`, as :func:`classify`
            returned it.

    Returns:
        True only for a refusal. See :data:`CONSUMES_ATTEMPT`.

    Raises:
        DriverError: If the classification is not one of the four. An unknown
            label must not read as "consumed nothing": that answer retries the
            cluster forever, and it is the answer a typo would otherwise get.
    """
    if classification not in CLASSIFICATIONS:
        raise DriverError(
            f"{classification!r} is not a session classification; classify "
            f"returns one of {', '.join(CLASSIFICATIONS)}, and an unrecognised "
            "one would silently consume no attempt and retry forever"
        )
    return classification in CONSUMES_ATTEMPT


def exit_code(reason: StopReason, *, strict_findings: bool = False) -> int:
    """The process exit code a sweep that stopped for this reason returns.

    Args:
        reason: Why the sweep stopped.
        strict_findings: Whether the build gate stopped on ``check --strict``
            reporting findings, rather than on ``lint`` or ``build`` failing.
            It is a separate argument because the reason cannot carry it: spec
            §5 gives one ``build_failed`` for all three gates while also
            reserving 3 for strict findings, and only the caller that ran the
            gate knows which fired.

    Returns:
        :data:`DONE_EXIT` for the reasons that are not a failure
        (:data:`_ZERO_EXIT`: ``done``, ``bound_reached`` and
        ``action_performed``), :data:`STRICT_FINDINGS_EXIT` for a build gate
        that reported strict findings, :data:`STOPPED_EXIT` otherwise.

    Raises:
        DriverError: If ``strict_findings`` is given for anything but
            ``build_failed``; no other stop runs the check gate, so it could
            only be a caller passing the flag through by accident.
    """
    if strict_findings and reason is not StopReason.build_failed:
        raise DriverError(
            f"{reason.value} did not run the check gate; only build_failed can "
            "report strict findings"
        )
    if strict_findings:
        return STRICT_FINDINGS_EXIT
    return DONE_EXIT if reason in _ZERO_EXIT else STOPPED_EXIT


def _quoted(value: str, what: str) -> str:
    """One shell argument, refusing anything that cannot be printed on a line.

    :func:`shlex.quote` alone is not enough, which the first spelling of this
    module got wrong. It makes a newline *parse* as part of one argument, but
    the rendered line still spans two physical lines — and it is the printed
    line that an operator copies and that the optimize track renders into a
    prompt a model reads, where the second line reads as an instruction of its
    own.

    C0 and DEL are not the whole class, which the second spelling then got
    wrong. ``str.splitlines`` already reads NEL (U+0085), LINE SEPARATOR
    (U+2028) and PARAGRAPH SEPARATOR (U+2029) as breaks, so those three break
    the line by Python's own definition; RIGHT-TO-LEFT OVERRIDE (U+202E)
    reorders what a reader sees without breaking anything, and ZERO WIDTH SPACE
    (U+200B) hides a token boundary. :meth:`str.isprintable` is the whole class
    in one test — it is False for every Cc, Cf, Cs, Co, Cn and separator except
    the plain space, which is exactly what may not appear here and exactly what
    ``shlex.quote`` then handles.

    :func:`ai_rfc.driver.printable` applies the same predicate and
    **escapes** instead of refusing. Do not unify the two toward escaping: a
    resume line is copied into a terminal and executed, so a mangled-but-
    printed one is worse than none, while a progress line *is* the diagnosis
    and refusing it would lose the stop it describes. Same predicate, opposite
    remedy, because the artifacts differ in what a bad line does.

    Args:
        value: The value to interpolate.
        what: What it is, for the message.

    Returns:
        ``value``, quoted for a shell.

    Raises:
        DriverError: If it holds anything that is not printable.
    """
    if not value.isprintable():
        raise DriverError(
            f"{what} {value!r} holds an unprintable character; a resume line is "
            "copied into a terminal and rendered into a prompt, so it must be "
            "one printable line"
        )
    return shlex.quote(value)


def _checked_cluster_id(cluster_id: str, known: Collection[str] | None) -> str:
    """The id, confirmed to name a cluster the caller's timeline actually has.

    Membership is the guard rather than a filter over characters, following
    ``experiment/per_cluster._checked_cluster_id``. A cluster id originates in
    an agent-written YAML file, and YAML's implicit typing rewrites it before
    anything sees it: ``01`` arrives as ``'1'``, an empty value as ``'None'``,
    a sequence as its repr. Such a value carries no control character and names
    no cluster, so no character filter catches it — while requiring it to be a
    known cluster covers that, a newline forging a second line in the terminal
    or in a progress line a model reads, and a path escape, in one test.

    Args:
        cluster_id: The id to check.
        known: Every cluster id the caller's timeline holds.

    Returns:
        ``cluster_id`` unchanged.

    Raises:
        DriverError: If ``known`` is None, or the id is not one of it.
    """
    if known is None:
        raise DriverError(
            "a resume line naming a cluster needs the timeline's known cluster "
            "ids; this package cannot read the timeline, and a filter over "
            "characters is not a substitute for membership"
        )
    if cluster_id not in known:
        # Repr, not the bare value: this message is itself a line-per-record
        # artifact and a forged id carries a newline.
        raise DriverError(
            f"cluster id {cluster_id!r} is not one of this workspace's "
            "clusters; it would reach a copied command line unescaped"
        )
    return cluster_id


def resume_line(
    reason: StopReason,
    config_path: Path,
    *,
    cluster_id: str | None = None,
    known_clusters: Collection[str] | None = None,
) -> str:
    """The one line an operator types after this stop.

    It is copied into a terminal, and the optimize track renders an equivalent
    progress line into a prompt a model reads, so every value interpolated into
    it is guarded: the cluster id by membership, and both it and the
    configuration path by :func:`_quoted`, which keeps a path holding a space
    as one argument and refuses one that could not be printed on a single line.

    Args:
        reason: Why the sweep stopped.
        config_path: The operator's ``recon.yaml``, as they named it.
        cluster_id: The halted cluster. Required for ``cluster_halted``, which
            resumes with ``--retry <id>``, and refused for every other reason —
            accepting it silently would print a line that resumes the wrong
            work.
        known_clusters: Every cluster id this workspace's timeline holds.
            Required whenever ``cluster_id`` is given.

    Returns:
        A single line, beginning with :data:`PROG`.

    Raises:
        DriverError: If ``reason`` is ``done`` (a finished sweep has nothing to
            resume, and a line telling the operator to run it again is worse
            than none), if ``cluster_id`` is given for another reason or
            missing for ``cluster_halted``, if it names no known cluster, or if
            either it or the path holds a character that is not printable.
    """
    if reason is StopReason.done:
        raise DriverError(
            "a sweep that reached done has nothing to resume; report the "
            "outcome rather than a command"
        )
    if reason is StopReason.cluster_halted and cluster_id is None:
        raise DriverError(
            "cluster_halted resumes with --retry <id>, so it needs the cluster "
            "that halted"
        )
    if reason is not StopReason.cluster_halted and cluster_id is not None:
        raise DriverError(
            f"{reason.value} resumes the whole sweep; a cluster id would be "
            "dropped, and the line would then resume different work than the "
            "caller asked for"
        )
    if cluster_id is None and known_clusters is not None:
        # The mirror of the case above, and refused for the same reason: a
        # caller that handed over the timeline meant to name a cluster, and
        # silently ignoring the set prints a whole-sweep line instead.
        raise DriverError(
            "known_clusters was given without a cluster id; nothing would be "
            f"checked against it, and {reason.value} resumes the whole sweep"
        )
    line = [PROG, _VERB[reason], "--config", _quoted(str(config_path), "the config")]
    if cluster_id is not None:
        checked = _checked_cluster_id(cluster_id, known_clusters)
        # Quoted after the membership check, not instead of it: membership is
        # the guard, but it can only be as clean as the set it was given, and
        # the timeline those ids come from is agent-written too.
        line += ["--retry", _quoted(checked, "the cluster id")]
    return " ".join(line)
