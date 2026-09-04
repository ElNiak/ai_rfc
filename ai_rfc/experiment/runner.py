"""Launch one hermetic ``claude -p`` run and capture everything it emits.

The process gets a minimal environment, its stdout streams straight into
``events.jsonl`` as it arrives, a wall-clock cap is enforced on the whole
process group (the MCP server is a child), and ``status.json`` is written
exactly once — a run is never relaunched in place.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import ExperimentError
from .arms import MCP_FILE, arm_profile, claude_argv, mcp_config
from .config import Campaign
from .enforcement import bash_prefixes, render_settings
from .spawn import spawn
from .stream import merge_results, result_events, salvage_stream

EVENTS_FILE = "events.jsonl"
RESULT_FILE = "result.json"
STATUS_FILE = "status.json"
STDERR_FILE = "stderr.log"
ARGV_FILE = "argv.json"
ENV_FILE = "env.json"
PROMPT_FILE = "prompt.md"
GUARD_FILE = "guard.json"
GUARD = Path(__file__).parent / "guard.py"


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


def build_env(campaign: Campaign, ref: RunRef) -> dict[str, str]:
    """The minimal environment of a run: profile, contract, PATH, HOME, LANG.

    Args:
        campaign: The frozen campaign.
        ref: The run being launched.

    Returns:
        The complete environment; nothing else is inherited.
    """
    venv_bin = str(Path(campaign.python).parent)
    return {
        "CLAUDE_CONFIG_DIR": str(campaign.profile_dir),
        "AI_RFC_WORKSPACE": str(ref.workspace),
        **({"AI_RFC_TOOLCHAIN": campaign.toolchain} if campaign.toolchain else {}),
        "PATH": f"{campaign.bin_dir}:{venv_bin}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        # Measured on Claude Code 2.1.247 / macOS: drop USER and the CLI cannot
        # reach its stored credentials, answering "Not logged in" however valid
        # the profile. Spike S0 failed on exactly this before it was added.
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def prepare_run_argv(
    campaign: Campaign,
    ref: RunRef,
    task: str | None = None,
    budget_usd: float | None = None,
    prompt_file: Path | None = None,
) -> list[str]:
    """The argument vector of a run; writes its MCP config and its guard.

    The guard is what actually separates the arms: ``--allowedTools`` does not
    confine a built-in tool, so each run mounts a ``PreToolUse`` hook holding
    its own arm's command prefixes. It is written beside the run rather than
    inside ``AI_RFC_WORKSPACE``, which arms B and C can write.

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
        prompt_file: The file appended as the session's system prompt, when it
            is not this run's own arm file. SP7c's consolidation sessions pass
            their own.

    Returns:
        The complete ``claude -p`` argument vector.
    """
    this_arm = arm_profile(ref.arm)
    mcp_path = None
    if this_arm.uses_mcp:
        mcp_path = ref.run_dir / MCP_FILE
        mcp_path.write_text(
            json.dumps(
                mcp_config(
                    python=campaign.python,
                    workspace=ref.workspace,
                    toolchain=Path(campaign.toolchain) if campaign.toolchain else None,
                ),
                indent=2,
            )
            + "\n"
        )
    guard_path = ref.run_dir / GUARD_FILE
    guard_path.write_text(
        json.dumps(
            render_settings(
                python=campaign.python,
                guard=GUARD,
                prefixes=bash_prefixes(this_arm),
            ),
            indent=2,
        )
        + "\n"
    )
    return claude_argv(
        claude_bin=campaign.claude_bin,
        prompt=(
            task if task is not None else (campaign.prompts_dir / "task.md").read_text()
        ),
        this_arm=this_arm,
        mcp_config_path=mcp_path,
        model=campaign.model,
        effort=campaign.effort,
        budget_usd=campaign.budget_usd if budget_usd is None else budget_usd,
        prompt_file=prompt_file or campaign.prompts_dir / f"arm-{ref.arm}.md",
        guard_settings=guard_path,
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
        ExperimentError: If the workspace is missing or the run already has
            a status record.
    """
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
    argv = prepare_run_argv(campaign, ref)
    # Digest the settings the guard is mounted from, before the process that
    # could edit them exists. The audit re-hashes the file and compares.
    guard_digest = hashlib.sha256((ref.run_dir / GUARD_FILE).read_bytes()).hexdigest()
    env = build_env(campaign, ref)
    (ref.run_dir / ARGV_FILE).write_text(json.dumps(argv, indent=2) + "\n")
    (ref.run_dir / ENV_FILE).write_text(
        json.dumps(env, indent=2, sort_keys=True) + "\n"
    )
    if campaign.session_mode == "per-cluster":
        task_record = (
            "(per-cluster mode: the task is rendered per session from "
            "prompts/task.tmpl.md for one ordinal; each session's rendered text is "
            "in sessions.jsonl under argv)\n\n" + campaign.task_template.read_text()
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
        # Imported here, not at module scope: per_cluster needs this
        # module's env and argv builders, and importing it eagerly would make
        # that a cycle.
        from .per_cluster import run_per_cluster

        exit_code, timed_out, _ = run_per_cluster(campaign, ref, report=report)
    else:
        exit_code, timed_out = spawn(
            argv,
            cwd=ref.workspace,
            env=env,
            events_path=ref.run_dir / EVENTS_FILE,
            stderr_path=ref.run_dir / STDERR_FILE,
            timeout_s=campaign.timeout_s,
        )
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
