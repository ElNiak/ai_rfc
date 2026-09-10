"""Launch one hermetic ``claude -p`` run and capture everything it emits.

The process gets a minimal environment, its stdout streams straight into
``events.jsonl`` as it arrives, a wall-clock cap is enforced on the whole
process group (the MCP server is a child), and ``status.json`` is written
exactly once — a run is never relaunched in place.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_rfc.driver.arms import arm_profile
from ai_rfc.driver.session import (
    EVENTS_FILE,
    GUARD_FILE,
    SessionSpec,
    prepare_argv,
    run_session,
    session_env,
)
from ai_rfc.driver.stream import merge_results, result_events, salvage_stream

from . import ExperimentError
from .config import TASK_TEMPLATE_FILE, Campaign

RESULT_FILE = "result.json"
STATUS_FILE = "status.json"
ARGV_FILE = "argv.json"
ENV_FILE = "env.json"
PROMPT_FILE = "prompt.md"


@dataclass(frozen=True)
class RunRef:
    """One run's identity and directory."""

    run_id: str
    arm: str
    repeat: int
    run_dir: Path

    @property
    def workspace(self) -> Path:
        """The run's private copy of the pristine workspace."""
        return self.run_dir / "workspace"


@dataclass(frozen=True)
class RunStatus:
    """What happened to one launch; written once as ``status.json``."""

    run_id: str
    arm: str
    repeat: int
    started_at: str
    finished_at: str
    exit_code: int | None
    timed_out: bool
    budget_hit: bool
    claude_version: str
    guard_sha256: str = ""

    @property
    def complete(self) -> bool:
        """The process ended on its own (any exit code); timeouts are not complete."""
        return not self.timed_out and self.exit_code is not None


def run_ref(campaign: Campaign, run_id: str) -> RunRef:
    """Resolve a run id from the campaign's frozen order.

    Args:
        campaign: The frozen campaign.
        run_id: An id from its run order.

    Returns:
        The run's identity and directory.
    """
    arm, repeat = campaign.split_run_id(run_id)
    return RunRef(run_id, arm, repeat, campaign.runs_dir / run_id)


