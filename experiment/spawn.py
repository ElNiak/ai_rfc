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
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return exit_code, timed_out
