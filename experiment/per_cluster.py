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
import time
from pathlib import Path
from typing import Any, Callable

from .arms import arm_profile
from .config import Campaign, render_task
from .metrics import cluster_artifacts, window_clusters
from .runner import EVENTS_FILE, STDERR_FILE, RunRef, build_env, prepare_run_argv
from .spawn import spawn
from .stream import (
    ai_rfc_connected,
    init_event,
    mcp_servers,
    result_events,
    salvage_stream,
)

#: How many times one cluster is attempted before the run halts. A second
#: attempt gets a clean context, which is the plausible cure for a session that
#: wandered; a third would mostly buy repetition of the same failure.
ATTEMPTS_PER_CLUSTER = 2

#: One line per session: which cluster, what it cost, and the argv it ran.
#: ``argv.json`` holds the whole-window vector built before dispatch, which is
#: not what any session executed in this mode.
SESSIONS_FILE = "sessions.jsonl"


def next_cluster(workspace: Path) -> dict[str, Any] | None:
    """The lowest-ordinal in-window cluster that is not finished.

    Read from the workspace rather than counted in memory, so a resumed or
    re-entered run agrees with what is actually on disk.

    Args:
        workspace: The run's workspace.

    Returns:
        The cluster row to process next, or ``None`` when the window is done.
    """
    for row in window_clusters(workspace):
        artifacts = cluster_artifacts(workspace, row)
        if artifacts["pre_seeded"]:
            continue
        if not artifacts["artifacts"]:
            return row
    return None


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
    if not artifacts.get("checkpoint"):
        return None
    if not artifacts.get("revision_tag"):
        return "checkpoint present, no revision entry"
    if not artifacts.get("tag_exists"):
        return "checkpoint present, revision entry recorded, tag missing"
    return None


def surface_shortfall(arm: str, events_path: Path) -> str | None:
    """What the arm declared it would mount, against what the session did.

    An arm states its surface as data and every session announces what actually
    mounted, and nothing joined the two — so a server that failed to start gave
    a session carrying the arm's name and none of its tools. That is not a
    weaker arm, it is a different one: every write the substrate validates goes
    through those tools, so without them a session writes unchecked. It still
    exits 0, which is why one produced thirty-nine claims in a vocabulary the
    schema rejects and reported success.

    Args:
        arm: The arm the run declares.
        events_path: The run's transcript.

    Returns:
        A description of what mounted instead, or None when the surface is
        whole — including when the arm mounts no server, and when the session
        never got far enough to say.
    """
    if not arm_profile(arm).uses_mcp:
        return None
    try:
        events, _ = salvage_stream(events_path.read_text(errors="replace"))
    except OSError:
        return None
    if init_event(events) is None:
        return None
    if ai_rfc_connected(events):
        return None
    mounted = mcp_servers(events)
    return ", ".join(f"{n}={s}" for n, s in sorted(mounted.items())) or "no server"


