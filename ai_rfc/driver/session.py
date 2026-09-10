"""Launch one ``claude -p`` session — the single path every caller goes through.

There were two. The whole-window launcher assembled the argument vector, wrote
the per-run MCP config and the guard settings, and built the closed environment;
the per-cluster sweep called back into those private helpers and then did its
own transcript accounting on top. Anything either side learned had to be taught
twice, and the arm's enforcement is carried by the files this module writes, so
a call site that skipped one would launch a session that reports as confined and
is not.

What a session needs is a :class:`SessionSpec`; what it did is a
:class:`SessionResult`. Neither mentions a campaign: this package is the lower
layer, so production and the three-arm experiment describe a session in the same
terms rather than in one another's.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_rfc.config import experiments_root, profile_dir

from . import DriverError
from .arms import MCP_FILE, ArmProfile, claude_argv, mcp_config
from .enforcement import bash_prefixes, render_settings
from .spawn import spawn
from .stream import result_events, salvage_stream, session_ids

#: The transcript every session of a run appends to.
EVENTS_FILE = "events.jsonl"
#: Where the session's stderr is streamed.
STDERR_FILE = "stderr.log"
#: The settings document that mounts the arm's ``PreToolUse`` guard.
GUARD_FILE = "guard.json"
#: The guard itself, a sibling of this module. Derived here rather than handed
#: in: the hook is the driver's own enforcement, and a caller free to name a
#: different script could mount one that permits everything.
GUARD = Path(__file__).resolve().parent / "guard.py"


def resolve_profile(configured: Path | None) -> Path:
    """The isolated Claude Code profile a session launches against.

    ``sessions.profile`` is declared with no default, and the loader passes the
    raw ``None`` through, so nothing between a configuration file and a launch
    fills it in. The fallback is the one the doctor reports, so what a
    diagnostic says a session will use is what it uses.

    Args:
        configured: The configured profile directory, or None.

    Returns:
        The configured directory, else ``<experiments root>/profile``.
    """
    return configured or profile_dir(experiments_root())


@dataclass(frozen=True)
class SessionSpec:
    """Everything one session is launched with.

    Attributes:
        claude: The Claude Code binary.
        model: Model id.
        effort: Reasoning effort.
        budget_usd: This session's own spend cap. A run made of several
            sessions gives each what the run has left, so the total holds
            however many sessions there turn out to be; a one-shot round
            passes the whole cap.
        timeout_s: Wall-clock cap on the session's process group.
        profile: ``CLAUDE_CONFIG_DIR``, or None to take the default. It is
            optional because the configured value is: ``sessions.profile`` is
            declared with no default and the loader passes the raw ``None``
            through, so a spec that could not carry one would force every
            production caller to resolve the fallback itself — and a fallback
            resolved by each caller is a fallback that drifts. See
            :func:`resolve_profile`.
        python: The interpreter the MCP server and the guard run under.
        workspace: The session's working directory and ``AI_RFC_WORKSPACE``.
        toolchain: The build-gate toolchain record, or None.
        prompt_file: Appended as the session's system prompt.
        task: The rendered task prompt, passed to ``-p``.
        surface: The arm's enforcement profile, not its letter:
            :func:`~ai_rfc.driver.arms.arm_flags` refuses a profile whose
            ``uses_mcp`` and MCP path disagree, and only the profile can keep
            the two in step.
        append: Append to the run's transcript rather than truncating it, so a
            run made of several sessions leaves one transcript.
        bin_dir: A directory placed first on the session's ``PATH``, holding
            the campaign's ``ai_rfc`` shim. Arm B reaches the substrate as
            ``Bash(ai_rfc *)``, and the shim is what that name resolves to;
            without it the arm keeps the permission and loses the command.
    """

    claude: str
    model: str
    effort: str
    budget_usd: float
    timeout_s: int
    profile: Path | None
    python: str
    workspace: Path
    toolchain: Path | None
    prompt_file: Path
    task: str
    surface: ArmProfile
    append: bool = False
    bin_dir: Path | None = None


@dataclass(frozen=True)
class SessionResult:
    """What one session did, read back off the transcript it appended to.

    Attributes:
        exit_code: The process's exit code, already None when the group was
            killed on the cap. It is not re-derived from ``timed_out``: one
            fact written twice from two places is how the two launchers drifted.
        timed_out: Whether the cap killed the group.
        cost_usd: What the result events appended by *this* session report.
        results_seen: The transcript's result-event count now, to pass back as
            the next session's ``seen``.
        session_ids: Every distinct session id in the transcript, in
            first-appearance order — not only this session's. The transcript is
            shared, and a caller that must attribute work to one session
            filters against the ids it already knew.
        wall_s: Seconds from entry to the transcript being read back.
        argv: The vector this session actually ran, which is not the one a
            run's ``argv.json`` recorded before its first session.
        events: The salvaged transcript, whole.
        damaged: Transcript lines that could not be parsed. A kill can truncate
            a line mid-write and the next session appends onto that tail, so
            the cost above is a floor whenever this is non-zero.
    """

    exit_code: int | None
    timed_out: bool
    cost_usd: float
    results_seen: int
    session_ids: tuple[str, ...]
    wall_s: float
    argv: tuple[str, ...]
    events: tuple[dict[str, Any], ...]
    damaged: int


def session_env(spec: SessionSpec) -> dict[str, str]:
    """The complete environment of a session: profile, contract, PATH, HOME.

    Args:
        spec: The session being launched.

    Returns:
        The environment; nothing else is inherited.
    """
    directories = [str(spec.bin_dir)] if spec.bin_dir else []
    directories += [str(Path(spec.python).parent), "/usr/bin", "/bin"]
    return {
        "CLAUDE_CONFIG_DIR": str(resolve_profile(spec.profile)),
        "AI_RFC_WORKSPACE": str(spec.workspace),
        **({"AI_RFC_TOOLCHAIN": str(spec.toolchain)} if spec.toolchain else {}),
        "PATH": ":".join(directories),
        "HOME": os.environ.get("HOME", ""),
        # Measured on Claude Code 2.1.247 / macOS: drop USER and the CLI cannot
        # reach its stored credentials, answering "Not logged in" however valid
        # the profile. Spike S0 failed on exactly this before it was added.
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def prepare_argv(spec: SessionSpec, run_dir: Path) -> list[str]:
    """The session's argument vector; writes its MCP config and its guard.

    The guard is what actually separates the arms: ``--allowedTools`` does not
    confine a built-in tool, so each session mounts a ``PreToolUse`` hook
    holding its own arm's command prefixes. Both files are written beside the
    run rather than inside ``AI_RFC_WORKSPACE``, which arms B and C can write.

    Args:
        spec: The session being launched.
        run_dir: Where the two files are written.

    Returns:
        The complete ``claude -p`` argument vector.

    Raises:
        DriverError: If the arm's surface and its MCP mount disagree.
        OSError: If either file cannot be written.
    """
    mcp_path = None
    if spec.surface.uses_mcp:
        mcp_path = run_dir / MCP_FILE
        mcp_path.write_text(
            json.dumps(
                mcp_config(
                    python=spec.python,
                    workspace=spec.workspace,
                    toolchain=spec.toolchain,
                ),
                indent=2,
            )
            + "\n"
        )
    guard_path = run_dir / GUARD_FILE
    guard_path.write_text(
        json.dumps(
            render_settings(
                python=spec.python,
                guard=GUARD,
                prefixes=bash_prefixes(spec.surface),
            ),
            indent=2,
        )
        + "\n"
    )
    return claude_argv(
        claude_bin=spec.claude,
        prompt=spec.task,
        this_arm=spec.surface,
        mcp_config_path=mcp_path,
        model=spec.model,
        effort=spec.effort,
        budget_usd=spec.budget_usd,
        prompt_file=spec.prompt_file,
        guard_settings=guard_path,
    )


def _read_events(events_path: Path) -> tuple[list[dict[str, Any]], int]:
    """The transcript so far, and how many of its lines could not be read.

    Salvaged rather than parsed strictly: a kill can truncate a line mid-write
    and the next session appends onto that tail, so one unparseable line is a
    normal outcome of the interruptions a session must survive. Refusing the
    whole transcript there would freeze the accumulated spend, and a frozen
    spend is a budget ceiling that can never be reached again.

    Args:
        events_path: The run's transcript.

    Returns:
        ``(events, damaged)``; ``([], 0)`` when the file cannot be read.
    """
    try:
        return salvage_stream(events_path.read_text(errors="replace"))
    except OSError:
        return [], 0


def _session_cost(events: list[dict[str, Any]], seen: int) -> tuple[float, int]:
    """What was spent by the result events appended since ``seen``.

    Read back off the transcript rather than tracked, because the transcript is
    what survives a kill. Counting from ``seen`` rather than taking the tail
    matters on exactly the path this design exists for: a session killed on its
    cap emits no result event, so the tail is still the *previous* session's,
    and charging that again both overstates the run and writes the wrong figure
    into the per-session record.

    Args:
        events: The transcript, already salvaged.
        seen: How many result events it held before this session.

    Returns:
        ``(cost, total)`` — what the new events report, and the transcript's new
        result-event count. A session that produced none reports 0.0, because it
        said nothing about its own spend.
    """
    results = result_events(events)
    cost = 0.0
    for result in results[seen:]:
        value = result.get("total_cost_usd")
        if isinstance(value, (int, float)):
            cost += float(value)
    return cost, len(results)


def run_session(spec: SessionSpec, run_dir: Path, *, seen: int = 0) -> SessionResult:
    """Launch one session and read back what it did.

    Args:
        spec: The session to launch.
        run_dir: Where its MCP config, guard settings, transcript and stderr
            live. It must already exist; a session never creates the run it
            belongs to.
        seen: How many result events the transcript already held. Pass the
            previous :attr:`SessionResult.results_seen`; leaving it at 0 on a
            transcript that already holds results charges this session for
            every earlier one.

    Returns:
        What the session did, including the transcript as it now stands.

    Raises:
        DriverError: If ``run_dir`` or the workspace is not a directory, or the
            guard script is missing — a guard that cannot be executed exits
            127 rather than 2, and only exit 2 blocks, so the arm would keep a
            hook that reports as mounted and permits every command.
    """
    if not run_dir.is_dir():
        raise DriverError(f"{run_dir} is not a directory; create the run first")
    if not spec.workspace.is_dir():
        raise DriverError(
            f"{spec.workspace} is missing; a session runs inside a workspace "
            "that already exists"
        )
    if not GUARD.is_file():
        raise DriverError(
            f"{GUARD} is missing; without it the mounted hook cannot block and "
            "the arm's Bash surface is unconfined"
        )
    started = time.monotonic()
    argv = prepare_argv(spec, run_dir)
    events_path = run_dir / EVENTS_FILE
    exit_code, timed_out = spawn(
        argv,
        cwd=spec.workspace,
        env=session_env(spec),
        events_path=events_path,
        stderr_path=run_dir / STDERR_FILE,
        timeout_s=spec.timeout_s,
        append=spec.append,
    )
    events, damaged = _read_events(events_path)
    cost, results = _session_cost(events, seen)
    return SessionResult(
        exit_code=exit_code,
        timed_out=timed_out,
        cost_usd=cost,
        results_seen=results,
        session_ids=tuple(session_ids(events)),
        wall_s=time.monotonic() - started,
        argv=tuple(argv),
        events=tuple(events),
        damaged=damaged,
    )
