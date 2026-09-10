"""What a production run writes down, and how the driver reads it back.

A run leaves four things under ``<workspace>/runs/<run id>/``: ``run.json``,
written once at the start and saying what the run was launched from;
``events.jsonl``, the transcript every session appends to;
``sessions.jsonl``, one row per session; and ``status.json``, written only
when the run ends on its own terms. Nothing else in ``ai_rfc`` creates
``<workspace>/runs/`` — :attr:`ai_rfc.lifecycle.workspace.Layout.runs` names it
and has no producer — so it comes into being here, with the first run record.

Two rules hold this module together, and both exist because a run can be
killed between its process returning and its record being written.

*The budget is a lifetime cap, so* :func:`spent` *counts interrupted runs.* It
sums the result events of every run directory the workspace holds, moved-aside
ones included, because a ``sessions.jsonl`` row is appended only after the
process returns: a kill in between loses the row but not the transcript. A
:func:`spent` that skipped interrupted runs would let a killed session's cost
vanish, and the lifetime cap would be quietly overspent on every resume. The
run directories are therefore enumerated rather than matched against a
pattern — a moved-aside run is a child of ``runs/`` like any other, and is
included by construction rather than by a glob someone could later narrow.

*What is interrupted is renamed, never deleted.* The resume rule keys on the
absence of a marker: ``runs/<ts>/`` without ``status.json`` is an interrupted
run, ``checkpoints/<id>/`` without ``checkpoint.json`` an interrupted
checkpoint. :func:`move_aside` names the cause in the directory's own name and
moves it whole; what it moves is the evidence of the interruption, and
:func:`spent` still reads it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import DriverError
from .session import EVENTS_FILE
from .stream import result_events, salvage_stream

#: One directory per run, under the workspace root. The literal is duplicated
#: by :attr:`ai_rfc.lifecycle.workspace.Layout.runs`, which cannot be imported
#: here: ``driver`` sits below ``lifecycle`` and may not depend on it.
RUNS_DIR = "runs"
#: Written once per invocation, before the first session exists.
RUN_RECORD_FILE = "run.json"
#: One row per session, appended after the process returns.
SESSIONS_FILE = "sessions.jsonl"
#: Written only when a run ends on its own terms. Its *absence* is what marks
#: a run as interrupted, so nothing may write it from a ``finally``.
STATUS_FILE = "status.json"
#: What :func:`move_aside` inserts before the cause.
INTERRUPTED = ".interrupted-"

#: Every key a run record must carry. Fixed now so the shape does not have to
#: change when Task 10 fills in the digests it computes:
#:
#: ``run_id``
#:     The run directory's name. Run ids must sort chronologically as strings,
#:     because :func:`previous_run_record` takes the latest by that order.
#: ``started_at``
#:     ISO 8601, UTC.
#: ``config_sha256`` / ``init_sha256``
#:     The workspace's sealed ``recon.yaml`` and its ``init.json`` — what the
#:     run was launched against, so a later reader can tell whether two runs
#:     reconstructed from the same frozen inputs.
#: ``prompt_sha256`` / ``task_sha256``
#:     The rendered system prompt and task text. Production has no freeze
#:     step, so each invocation renders from the package templates; these are
#:     what makes an unannounced template change visible.
#: ``prompt_drift``
#:     Whether those two digests differ from the previous run's. ``None`` on
#:     the first run, which has no baseline. See :func:`previous_run_record`.
#: ``claude_version``
#:     The binary the run launched.
#: ``budget_usd``
#:     The configured lifetime cap, not this run's share of it.
#: ``spent_before_usd``
#:     :func:`spent` as it stood before the first session, so the run's own
#:     share of the lifetime cap can be read back off the record alone.
RUN_RECORD_KEYS: tuple[str, ...] = (
    "run_id",
    "started_at",
    "config_sha256",
    "init_sha256",
    "prompt_sha256",
    "task_sha256",
    "prompt_drift",
    "claude_version",
    "budget_usd",
    "spent_before_usd",
)

#: Every key a session row must carry. The campaign's row
#: (``experiment/per_cluster.py``) is the precedent; the divergences are
#: deliberate:
#:
#: ``kind``
#:     ``"cluster"`` or ``"consolidation"``, the same vocabulary the revision
#:     entries and :mod:`ai_rfc.ledger` already use. The campaign had no need
#:     for it because a campaign's consolidation sessions are a separate verb.
#: ``lifetime_cost_usd``
#:     The campaign's ``cumulative_cost_usd`` is a *run's* running total, and
#:     production's cap is not a run's. Renamed rather than reused, so a row
#:     cannot be read as bounding the wrong thing.
#: ``wall_s`` / ``damaged``
#:     Both are on :class:`~ai_rfc.driver.session.SessionResult` and the
#:     campaign keeps them elsewhere — in its per-cluster summaries and in a
#:     progress line. Production writes no per-cluster summary, so a row that
#:     dropped them would leave no durable record of how long a session ran or
#:     that its cost is a floor.
#:
#: ``attempt`` is *not* carried. The campaign numbers a cluster's retries
#: within one run; production counts them across the workspace's whole history
#: with :func:`attempts`, and a per-run counter beside it would be a second
#: answer to the same question.
#:
#: ``cluster_id``, ``ordinal``, ``session_id`` and ``exit_code`` are nullable —
#: a consolidation session names no cluster, and a session killed before its
#: init event never announced an id. Presence is what is required, never a
#: value.
SESSION_ROW_KEYS: tuple[str, ...] = (
    "session",
    "kind",
    "cluster_id",
    "ordinal",
    "task_template",
    "exit_code",
    "timed_out",
    "cost_usd",
    "lifetime_cost_usd",
    "budget_given_usd",
    "session_id",
    "wall_s",
    "damaged",
    "argv",
)

#: A cause is interpolated into a directory name, so it must read back as one
#: path segment. A separator would forge a path, and ``..`` would move the
#: leftover out of the directory it is evidence in.
_CAUSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]*")


def _require(body: dict[str, Any], keys: tuple[str, ...], what: str) -> None:
    """Refuse a record that does not carry every declared key.

    Args:
        body: The record.
        keys: The keys it must carry.
        what: What the record is, for the message.

    Raises:
        DriverError: If any key is absent. A key present but null is accepted:
            the nullable fields are nullable on purpose.
    """
    missing = [key for key in keys if key not in body]
    if missing:
        raise DriverError(
            f"{what} is missing {', '.join(missing)}; the shape is fixed so a "
            "later reader can rely on every field being there"
        )


def _write_once(path: Path, body: dict[str, Any], what: str) -> Path:
    """Write a record atomically, and only if it is not already there.

    Args:
        path: Where the record lands.
        body: The record.
        what: What the record is, for the message.

    Returns:
        The path written.

    Raises:
        DriverError: If the file already exists.
        OSError: If the write or the rename fails.
    """
    if path.exists():
        raise DriverError(
            f"{path} already exists; {what} is written once and never revised"
        )
    temporary = path.with_name(path.name + ".tmp")
    try:
        # default=str: a record may carry a Path — a task template, a profile —
        # and a run that could not be recorded because of one is a run whose
        # whole account is lost.
        temporary.write_text(
            json.dumps(body, indent=2, sort_keys=True, default=str) + "\n"
        )
        temporary.replace(path)
    finally:
        # A kill mid-write must leave neither half a JSON at the real name nor
        # a stray ``.tmp`` beside it: the resume rule reads a run directory by
        # what it holds, and a leftover temporary is one more thing to explain.
        temporary.unlink(missing_ok=True)
    return path


def write_run_record(run_dir: Path, record: dict[str, Any]) -> Path:
    """Write ``run.json``, creating the run directory and ``runs/`` with it.

    This is where ``<workspace>/runs/`` first comes into being: it is the first
    thing an invocation writes, before any session exists.

    Args:
        run_dir: The run's directory; created, parents included.
        record: The run record. It must carry every key in
            :data:`RUN_RECORD_KEYS`; extra keys are kept as given.

    Returns:
        The path written.

    Raises:
        DriverError: If a key is missing, or the run already has a record —
            which means two runs minted the same id.
        OSError: If the directory or the file cannot be written.
    """
    _require(record, RUN_RECORD_KEYS, "the run record")
    run_dir.mkdir(parents=True, exist_ok=True)
    return _write_once(run_dir / RUN_RECORD_FILE, record, "a run record")


def append_session(run_dir: Path, row: dict[str, Any]) -> None:
    """Append one session's row to ``sessions.jsonl``.

    Append-only by design, and written after the process returns: a run killed
    mid-session keeps every row it did write, and loses only the row for the
    session that was killed. That loss is why :func:`spent` reads transcripts
    rather than these rows.

    Args:
        run_dir: The run's directory; it must already exist.
        row: The session row. It must carry every key in
            :data:`SESSION_ROW_KEYS`; extra keys are kept as given.

    Raises:
        DriverError: If the run directory is not there, or a key is missing.
        OSError: If the row cannot be appended.
    """
    if not run_dir.is_dir():
        raise DriverError(
            f"{run_dir} is not a directory; a session belongs to a run that "
            "already exists"
        )
    _require(row, SESSION_ROW_KEYS, "the session row")
    with (run_dir / SESSIONS_FILE).open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def write_status(run_dir: Path, status: dict[str, Any]) -> Path:
    """Write ``status.json`` — the mark of a run that ended on its own terms.

    Never write this from a ``finally``. Its absence is what tells a resume
    that a run was interrupted, and a status written unconditionally would
    make an interrupted run indistinguishable from a finished one.

    Args:
        run_dir: The run's directory; it must already exist.
        status: The status record. Its shape is the caller's: what a run ended
            on is classified above this module.

    Returns:
        The path written.

    Raises:
        DriverError: If the run directory is not there, or the run already has
            a status.
        OSError: If the file cannot be written.
    """
    if not run_dir.is_dir():
        raise DriverError(
            f"{run_dir} is not a directory; a status belongs to a run that "
            "already exists"
        )
    return _write_once(run_dir / STATUS_FILE, status, "a status")


def _run_dirs(workspace: Path) -> list[Path]:
    """Every run directory the workspace holds, oldest name first.

    Enumerated rather than globbed, and deliberately unfiltered: a moved-aside
    run is a child of ``runs/`` like any other, so it is included because it is
    there, not because a pattern happens to match its name.

    Args:
        workspace: The workspace root.

    Returns:
        The run directories, sorted by name; empty when the workspace has
        never been run.
    """
    runs = workspace / RUNS_DIR
    if not runs.is_dir():
        return []
    return sorted(child for child in runs.iterdir() if child.is_dir())


def _events(run_dir: Path) -> list[dict[str, Any]]:
    """A run's transcript, salvaged.

    Salvaged rather than parsed strictly for the reason the live budget loop
    is: a kill can truncate a line mid-write and the next session appends onto
    that tail, so one unparseable line is a normal outcome. Refusing the whole
    transcript there would drop a real run's cost out of the lifetime figure.

    Args:
        run_dir: The run directory.

    Returns:
        The events that parsed; empty when there is no readable transcript.
    """
    try:
        events, _ = salvage_stream((run_dir / EVENTS_FILE).read_text(errors="replace"))
    except OSError:
        return []
    return events


def spent(workspace: Path) -> float:
    """What every run of this workspace has cost, moved-aside runs included.

    The lifetime cap is enforced against this. It is read off the transcripts
    rather than off ``sessions.jsonl`` because a row is appended only after the
    process returns: a kill in between loses the row but not the transcript,
    and a figure that skipped interrupted runs would let a killed session's
    cost vanish from the bill.

    A result event is read by the same rule
    :func:`ai_rfc.driver.session.run_session` charges a session by — a
    ``total_cost_usd`` that is not a number is not counted — so a run's
    ``spent_before_usd`` plus its rows' ``cost_usd`` re-reads as this figure.

    Args:
        workspace: The workspace root.

    Returns:
        The total in USD; ``0.0`` when the workspace has never been run. It is
        a *floor* whenever any transcript line was damaged, since a truncated
        line may have carried a cost. The count of damaged lines is on each
        session's row.
    """
    total = 0.0
    for run_dir in _run_dirs(workspace):
        for result in result_events(_events(run_dir)):
            value = result.get("total_cost_usd")
            if isinstance(value, (int, float)):
                total += float(value)
    return total


def _rows(path: Path) -> list[dict[str, Any]]:
    """The session rows of one run, skipping any line that cannot be read.

    Args:
        path: The ``sessions.jsonl`` to read.

    Returns:
        The rows that parsed; empty when the file is not there.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # A kill can truncate the last append the same way it truncates a
            # transcript line. The rows written before it are still the record.
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def attempts(workspace: Path, cluster_id: str) -> int:
    """How many sessions this workspace has already spent on one cluster.

    Counted across every run, moved-aside ones included, because the cap on a
    cluster's retries is a property of the reconstruction and not of the
    invocation that happens to be running.

    Unlike :func:`spent`, this can only be read off ``sessions.jsonl``: a
    transcript carries no cluster id, so there is nowhere else to look. A
    session killed before its row was appended is therefore not counted, and a
    cluster whose sessions are repeatedly killed can be retried more often than
    the configured cap allows.

    Args:
        workspace: The workspace root.
        cluster_id: The cluster's id.

    Returns:
        The number of rows naming that cluster; ``0`` when it has never been
        attempted.

    Raises:
        DriverError: If the cluster id is empty, which would otherwise match
            the rows that deliberately name no cluster.
    """
    if not cluster_id:
        raise DriverError(
            "a cluster id is required; an empty one matches the rows that name "
            "no cluster, such as a consolidation session's"
        )
    return sum(
        1
        for run_dir in _run_dirs(workspace)
        for row in _rows(run_dir / SESSIONS_FILE)
        if row.get("cluster_id") == cluster_id
    )


