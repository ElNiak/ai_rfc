"""Drive a run as one agent session per cluster.

The pilot ran a whole window in a single session. Over ten clusters that is
fine; over sixty-nine it is not, for two reasons that compound. The session
compacts repeatedly, so late clusters are reasoned about from a summary of the
evidence rather than the evidence. And a budget or wall-clock kill loses
everything after it, because a run is never relaunched in place.

Spawning per cluster fixes both. Each session starts on a clean context with
the same instructions, and the window it is given is one cluster wide — the
task prompt is already parameterised by window, so no second prompt exists to
drift from the first. Progress is durable between sessions because it is the
workspace: checkpoints, revisions and tags are on disk, and the next cluster is
derived from them rather than remembered.

The run still produces one status record, one transcript and one merged result,
so the audit, the metrics and the report cannot tell how it was executed.
"""

from __future__ import annotations

import json
import string
import time
from pathlib import Path
from typing import Any, Callable

from ai_rfc.driver import DriverError
from ai_rfc.driver.arms import arm_profile
from ai_rfc.driver.consolidation import Due, consolidation_due
from ai_rfc.driver.session import SessionResult, run_session
from ai_rfc.driver.stream import ai_rfc_connected, init_event, mcp_servers

from .. import ledger
from ..draft.gate import _cluster_ordinals, load_revisions
from . import ExperimentError
from .config import Campaign, render_task
from .metrics import cluster_artifacts
from .progress import _bar, _duration, cluster_span, describe, digest, window_progress
from .runner import RunRef, session_spec
from .summary import (
    build_summary,
    held_claim_ids,
    new_questions,
    question_ids,
    seed_seen,
    write_summary,
)

#: How many times one cluster is attempted before the run halts. A second
#: attempt gets a clean context, which is the plausible cure for a session that
#: wandered; a third would mostly buy repetition of the same failure.
ATTEMPTS_PER_CLUSTER = 2

#: One line per session: which cluster, what it cost, and the argv it ran.
#: ``argv.json`` holds the whole-window vector built before dispatch, which is
#: not what any session executed in this mode.
SESSIONS_FILE = "sessions.jsonl"


def partial_reason(artifacts: dict[str, Any]) -> str | None:
    """Name a half-finished cluster's state, or None when it is untouched.

    A checkpoint is written once and :func:`draft.checkpoint.write_checkpoint`
    raises when its directory already exists, so a cluster abandoned between
    the checkpoint and its tag cannot simply be redone: the retry may spend a
    whole session rediscovering that. Whether the session recovers is the
    agent's business, but the operator should not have to infer the state from
    two silent attempts.

    Args:
        artifacts: One row from :func:`metrics.cluster_artifacts`.

    Returns:
        A short description of what is already on disk, or None.
    """
    return ledger.partial_reason(
        bool(artifacts.get("checkpoint")),
        artifacts.get("revision_tag"),
        bool(artifacts.get("tag_exists")),
    )


def surface_shortfall(
    arm: str, events: list[dict[str, Any]]
) -> tuple[bool, str | None]:
    """What the arm declared it would mount, against what the session did.

    An arm states its surface as data and every session announces what actually
    mounted, and nothing joined the two — so a server that failed to start gave
    a session carrying the arm's name and none of its tools. That is not a
    weaker arm, it is a different one: every write the substrate validates goes
    through those tools, so without them a session writes unchecked. It still
    exits 0, which is why one produced thirty-nine claims in a vocabulary the
    schema rejects and reported success.

    "Whole" and "cannot tell yet" are returned as separate facts rather than
    both as None. A caller that judges once needs to know whether a verdict was
    actually reached, or it will treat a silent session as a clean one and
    never look again.

    Args:
        arm: The arm the run declares.
        events: The session's events, already salvaged.

    Returns:
        ``(judged, shortfall)``. ``judged`` is False when nothing could be
        decided — an empty transcript, or one whose session has not yet
        announced what it mounted. ``shortfall`` describes what mounted instead,
        and is None when the surface is whole or the arm mounts no server.
    """
    if not arm_profile(arm).uses_mcp:
        return True, None
    if init_event(events) is None:
        return False, None
    if ai_rfc_connected(events):
        return True, None
    mounted = mcp_servers(events)
    return (
        True,
        ", ".join(f"{n}={s}" for n, s in sorted(mounted.items())) or "no server",
    )


