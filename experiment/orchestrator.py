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

from pathlib import Path
from typing import Any, Callable

from .config import Campaign, render_task
from .metrics import cluster_artifacts, window_clusters
from .runner import (
    EVENTS_FILE,
    STDERR_FILE,
    RunSpec,
    build_env,
    build_run_argv,
)
from .spawn import spawn

#: How many times one cluster is attempted before the run halts. A second
#: attempt gets a clean context, which is the plausible cure for a session that
#: wandered; a third would mostly buy repetition of the same failure.
ATTEMPTS_PER_CLUSTER = 2


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


def run_per_cluster(
    campaign: Campaign,
    spec: RunSpec,
    *,
    report: Callable[[str], None] = lambda _: None,
) -> tuple[int | None, bool, int]:
    """Spawn one session per remaining cluster, appending to one transcript.

    Args:
        campaign: The frozen campaign.
        spec: The run being launched; its workspace must already exist.
        report: Where progress lines go.

    Returns:
        ``(exit_code, timed_out, sessions)``. The exit code is the last
        session's, and is non-zero if any cluster was abandoned; ``timed_out``
        is true if any session hit the cap.
    """
    env = build_env(campaign, spec)
    events_path = spec.run_dir / EVENTS_FILE
    stderr_path = spec.run_dir / STDERR_FILE
    sessions = 0
    exit_code: int | None = 0
    any_timeout = False

    while True:
        row = next_cluster(spec.workspace)
        if row is None:
            report(f"{spec.run_id}: window complete after {sessions} session(s)")
            return exit_code, any_timeout, sessions

        ordinal = row["ordinal"]
        # A one-cluster window through the prompt the whole-window runs use, so
        # there is no second task prompt to drift from the first.
        task = render_task((ordinal, ordinal))
        for attempt in range(1, ATTEMPTS_PER_CLUSTER + 1):
            report(
                f"{spec.run_id}: cluster {ordinal} ({row['id']}), " f"attempt {attempt}"
            )
            argv = build_run_argv(campaign, spec, task=task)
            exit_code, timed_out = spawn(
                argv,
                cwd=spec.workspace,
                env=env,
                events_path=events_path,
                stderr_path=stderr_path,
                timeout_s=campaign.timeout_s,
                append=sessions > 0,
            )
            sessions += 1
            any_timeout = any_timeout or timed_out
            if cluster_artifacts(spec.workspace, row)["artifacts"]:
                break
        else:
            # Never skip and continue. Later clusters' prose builds on earlier
            # prose, and a draft with a hole in it is worse than a short one.
            report(
                f"{spec.run_id}: cluster {ordinal} did not finish in "
                f"{ATTEMPTS_PER_CLUSTER} attempt(s); halting"
            )
            return exit_code or 1, any_timeout, sessions