def previous_run_record(workspace: Path) -> dict[str, Any] | None:
    """The run record the next run's prompt drift is measured against.

    The baseline is the latest run directory that recorded one, by directory
    name — which is why a run id must sort chronologically as a string — and
    moved-aside runs count: a killed run still rendered its prompts, so its
    digests are as good a baseline as a finished run's. A run killed before it
    wrote a record has no digests and is passed over.

    Args:
        workspace: The workspace root.

    Returns:
        The record, or ``None`` when no run has written one — the first run
        has no baseline, and its ``prompt_drift`` is null rather than false.
    """
    for run_dir in reversed(_run_dirs(workspace)):
        try:
            body = json.loads((run_dir / RUN_RECORD_FILE).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(body, dict):
            return body
    return None


def move_aside(path: Path, cause: str) -> Path:
    """Rename an interrupted directory to name its cause. Nothing is deleted.

    A ``runs/<ts>/`` without ``status.json`` becomes
    ``runs/<ts>.interrupted-<cause>/``; a ``checkpoints/<id>/`` without
    ``checkpoint.json`` becomes ``checkpoints/<id>.interrupted-<ts>/``. Which
    marker file is missing is the caller's judgement — this moves whatever it
    is handed — but what it moves is evidence, so it is moved whole and
    :func:`spent` goes on reading it.

    Args:
        path: The directory to move aside.
        cause: What interrupted it, as one path segment: letters, digits and
            ``_ . : + -``, beginning with a letter or digit. A stop reason for
            a run, a timestamp for a checkpoint.

    Returns:
        The new path.

    Raises:
        DriverError: If the cause is not usable as one path segment, the path
            is not there, or something already stands at the new name —
            ``rename`` would silently replace an empty directory, and a
            leftover that vanished is the one thing this must not do.
        OSError: If the rename fails.
    """
    if not _CAUSE.fullmatch(cause):
        raise DriverError(
            f"{cause!r} is not a usable cause; it is interpolated into a "
            "directory name, so it must be one path segment of letters, "
            "digits and _ . : + -"
        )
    if not path.exists():
        raise DriverError(f"{path} does not exist; there is nothing to move aside")
    target = path.with_name(f"{path.name}{INTERRUPTED}{cause}")
    if target.exists():
        raise DriverError(
            f"{target} already exists; moving {path} onto it would replace "
            "evidence of an earlier interruption"
        )
    path.rename(target)
    return target