def _finish_cluster(
    campaign: Campaign,
    ref: RunRef,
    row: dict[str, Any],
    *,
    outcome: str,
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    seen_claim_ids: frozenset[str],
    wall_s: float,
    seed_error: str | None,
    questions_before: set[str],
    report: Callable[[str], None],
) -> frozenset[str]:
    """Write one cluster's summary and print its digest.

    Wrapped whole: this is reporting, and a defect in it must not end a run
    that may already be hours old.

    Args:
        campaign: The frozen campaign.
        ref: The run being executed.
        row: The timeline row just processed.
        outcome: How the cluster ended.
        attempts: One entry per attempt made on it.
        events: The run transcript, already salvaged.
        seen_claim_ids: Every claim id held before this cluster.
        wall_s: Seconds spent on this cluster.
        seed_error: A failure from the resume rebuild, or None.
        questions_before: The question ids present before the cluster ran.
        report: Where the digest goes.

    Returns:
        ``seen_claim_ids`` extended with what this cluster holds — from both the
        success and the failure path, since a summary that failed after the
        checkpoint was read must not cost the claims it already found.
    """
    # Read before the try, and returned from both branches: the claim delta is
    # cumulative, so a cluster whose summary failed after its checkpoint was
    # read must still contribute what it held. Dropping it would credit those
    # claims again at the next cluster — the second definition of "new claim"
    # this design exists to avoid.
    held: frozenset[str] = frozenset()
    try:
        errors = [seed_error] if seed_error else []
        held, held_error = held_claim_ids(campaign, ref.workspace, str(row.get("id")))
        if held_error:
            errors.append(held_error)
        record = build_summary(
            campaign,
            ref,
            row,
            outcome=outcome,
            attempts=attempts,
            events=events,
            sessions=[a["session_id"] for a in attempts if a.get("session_id")],
            seen_claim_ids=seen_claim_ids,
            held_ids=held,
            wall_s=wall_s,
            errors=errors,
        )
        record["questions"] = new_questions(ref.workspace, questions_before)
        write_summary(ref.run_dir, str(row.get("id")), record)
        for line in digest(record):
            report(f"{ref.run_id}: {line}")
        return seen_claim_ids | held
    except Exception as error:  # noqa: BLE001 - reporting may not end a run
        report(f"{ref.run_id}: summary unavailable: {error}")
        return seen_claim_ids | held


def _checked_cluster_id(workspace: Path, cluster_id: str) -> str:
    """The id, confirmed to name a cluster this run's timeline actually has.

    Membership is the guard rather than a filter over characters. The id is
    read back out of an agent-written ``revisions.yaml``, and YAML's implicit
    typing rewrites it before anything sees it: ``01`` arrives as ``'1'``, an
    empty value as ``'None'``, a sequence as its repr, a block scalar as a
    string carrying a real newline. A value can therefore be free of control
    characters and still name nothing, which no character filter catches.
    Requiring it to be a known cluster covers that, a newline forging structure
    in the session prompt or in a progress line, and a ``../..`` or an absolute
    path reaching a checkpoint path, in one test.

    The whole timeline, not the run's window: a pre-seeded baseline is copied
    into the workspace whole, so its last unconsolidated revision can name a
    cluster below the window's first ordinal and still be perfectly legitimate.
    The set is the gate's own, so the driver and the gate cannot disagree about
    which clusters a run knows.

    Args:
        workspace: The run's workspace.
        cluster_id: The id to check.

    Returns:
        ``cluster_id`` unchanged.

    Raises:
        ExperimentError: If it is not a cluster of this workspace's timeline.
    """
    if cluster_id not in _cluster_ordinals(workspace / "timeline"):
        # Repr, not the bare value: the message is itself a line-per-record
        # artifact, and a forged id carries a newline.
        raise ExperimentError(
            f"cluster id {cluster_id!r} is not a cluster of {workspace}'s "
            "timeline; it would reach a session prompt and a checkpoint path "
            "unescaped"
        )
    return cluster_id