def session_spec(
    campaign: Campaign,
    ref: RunRef,
    *,
    task: str | None = None,
    budget_usd: float | None = None,
    timeout_s: int | None = None,
    prompt_file: Path | None = None,
    append: bool = False,
) -> SessionSpec:
    """Describe one of this run's sessions in the driver's own terms.

    The campaign-shaped defaults live here rather than in
    :mod:`ai_rfc.driver.session`: a driver that reached into a campaign's
    directory layout for its task, its arm prompt or its budget would be
    importing the experiment's shape without importing its code. Every session
    of every mode is described through this one function, so what a run records
    cannot depend on which call site launched it.

    Args:
        campaign: The frozen campaign.
        ref: The run being launched.
        task: The task prompt, when it is not the campaign's frozen one. A
            per-cluster session narrows the window to a single ordinal, and
            renders it through the same template, so the two execution modes
            cannot drift apart in what they ask for.
        budget_usd: The session's own spend cap, when it is not the campaign's.
            ``campaign.budget_usd`` is the cap on a *run*; a run made of
            several sessions gives each what the run has left, so the total
            holds however many sessions there turn out to be.
        timeout_s: The session's own wall-clock cap, when it is not the
            campaign's; a sweep hands each session what the run has left.
        prompt_file: The file appended as the session's system prompt, when it
            is not this run's own arm file. SP7c's consolidation sessions pass
            their own.
        append: Append to the run's transcript rather than truncating it, so a
            run made of several sessions leaves one transcript.

    Returns:
        The spec :func:`~ai_rfc.driver.session.run_session` launches from.
    """
    return SessionSpec(
        claude=campaign.claude_bin,
        model=campaign.model,
        effort=campaign.effort,
        budget_usd=campaign.budget_usd if budget_usd is None else budget_usd,
        timeout_s=campaign.timeout_s if timeout_s is None else timeout_s,
        profile=campaign.profile_dir,
        python=campaign.python,
        workspace=ref.workspace,
        toolchain=Path(campaign.toolchain) if campaign.toolchain else None,
        prompt_file=prompt_file or campaign.prompts_dir / f"arm-{ref.arm}.md",
        task=(
            task if task is not None else (campaign.prompts_dir / "task.md").read_text()
        ),
        surface=arm_profile(ref.arm),
        append=append,
        bin_dir=campaign.bin_dir,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_status(run_dir: Path) -> RunStatus | None:
    """The run's status record, or ``None`` if it never finished launching.

    Args:
        run_dir: The run directory.

    Returns:
        The status, or None when ``status.json`` is absent.
    """
    path = run_dir / STATUS_FILE
    if not path.exists():
        return None
    return RunStatus(**json.loads(path.read_text()))


def launch(
    campaign: Campaign,
    ref: RunRef,
    *,
    report: Callable[[str], None] = print,
) -> RunStatus:
    """Run one session to completion or timeout, streaming its output to disk.

    Args:
        campaign: The frozen campaign.
        ref: The run to launch; its workspace copy must already exist.
        report: Where the per-cluster loop's progress lines go. Without it they
            fall back to printing, so a run's own lines arrive split across two
            streams and redirecting one loses half of them.

    Returns:
        The status record, also written as ``status.json``.

    Raises:
        ExperimentError: If the campaign carries no toolchain, the workspace
            is missing, or the run already has a status record.
    """
    if campaign.toolchain is None:
        # `Campaign.toolchain` defaults to `None` so a campaign frozen before
        # the build gate existed still loads for `audit`; it must not also be
        # launchable, since `session_env` would then silently omit
        # `AI_RFC_TOOLCHAIN` and the session would run with no build gate.
        raise ExperimentError(
            f"campaign {campaign.id} carries no toolchain; it was frozen "
            "before the build gate existed — initialise a new campaign with "
            "--toolchain"
        )
    if not ref.workspace.is_dir():
        raise ExperimentError(
            f"{ref.workspace} is missing; copy the pristine workspace first"
        )
    if (ref.run_dir / STATUS_FILE).exists():
        raise ExperimentError(
            f"{ref.run_id} already ran; a run is never relaunched in place. "
            f"Its workspace holds whatever it finished, so continue by chaining "
            f"a new campaign onto it: experiment workspace reseal "
            f"{ref.workspace} --as <name>, then campaign init --baseline <name>"
        )
    spec = session_spec(campaign, ref)
    # Built here rather than left to `run_session`, which writes the guard and
    # spawns in one step: the run's audit record has to be laid down *before*
    # the process that could edit it exists, and there is no point inside that
    # one step at which a caller could take these three.
    argv = prepare_argv(spec, ref.run_dir)
    # Digest the settings the guard is mounted from, before the process that
    # could edit them exists. The audit re-hashes the file and compares.
    guard_digest = hashlib.sha256((ref.run_dir / GUARD_FILE).read_bytes()).hexdigest()
    env = session_env(spec)
    (ref.run_dir / ARGV_FILE).write_text(json.dumps(argv, indent=2) + "\n")
    (ref.run_dir / ENV_FILE).write_text(
        json.dumps(env, indent=2, sort_keys=True) + "\n"
    )
    if campaign.session_mode == "per-cluster":
        if not campaign.task_template.exists():
            raise ExperimentError(
                f"{campaign.task_template} is missing; this campaign was frozen "
                "before task templates were frozen — initialise a new campaign"
            )
        task_record = (
            "(per-cluster mode: the task is rendered per session from "
            f"prompts/{TASK_TEMPLATE_FILE} for one ordinal; each session's "
            "rendered text is in sessions.jsonl under argv)\n\n"
            + campaign.task_template.read_text()
        )
    else:
        task_record = (campaign.prompts_dir / "task.md").read_text()
    (ref.run_dir / PROMPT_FILE).write_text(
        (campaign.prompts_dir / f"arm-{ref.arm}.md").read_text()
        + "\n\n---\n\n"
        + task_record
    )
    started = _now()
    if campaign.session_mode == "per-cluster":
        # Imported here, not at module scope: per_cluster needs this module's
        # `RunRef` and its `session_spec`, and importing it eagerly would make
        # that a cycle.
        from .per_cluster import run_per_cluster

        exit_code, timed_out, _ = run_per_cluster(campaign, ref, report=report)
    else:
        result = run_session(spec, ref.run_dir)
        exit_code, timed_out = result.exit_code, result.timed_out
    try:
        # Merged rather than taken from the tail: a run that spawns an agent
        # per cluster writes one result event per session, and the last one's
        # cost is that cluster's, not the run's.
        # Salvaged for the same reason the live budget loop is: a kill can
        # truncate a line, and refusing the whole transcript there nulls the
        # run's entire cost record over one bad line — pricing a real run as
        # unknown and dropping it out of every cost figure.
        final = merge_results(
            result_events(
                salvage_stream((ref.run_dir / EVENTS_FILE).read_text(errors="replace"))[
                    0
                ]
            )
        )
    except OSError:
        final = None
    (ref.run_dir / RESULT_FILE).write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n" if final else "null\n"
    )
    subtype = str((final or {}).get("subtype", "")).lower()
    # `exit_code=None if timed_out` is not a second normalisation of what
    # `SessionResult` already carries, and reads like one only from the
    # single-session branch. `run_per_cluster` folds a whole sweep into
    # `exit_code or 1` beside `any_timeout=True`, so the value arriving here
    # from a timed-out sweep is 1 and never None. This is campaign-level
    # aggregation over many sessions; deleting it files a killed sweep as a run
    # that ended on its own.
    status = RunStatus(
        run_id=ref.run_id,
        arm=ref.arm,
        repeat=ref.repeat,
        started_at=started,
        finished_at=_now(),
        exit_code=None if timed_out else exit_code,
        timed_out=timed_out,
        budget_hit="budget" in subtype,
        claude_version=campaign.claude_version,
        guard_sha256=guard_digest,
    )
    (ref.run_dir / STATUS_FILE).write_text(
        json.dumps(asdict(status), indent=2, sort_keys=True) + "\n"
    )
    return status
