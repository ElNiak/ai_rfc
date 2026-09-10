"""Launch one child process under a wall-clock cap, and kill its whole group.

Extracted from :mod:`runner` so a driver that spawns an agent per cluster gets
the same lifetime guarantees as a single-session run rather than a second,
subtly different copy of them. The MCP server is a child of the session, so a
cap enforced on the process alone would leave it running.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

#: Seconds a terminated group is given to exit before it is killed outright.
#: Long enough for the MCP server to close its own files, short enough that a
#: wedged run does not hold the campaign open.
KILL_GRACE_S = 30


def _kill_group(process: subprocess.Popen) -> None:
    """Terminate the process group, then reap it, escalating if it lingers.

    Args:
        process: The group leader, started with ``start_new_session=True``,
            and known not to have exited yet.
    """
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except BaseException:
        # A second Ctrl-C can only land in this window when the first one
        # bought nothing: a healthy session is gone in under a second, so the
        # operator taps again precisely while a SIGTERM-ignoring child holds
        # the grace open. Escalate now or abandon the one case SIGKILL exists
        # for.
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise


def spawn(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    events_path: Path,
    stderr_path: Path,
    timeout_s: int,
    append: bool = False,
) -> tuple[int | None, bool]:
    """Run one process to completion or timeout, streaming its output to disk.

    Args:
        argv: The command to run.
        cwd: Its working directory.
        env: Its complete environment; nothing else is inherited.
        events_path: Where stdout is streamed as it arrives.
        stderr_path: Where stderr is streamed.
        timeout_s: Wall-clock cap on the whole process group.
        append: Append to the output files rather than truncating them, so a
            run made of several sessions leaves one transcript.

    Returns:
        ``(exit_code, timed_out)``. The exit code is ``None`` when the group was
        killed on the cap.
    """
    mode = "ab" if append else "wb"
    timed_out = False
    exit_code: int | None = None
    with open(events_path, mode) as events, open(stderr_path, mode) as stderr:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=events,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            exit_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process)
        except BaseException:
            # The terminal's SIGINT reached this process only: the session is in
            # its own group. BaseException, not Exception, because the Ctrl-C
            # and the SystemExit a signal handler raises orphan it alike, and
            # nothing downstream can notice -- from this side a session that is
            # still spending looks exactly like a finished one. The guard is for
            # an asynchronous interrupt that landed after the child was already
            # reaped, where signalling would only raise ProcessLookupError over
            # the operator's interrupt.
            if process.returncode is None:
                _kill_group(process)
            raise
    return exit_code, timed_out