def _consolidations_recorded(workspace: Path) -> int:
    """How many consolidation revisions the workspace records.

    Read through the gate's own loader so the driver and the gate cannot
    disagree about what a revision is.

    Args:
        workspace: The run's workspace.

    Returns:
        The number of entries carrying ``kind: consolidation``, and 0 when the
        map is missing or will not load. The count is evidence a round left
        behind, so anything standing between the caller and that evidence is
        an absence of proof and never a reason to raise inside a sweep.
    """
    try:
        entries = load_revisions(workspace / "revisions.yaml")
    except Exception:  # noqa: BLE001 - no proof of a revision is not one
        return 0
    return sum(1 for entry in entries if entry.kind == "consolidation")


def _run_consolidation(
    campaign: Campaign,
    ref: RunRef,
    due: Due,
    *,
    budget_usd: float,
    timeout_s: int,
    seen: int,
    at_end: bool,
    report: Callable[[str], None],
) -> tuple[bool, SessionResult]:
    """Run one consolidation round.

    Args:
        campaign: The frozen campaign.
        ref: The run being swept.
        due: What :func:`consolidation.consolidation_due` decided.
        budget_usd: Budget remaining for this session.
        timeout_s: Seconds remaining.
        seen: How many result events the run's transcript already held. A
            consolidation appends to the same transcript every cluster round
            does, so a round told nothing about what preceded it charges this
            session for every session before it.
        at_end: True for the sweep's final consolidation.
        report: Progress sink.

    Returns:
        ``(recorded, result)``. ``recorded`` is True when the round recorded
        its revision, re-derived from disk rather than from the session's exit
        code — an agent can exit 0 having done nothing. The whole
        :class:`~ai_rfc.driver.session.SessionResult` is returned rather than
        only its ``timed_out`` because a consolidation spends and announces an
        id like any round: the caller folds its cost into the run's, claims its
        session id so the next cluster does not, and reads its ``timed_out``
        into the run's status record — a consolidation killed on the cap would
        otherwise be filed as a run that finished on its own.

    Raises:
        ExperimentError: If the base cluster names no cluster of this run's
            timeline, or the campaign froze no consolidation task template.
    """
    # Checked before it is reported, not after. The report sink is not only an
    # operator log: the optimizer passes its own, and those lines become the
    # feedback text a proposer model reads, so an unchecked id here is a prompt
    # injection and not merely a broken line.
    base = _checked_cluster_id(ref.workspace, due.base_cluster)
    report(
        f"{ref.run_id}: consolidation {due.ordinal:02d} due ({due.reason}), "
        f"base {base}"
    )
    if not campaign.consolidation_task_template.exists():
        raise ExperimentError(
            f"{campaign.consolidation_task_template} is missing; this campaign "
            "was frozen before consolidation rounds existed — initialise a new "
            "campaign"
        )
    # Two passes because render_task fills only the window, and the frozen
    # consolidation template also names the round and its base — per-round
    # values no campaign can freeze. The second pass cannot be forged by the
    # first, whose only substitutions are window bounds and therefore integers,
    # and safe_substitute never re-parses what it substituted, so the checked
    # id lands verbatim.
    task = string.Template(
        render_task(
            (due.ordinal, due.ordinal),
            template=campaign.consolidation_task_template,
            profile="consolidation",
        )
    ).safe_substitute(ordinal=due.ordinal, base=base)
    result = run_session(
        session_spec(
            campaign,
            ref,
            task=task,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            prompt_file=campaign.prompts_dir / f"consolidation-{ref.arm}.md",
            append=True,
        ),
        ref.run_dir,
        seen=seen,
    )
    # Counted, not inferred from the schedule falling silent. consolidation_due
    # answers None for a revisions map its loader refuses as well as for one
    # with nothing outstanding, so a round that destroyed that map would be
    # credited by its own damage. The ordinal is the round this session owed,
    # which is one more than the map held before it: at least that many is the
    # evidence it recorded, and more is an agent recording generously.
    if _consolidations_recorded(ref.workspace) >= due.ordinal:
        report(f"{ref.run_id}: consolidation {due.ordinal:02d} recorded")
        return True, result
    report(
        f"{ref.run_id}: consolidation {due.ordinal:02d} recorded no revision"
        + ("" if at_end else "; continuing the sweep")
    )
    return False, result


