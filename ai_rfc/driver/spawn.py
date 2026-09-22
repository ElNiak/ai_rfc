"""Launch one child process under a wall-clock cap, and kill its whole group.

Extracted from :mod:`runner` so a driver that spawns an agent per cluster gets
the same lifetime guarantees as a single-session run rather than a second,
subtly different copy of them. The MCP server is a child of the session, so a
cap enforced on the process alone would leave it running.

A run's transcript is also its directory's claim. ``events.jsonl`` is created
exclusively — by :func:`claim` for a caller with sidecars to write first, else
by :func:`spawn` itself for a run's first session — so a second launch reaching
for the same run directory is refused before its process exists and before it
has spent anything, rather than truncating a live run's transcript and spending
a fresh budget beside it.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import BinaryIO, Literal

from . import DriverError

#: Seconds a terminated group is given to exit before it is killed outright.
#: Long enough for the MCP server to close its own files, short enough that a
#: wedged run does not hold the campaign open.
KILL_GRACE_S = 30
#: What :func:`ai_rfc.driver.record.move_aside` inserts before the cause. The
#: literal is duplicated from :data:`ai_rfc.driver.record.INTERRUPTED`, which
#: cannot be imported here: ``record`` imports ``session`` for its transcript
#: filename and ``session`` imports this module, so the import would close a
#: cycle. It is here because the refusal below is worth nothing without the
#: remedy — an operator told only that a directory is held has to guess.
INTERRUPTED = ".interrupted-"


def _kill_group(process: subprocess.Popen) -> None:
    """Terminate the process group, then reap it, escalating if it lingers.

    Args:
        process: The group leader, started with ``start_new_session=True``, and
            still running *on entry* -- the first signal is sent unguarded, and
            both callers establish that much: the cap path has just seen
            ``TimeoutExpired``, and the abnormal-exit path checks ``returncode``.
            Liveness after the grace wait is re-checked here, not assumed.
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
        # for -- but only if the grace wait had not already reaped the child,
        # since signalling a dead group would replace the operator's stop with
        # a ProcessLookupError. The cap branch above needs no such check:
        # TimeoutExpired is itself proof the child outlived the grace.
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise


def _claim(path: Path, mode: Literal["ab", "xb"]) -> BinaryIO:
    """Open one of a session's output files, never destroying a held one.

    Args:
        path: The file to open.
        mode: ``"ab"`` when the session is continuing a run's transcript,
            ``"xb"`` when it is the run's first. The exclusive create is what
            claims the run directory: a second launch of the same run id is
            refused here, before its process exists and before it has spent
            anything.

    Returns:
        The open file.

    Raises:
        DriverError: If the create was exclusive and the file is already
            there. The message names the holder and how to release it if the
            holding run is dead.
        OSError: If the file cannot be opened for any other reason.
    """
    try:
        return open(path, mode)
    except FileExistsError as error:
        raise DriverError(
            f"{path} already exists -- another run holds this directory; if "
            f"it is dead, move it aside: mv {path.parent} "
            f"{path.parent}{INTERRUPTED}<cause>"
        ) from error


def claim(events_path: Path) -> None:
    """Claim a run directory by creating its transcript, exclusively and empty.

    The same exclusive create :func:`spawn` performs, taken by a caller that
    has work to do *before* the spawn. ``experiment.runner.launch`` writes
    four sidecars — ``guard.json`` among them — between deciding to run and
    reaching :func:`spawn`, and a refusal at the spawn came four files too
    late: the second launch had already rewritten the holder's guard settings,
    restoring a tampered ``guard.json`` to pristine bytes past the digest that
    exists to catch the tampering.

    One syscall, so hoisting the claim turns nothing into a check-then-act.
    The caller then spawns with ``append=True`` for the run's first session,
    because the transcript it would otherwise create exclusively is the one
    this function already made.

    The file it leaves is zero bytes. That is not damage:
    :func:`ai_rfc.driver.coverage.read_transcript` answers ``([], None)`` for
    it — covers nothing, reports nothing unreadable — and
    :func:`ai_rfc.driver.record.transcripts` skips a run directory that has no
    transcript at all, so neither state is mistaken for the other.

    Args:
        events_path: The run's ``events.jsonl``, which does not yet exist.

    Raises:
        DriverError: If it does exist — another launch holds this run
            directory. The message names the transcript and the move-aside
            that releases it if the holding run is dead.
        OSError: If it cannot be created for any other reason.
    """
    _claim(events_path, "xb").close()


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
        append: Append to the output files, because this session is continuing
            a run another session began, so a run made of several sessions
            leaves one transcript. False means the opposite — this is the run's
            first session — and never "truncate whatever is there": the
            transcript is then created exclusively, which is what claims the
            run directory. Every caller passes False for a run's first session
            and True after.

    Returns:
        ``(exit_code, timed_out)``. The exit code is ``None`` when the group was
        killed on the cap.

    Raises:
        DriverError: If this is a run's first session and the run directory is
            already held by another launch. Nothing is spawned.
        OSError: If either output file cannot be opened.
    """
    # Annotated rather than inferred: the two modes are the whole vocabulary
    # here, and a widened ``str`` would let a third one -- ``"wb"``, the one
    # this replaced -- pass the type checker into the call below.
    mode: Literal["ab", "xb"] = "ab" if append else "xb"
    timed_out = False
    exit_code: int | None = None
    # The transcript is opened first because it is the claim: a second launch
    # is refused on it, before the stderr log it would otherwise have created.
    with _claim(events_path, mode) as events:
        try:
            stderr_file = _claim(stderr_path, mode)
        except DriverError:
            if mode == "xb":
                # A stray stderr.log with no transcript beside it: the claim
                # above *succeeded*, so this refusal would otherwise leave a
                # zero-byte events.jsonl holding the run directory in the name
                # of a launch that never started -- while the message names
                # stderr.log, which is then not what holds it. Released, so the
                # refusal is true about what an operator will find.
                events.close()
                events_path.unlink(missing_ok=True)
            raise
        with stderr_file as stderr:
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
                # The terminal's SIGINT reached this process only: the session
                # is in its own group. BaseException, not Exception, because the
                # Ctrl-C and the SystemExit a signal handler raises orphan it
                # alike, and nothing downstream can notice -- from this side a
                # session that is still spending looks exactly like a finished
                # one. The guard is for an asynchronous interrupt that landed
                # after the child was already reaped, where signalling would
                # only raise ProcessLookupError over the operator's interrupt.
                if process.returncode is None:
                    _kill_group(process)
                raise
    return exit_code, timed_out