def _session_cost(events_path: Path, seen: int) -> tuple[float, int, int]:
    """What was spent by the result events appended since ``seen``.

    Read back off the transcript rather than tracked, for the same reason the
    next cluster is: the transcript is what survives, and a figure carried in
    memory would be lost by the kill it exists to tolerate.

    Counting from ``seen`` rather than taking the last event matters on exactly
    the path this design exists for. A session killed on its cap emits no result
    event, so the tail of the transcript is still the *previous* session's — and
    charging that a second time both overstates the run and writes the wrong
    figure into the per-session record.

    Args:
        events_path: The run's transcript.
        seen: How many result events the transcript held before this session.

    The transcript is salvaged rather than parsed strictly. A kill can truncate
    a line mid-write and the next session appends onto that tail, so one
    unparseable line is a normal outcome of the very interruptions this loop
    exists to survive. Refusing the whole file there would freeze ``spent``, and
    a frozen ``spent`` is a budget ceiling that can never be reached again.

    Args:
        events_path: The run's transcript.
        seen: How many result events the transcript held before this session.

    Returns:
        ``(cost, total, skipped)`` — what the new events report, the
        transcript's new result-event count, and how many of its lines could not
        be read. A session that produced none reports 0.0, because it said
        nothing about its own spend.
    """
    try:
        events, skipped = salvage_stream(events_path.read_text(errors="replace"))
    except OSError:
        return 0.0, seen, 0
    results = result_events(events)
    cost = 0.0
    for result in results[seen:]:
        value = result.get("total_cost_usd")
        if isinstance(value, (int, float)):
            cost += float(value)
    return cost, len(results), skipped


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
            :func:`driver.execute` does: a sweep of sixty-nine clusters runs for
            hours, and the caller that discards these lines leaves an operator
            unable to tell a working run from a stalled one.

    Returns:
        ``(exit_code, timed_out, sessions)``. The exit code is the last
        session's, and is non-zero if any cluster was abandoned or a cap was
        reached with work outstanding; ``timed_out`` is true if any session hit
        its cap.
    """
    env = build_env(campaign, ref)
    events_path = ref.run_dir / EVENTS_FILE
    stderr_path = ref.run_dir / STDERR_FILE
    sessions_path = ref.run_dir / SESSIONS_FILE
    sessions = 0
    spent = 0.0
    results_seen = 0
    reported_damage = 0
    started = time.monotonic()
    exit_code: int | None = 0
    any_timeout = False

    while True:
        row = next_cluster(ref.workspace)
        if row is None:
            report(f"{ref.run_id}: window complete after {sessions} session(s)")
            return exit_code, any_timeout, sessions

        budget_left = campaign.budget_usd - spent
        time_left = campaign.timeout_s - (time.monotonic() - started)
        if budget_left <= 0 or time_left <= 0:
            reached = "budget" if budget_left <= 0 else "wall clock"
            report(
                f"{ref.run_id}: {reached} exhausted after {sessions} session(s) "
                f"(${spent:.2f}); {row['ordinal']} and later not attempted"
            )
            return exit_code or 1, any_timeout, sessions

        ordinal = row["ordinal"]
        outstanding = partial_reason(cluster_artifacts(ref.workspace, row))
        if outstanding is not None:
            report(f"{ref.run_id}: cluster {ordinal} is half finished ({outstanding})")
        # A one-cluster window through the prompt the whole-window runs use, so
        # there is no second task prompt to drift from the first.
        task = render_task((ordinal, ordinal))
        for attempt in range(1, ATTEMPTS_PER_CLUSTER + 1):
            report(
                f"{ref.run_id}: cluster {ordinal} ({row['id']}), attempt "
                f"{attempt}, ${budget_left:.2f} left"
            )
            argv = prepare_run_argv(campaign, ref, task=task, budget_usd=budget_left)
            exit_code, timed_out = spawn(
                argv,
                cwd=ref.workspace,
                env=env,
                events_path=events_path,
                stderr_path=stderr_path,
                timeout_s=int(time_left),
                append=sessions > 0,
            )
            sessions += 1
            any_timeout = any_timeout or timed_out
            cost, results_seen, damaged = _session_cost(events_path, results_seen)
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
                            "attempt": attempt,
                            "exit_code": exit_code,
                            "timed_out": timed_out,
                            "cost_usd": cost,
                            "cumulative_cost_usd": spent,
                            "budget_given_usd": budget_left,
                            "argv": argv,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            # Checked once, on the first session, because it is a property of
            # how the run was launched rather than of any cluster: whatever the
            # first session mounted, the rest will mount too. Retrying a session
            # that had no tools only buys a second session with no tools.
            shortfall = surface_shortfall(ref.arm, events_path)
            if sessions == 1 and shortfall is not None:
                report(
                    f"{ref.run_id}: arm {ref.arm} declares the ai_rfc tool "
                    f"surface and the session mounted {shortfall}; stopping "
                    f"after ${spent:.2f} rather than spending the window on "
                    f"sessions that cannot checkpoint, gate or tag"
                )
                return 1, any_timeout, sessions
            if cluster_artifacts(ref.workspace, row)["artifacts"]:
                break
            budget_left = campaign.budget_usd - spent
        else:
            # Never skip and continue. Later clusters' prose builds on earlier
            # prose, and a draft with a hole in it is worse than a short one.
            report(
                f"{ref.run_id}: cluster {ordinal} did not finish in "
                f"{ATTEMPTS_PER_CLUSTER} attempt(s); halting"
            )
            return exit_code or 1, any_timeout, sessions