def run_per_cluster(
    campaign: Campaign,
    ref: RunRef,
    *,
    report: Callable[[str], None] = print,
) -> tuple[int | None, bool, int]:
    """Spawn one session per remaining cluster, appending to one transcript.

    ``campaign.budget_usd`` and ``campaign.timeout_s`` cap the **run**, not a
    session. Each session is given what the run has left, so the totals hold
    however many sessions there turn out to be — without that, a per-cluster run
    of sixty-nine clusters could spend sixty-nine times the flag, which is the
    opposite of what a budget is for.

    Args:
        campaign: The frozen campaign.
        ref: The run being launched; its workspace must already exist.
        report: Where progress lines go. Defaults to printing, as
            :func:`campaign_runs.launch_pending` does: a sweep of sixty-nine clusters
            runs for hours, and the caller that discards these lines leaves an
            operator unable to tell a working run from a stalled one.

    Returns:
        ``(exit_code, timed_out, sessions)``. The exit code is the last
        session's, and is non-zero if any cluster was abandoned or a cap was
        reached with work outstanding; ``timed_out`` is true if any session hit
        its cap.
    """
    sessions_path = ref.run_dir / SESSIONS_FILE
    sessions = 0
    spent = 0.0
    results_seen = 0
    reported_damage = 0
    surface_judged = False
    known_sessions: set[str] = set()
    # Ordinals of the mid-sweep consolidations already attempted in this
    # process. A round that recorded nothing stays due after every later
    # cluster round, so without this the sweep pays for it once per cluster
    # from the first failure onward. The ordinal alone is the key: it holds at
    # consolidations + 1 until a round actually records, whereas the base
    # cluster is re-derived as the newest cluster revision and therefore moves
    # under a failed round, deduplicating nothing. In memory only — D50's
    # "derived, never recorded" is about disk, and a resumed sweep is entitled
    # to try again.
    attempted: set[int] = set()
    seen_claim_ids, seed_error = seed_seen(campaign, ref.workspace)
    started = time.monotonic()
    exit_code: int | None = 0
    any_timeout = False

    while True:
        row, artifacts, position, done, total = window_progress(ref.workspace)
        # Above the end-of-sweep branch, not below it: the final consolidation
        # is a session like any other, and reading what the run has left after
        # the branch that launches it would leave it uncapped.
        budget_left = campaign.budget_usd - spent
        time_left = campaign.timeout_s - (time.monotonic() - started)
        if row is None:
            if ref.arm == "C":
                report(
                    f"{ref.run_id}: arm C does not consolidate "
                    f"(its tool surface is frozen)"
                )
            elif (
                due := consolidation_due(
                    ref.workspace, campaign.consolidate_every, at_end=True
                )
            ) is not None:
                if budget_left <= 0 or time_left <= 0:
                    reached = "budget" if budget_left <= 0 else "wall clock"
                    report(
                        f"{ref.run_id}: {reached} exhausted after {sessions} "
                        f"session(s) (${spent:.2f}); the final consolidation "
                        f"was not attempted"
                    )
                    exit_code = exit_code or 1
                else:
                    # The final round is neither filtered through `attempted`
                    # nor added to it. Keyed on the ordinal, it shares its key
                    # with the mid-sweep round that failed — but it is the
                    # sweep's deliverable and its failure is what the exit code
                    # is for, so it is owed one attempt regardless.
                    try:
                        recorded, round_result = _run_consolidation(
                            campaign,
                            ref,
                            due,
                            budget_usd=budget_left,
                            timeout_s=int(time_left),
                            seen=results_seen,
                            at_end=True,
                            report=report,
                        )
                    except (ExperimentError, DriverError) as error:
                        report(f"{ref.run_id}: final consolidation not run: {error}")
                        exit_code = 1
                    else:
                        sessions += 1
                        any_timeout = any_timeout or round_result.timed_out
                        # Claiming the id is what keeps it out of the next
                        # cluster's record: a cluster round attributes every
                        # transcript id it has not seen before to itself.
                        known_sessions.update(round_result.session_ids)
                        spent += round_result.cost_usd
                        results_seen = round_result.results_seen
                        if not recorded:
                            exit_code = 1
            else:
                # None is two answers here, and only one of them is a finished
                # sweep. `run --task consolidation` reports the other and exits
                # 1; this branch is the one place D52 makes the final round
                # fatal, so it must not file an unreadable workspace as a run
                # that had nothing left to do. Asked again rather than asked
                # differently: consolidation_due's None is deliberate and
                # tested, and widening it would schedule an editorial pass over
                # a map the gate is the one to report on.
                revisions = ref.workspace / "revisions.yaml"
                if revisions.is_file():
                    try:
                        load_revisions(revisions)
                    except Exception as error:  # noqa: BLE001 - never fatal here
                        report(
                            f"{ref.run_id}: final consolidation not run: "
                            f"{revisions} is unreadable ({error})"
                        )
                        exit_code = 1
            report(f"{ref.run_id}: window complete after {sessions} session(s)")
            return exit_code, any_timeout, sessions

        # Guarded on the same cap the cluster round is, because a consolidation
        # costs money like any round. It sits above that guard rather than
        # below it so one guard covers both: a round that recorded nothing
        # falls through to the cluster work it was scheduled ahead of, and the
        # figures the cluster round is then launched from — and refused on —
        # must already include what the editorial pass spent.
        if ref.arm != "C" and budget_left > 0 and time_left > 0:
            due = consolidation_due(ref.workspace, campaign.consolidate_every)
            if due is not None and due.ordinal not in attempted:
                attempted.add(due.ordinal)
                try:
                    recorded, round_result = _run_consolidation(
                        campaign,
                        ref,
                        due,
                        budget_usd=budget_left,
                        timeout_s=int(time_left),
                        seen=results_seen,
                        at_end=False,
                        report=report,
                    )
                except (ExperimentError, DriverError) as error:
                    # Reported, never raised. launch_pending puts no guard
                    # around launch() and refuses to resume a run directory
                    # holding no status record, so an escape here would abort
                    # the campaign's remaining runs and leave this one needing
                    # an operator to move the directory aside by hand.
                    report(
                        f"{ref.run_id}: consolidation {due.ordinal:02d} "
                        f"not run: {error}"
                    )
                else:
                    sessions += 1
                    any_timeout = any_timeout or round_result.timed_out
                    # Claiming the id is what keeps it out of the next
                    # cluster's record: a cluster round attributes every
                    # transcript id it has not seen before to itself, and until
                    # consolidations existed there was no other kind of session
                    # for that rule to misattribute.
                    known_sessions.update(round_result.session_ids)
                    spent += round_result.cost_usd
                    results_seen = round_result.results_seen
                    budget_left = campaign.budget_usd - spent
                    time_left = campaign.timeout_s - (time.monotonic() - started)
                    if recorded:
                        # Only when the round changed the disk. The next pass
                        # re-reads progress, so the consolidation is accounted
                        # for before a cluster round is chosen; a round that
                        # recorded nothing left that disk as it was, and falls
                        # through to the cluster work it was scheduled ahead
                        # of.
                        continue

        if budget_left <= 0 or time_left <= 0:
            reached = "budget" if budget_left <= 0 else "wall clock"
            report(
                f"{ref.run_id}: {reached} exhausted after {sessions} session(s) "
                f"(${spent:.2f}); cluster {position} of {total} "
                f"(ordinal {row['ordinal']}) and later not attempted"
            )
            return exit_code or 1, any_timeout, sessions

        ordinal = row["ordinal"]
        cluster_attempts: list[dict[str, Any]] = []
        cluster_started = time.monotonic()
        events: list[dict[str, Any]] = []
        # Guarded at the call site as well as inside: this is the one summary
        # call outside _finish_cluster's try, so an escape here would reach the
        # loop and end a run that has nothing else wrong with it.
        try:
            questions_before = question_ids(ref.workspace)
        except Exception as error:  # noqa: BLE001 - reporting may not end a run
            report(f"{ref.run_id}: questions unreadable: {error}")
            questions_before = set()
        outstanding = partial_reason(artifacts)
        if outstanding is not None:
            report(
                f"{ref.run_id}: cluster {position} of {total} "
                f"(ordinal {ordinal}) is half finished ({outstanding})"
            )
        # Once per cluster, not once per attempt: the row does not change
        # between attempts, and a contradicting timeline would otherwise say so
        # twice.
        try:
            span = cluster_span(ref.workspace, row)
        except ExperimentError as error:
            # Loud, but never fatal: two files on disk disagreeing is worth
            # saying, and is not worth killing a run of many hours over.
            report(f"{ref.run_id}: {error}")
            span = None
        # A one-cluster window through the prompt the whole-window runs use, so
        # there is no second task prompt to drift from the first. Rendered only
        # from the campaign's own frozen copy: falling back to the live source
        # template would be exactly the drift this mode exists to remove.
        if not campaign.task_template.exists():
            raise ExperimentError(
                f"{campaign.task_template} is missing; this campaign was frozen "
                "before task templates were frozen — initialise a new campaign"
            )
        template = campaign.task_template
        task = render_task((ordinal, ordinal), template=template)
        for attempt in range(1, ATTEMPTS_PER_CLUSTER + 1):
            report(
                f"{ref.run_id}: {_bar(done, total)} starting cluster "
                f"{position} of {total} (ordinal {ordinal}), "
                f"attempt {attempt} of {ATTEMPTS_PER_CLUSTER}"
            )
            report(f"{ref.run_id}:   {describe(row, span)}")
            report(
                f"{ref.run_id}:   ${budget_left:.2f} left of "
                f"${campaign.budget_usd:.2f} - "
                f"{_duration(time.monotonic() - started)} elapsed of "
                f"{_duration(campaign.timeout_s)} cap"
            )
            attempt_started = time.monotonic()
            result = run_session(
                session_spec(
                    campaign,
                    ref,
                    task=task,
                    budget_usd=budget_left,
                    timeout_s=int(time_left),
                    append=sessions > 0,
                ),
                ref.run_dir,
                seen=results_seen,
            )
            argv = list(result.argv)
            exit_code, timed_out = result.exit_code, result.timed_out
            sessions += 1
            any_timeout = any_timeout or timed_out
            events, damaged = list(result.events), result.damaged
            attempt_sessions = [
                session
                for session in result.session_ids
                if session not in known_sessions
            ]
            known_sessions.update(attempt_sessions)
            cost, results_seen = result.cost_usd, result.results_seen
            cluster_attempts.append(
                {
                    "attempt": attempt,
                    "session": sessions,
                    "session_id": attempt_sessions[0] if attempt_sessions else None,
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "cost_usd": cost,
                    "wall_s": round(time.monotonic() - attempt_started, 1),
                }
            )
            spent += cost
            if damaged > reported_damage:
                # Said once per new loss rather than per session: the count only
                # grows, and a ceiling enforced over a partial record is a fact
                # the operator has to know to read the final figure.
                report(
                    f"{ref.run_id}: {damaged} transcript line(s) unreadable; "
                    f"spend is counted from what parsed, so ${spent:.2f} is a "
                    f"floor and the budget ceiling is enforced against it"
                )
                reported_damage = damaged
            # The run's argv.json holds the whole-window vector launch() built
            # before dispatching here, which is not what any session ran. Each
            # session's own is recorded so the run says what it actually did.
            with sessions_path.open("a") as handle:
                handle.write(
                    json.dumps(
                        {
                            "session": sessions,
                            "cluster_id": row["id"],
                            "ordinal": ordinal,
                            "task_template": str(template),
                            "attempt": attempt,
                            "exit_code": exit_code,
                            "timed_out": timed_out,
                            "cost_usd": cost,
                            "cumulative_cost_usd": spent,
                            "budget_given_usd": budget_left,
                            # Nullable: a session killed before its init event
                            # never announced an id, so the row can only say so.
                            "session_id": (
                                attempt_sessions[0] if attempt_sessions else None
                            ),
                            "argv": argv,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            # Judged once, but on the first session that can be judged rather
            # than on the first session outright. The surface is a property of
            # how the run was launched, so one verdict settles the window — but
            # a session that produced no readable transcript, or none with an
            # init event, has not said what it mounted. Gating on the session
            # number would spend that silence: the check would be skipped and
            # could never fire again, leaving the rest of the window unguarded.
            if not surface_judged:
                surface_judged, shortfall = surface_shortfall(ref.arm, events)
                if shortfall is not None:
                    report(
                        f"{ref.run_id}: arm {ref.arm} declares the ai_rfc tool "
                        f"surface and the session mounted {shortfall}; stopping "
                        f"after ${spent:.2f} rather than spending the window on "
                        f"sessions that cannot checkpoint, gate or tag"
                    )
                    _finish_cluster(
                        campaign,
                        ref,
                        row,
                        outcome="surface_shortfall",
                        attempts=cluster_attempts,
                        events=events,
                        seen_claim_ids=seen_claim_ids,
                        wall_s=time.monotonic() - cluster_started,
                        seed_error=seed_error,
                        questions_before=questions_before,
                        report=report,
                    )
                    return 1, any_timeout, sessions
            if cluster_artifacts(ref.workspace, row)["artifacts"]:
                seen_claim_ids = _finish_cluster(
                    campaign,
                    ref,
                    row,
                    outcome="complete",
                    attempts=cluster_attempts,
                    events=events,
                    seen_claim_ids=seen_claim_ids,
                    wall_s=time.monotonic() - cluster_started,
                    seed_error=seed_error,
                    questions_before=questions_before,
                    report=report,
                )
                # Said once, on the first cluster to report: the rebuild either
                # failed for the whole run or not at all.
                seed_error = None
                break
            budget_left = campaign.budget_usd - spent
        else:
            # Never skip and continue. Later clusters' prose builds on earlier
            # prose, and a draft with a hole in it is worse than a short one.
            report(
                f"{ref.run_id}: cluster {ordinal} did not finish in "
                f"{ATTEMPTS_PER_CLUSTER} attempt(s); halting"
            )
            _finish_cluster(
                campaign,
                ref,
                row,
                outcome="attempts_exhausted",
                attempts=cluster_attempts,
                events=events,
                seen_claim_ids=seen_claim_ids,
                wall_s=time.monotonic() - cluster_started,
                seed_error=seed_error,
                questions_before=questions_before,
                report=report,
            )
            return exit_code or 1, any_timeout, sessions
