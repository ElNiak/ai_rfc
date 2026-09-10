"""What a production run writes down, and how the driver reads it back.

Two properties carry this module, and both exist because a run can be killed
between the process returning and the record being written.

*The budget is a lifetime cap.* :func:`~ai_rfc.driver.record.spent` sums the
transcripts of every run the workspace has ever held, moved-aside ones
included, because a ``sessions.jsonl`` row is appended only after the process
returns: a kill in between loses the row but not the transcript. Skipping an
interrupted run would make a killed session's cost vanish and the cap be
overspent on every resume. A naive ``runs/*/events.jsonl`` glob already matches
``runs/<ts>.interrupted-<cause>/``, so asserting on the pattern proves nothing
here — every test below asserts the summed **value**.

*Moving a leftover aside never deletes it.* The resume rule keys on the absence
of ``status.json``, so what is moved aside is precisely the evidence of an
interrupted run. The tests assert the original path is gone **and** that the
renamed one holds the same bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ai_rfc.driver import DriverError, record, session


def _result(cost: Any, session_id: str = "s1") -> str:
    """One stream-json result event carrying a cost, as a transcript line."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "session_id": session_id,
            "total_cost_usd": cost,
        }
    )


def _transcript(run_dir: Path, *lines: str) -> Path:
    """Write a transcript into ``run_dir``, creating it."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / record.EVENTS_FILE
    path.write_text("".join(line + "\n" for line in lines))
    return path


def _run_record(**overrides: Any) -> dict[str, Any]:
    """A complete run record, with the declared keys filled in."""
    body: dict[str, Any] = {
        "run_id": "20260910T120000Z",
        "started_at": "2026-09-10T12:00:00+00:00",
        "config_sha256": "c" * 64,
        "init_sha256": "i" * 64,
        "prompt_sha256": "p" * 64,
        "task_sha256": "t" * 64,
        "prompt_drift": None,
        "claude_version": "2.1.247",
        "budget_usd": 40.0,
        "spent_before_usd": 0.0,
    }
    body.update(overrides)
    return body


def _session_row(**overrides: Any) -> dict[str, Any]:
    """A complete session row, with the declared keys filled in."""
    body: dict[str, Any] = {
        "session": 1,
        "kind": "cluster",
        "cluster_id": "c1",
        "ordinal": 0,
        "task_template": "task-cluster.md",
        "exit_code": 0,
        "timed_out": False,
        "cost_usd": 0.25,
        "lifetime_cost_usd": 0.25,
        "budget_given_usd": 40.0,
        "session_id": "s1",
        "wall_s": 12.5,
        "damaged": 0,
        "argv": ["claude", "-p"],
    }
    body.update(overrides)
    return body


# --- the declared contracts -------------------------------------------------
#
# Written out literally rather than derived from the constants. The
# parametrized tests below take their case list *from* the constant under
# test, so they catch `_require` failing to check a declared key but are blind
# to a key leaving the tuple: shrink the contract and every one of those cases
# simply disappears, silently. Task 10 consumes both shapes, so a narrowing is
# exactly the change that must not pass unnoticed.


def test_the_run_record_contract_is_exactly_these_keys() -> None:
    """Adding or removing a key here is a decision, never a side effect."""
    assert record.RUN_RECORD_KEYS == (
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


def test_the_session_row_contract_is_exactly_these_keys() -> None:
    """Including the three the campaign's row does not carry, and no ``attempt``."""
    assert record.SESSION_ROW_KEYS == (
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


# --- run.json ---------------------------------------------------------------


def test_write_run_record_creates_the_runs_directory(tmp_path: Path) -> None:
    """``<ws>/runs/`` has no producer anywhere else; this is where it appears."""
    run_dir = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    path = record.write_run_record(run_dir, _run_record())

    assert path == run_dir / record.RUN_RECORD_FILE
    assert json.loads(path.read_text())["run_id"] == "20260910T120000Z"


def test_write_run_record_refuses_a_second_write(tmp_path: Path) -> None:
    """A run record is written once; a second means two runs minted one id."""
    run_dir = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    record.write_run_record(run_dir, _run_record())

    with pytest.raises(DriverError, match="already"):
        record.write_run_record(run_dir, _run_record(budget_usd=1.0))

    assert json.loads((run_dir / record.RUN_RECORD_FILE).read_text())[
        "budget_usd"
    ] == pytest.approx(40.0)


@pytest.mark.parametrize("missing", record.RUN_RECORD_KEYS)
def test_write_run_record_requires_every_declared_key(
    tmp_path: Path, missing: str
) -> None:
    """The shape is fixed now so Task 10 cannot quietly land a partial one."""
    body = _run_record()
    del body[missing]

    with pytest.raises(DriverError, match=missing):
        record.write_run_record(tmp_path / "runs" / "r", body)


def test_write_run_record_accepts_a_null_valued_key(tmp_path: Path) -> None:
    """Presence is the requirement, not truthiness: drift is null on run one."""
    run_dir = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    path = record.write_run_record(run_dir, _run_record(prompt_drift=None))

    assert json.loads(path.read_text())["prompt_drift"] is None


def test_a_failed_run_record_write_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill mid-write must not leave half a JSON, nor a stray ``.tmp``."""
    run_dir = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    run_dir.mkdir(parents=True)

    def boom(self: Path, target: Any) -> Path:
        raise OSError("interrupted")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        record.write_run_record(run_dir, _run_record())

    assert list(run_dir.iterdir()) == []


# --- status.json ------------------------------------------------------------


def test_write_status_writes_the_record(tmp_path: Path) -> None:
    """``status.json`` is what a finished run is recognised by."""
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)

    path = record.write_status(run_dir, {"outcome": "done", "exit_code": 0})

    assert path == run_dir / record.STATUS_FILE
    assert json.loads(path.read_text()) == {"outcome": "done", "exit_code": 0}


def test_write_status_refuses_a_second_write(tmp_path: Path) -> None:
    """Written once and never revised, exactly as a campaign run's is."""
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    record.write_status(run_dir, {"outcome": "done"})

    with pytest.raises(DriverError, match="already"):
        record.write_status(run_dir, {"outcome": "budget"})

    assert json.loads((run_dir / record.STATUS_FILE).read_text()) == {"outcome": "done"}


def test_write_status_refuses_a_missing_run_directory(tmp_path: Path) -> None:
    """A status for a run that was never created names nothing."""
    with pytest.raises(DriverError, match="not a directory"):
        record.write_status(tmp_path / "runs" / "absent", {"outcome": "done"})


def test_a_failed_status_write_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume rule reads the *absence* of status.json; a half-file lies."""
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)

    def boom(self: Path, target: Any) -> Path:
        raise OSError("interrupted")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        record.write_status(run_dir, {"outcome": "done"})

    assert list(run_dir.iterdir()) == []


# --- sessions.jsonl ---------------------------------------------------------


def test_append_session_appends_rows_in_order(tmp_path: Path) -> None:
    """Append-only by design: a killed run keeps every row it did write."""
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)

    record.append_session(run_dir, _session_row(session=1))
    record.append_session(run_dir, _session_row(session=2, cluster_id="c2"))

    rows = [
        json.loads(line)
        for line in (run_dir / record.SESSIONS_FILE).read_text().splitlines()
    ]
    assert [row["session"] for row in rows] == [1, 2]
    assert [row["cluster_id"] for row in rows] == ["c1", "c2"]


@pytest.mark.parametrize("missing", record.SESSION_ROW_KEYS)
def test_append_session_requires_every_declared_key(
    tmp_path: Path, missing: str
) -> None:
    """A row missing a key is a silent hole in the run's own account."""
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    row = _session_row()
    del row[missing]

    with pytest.raises(DriverError, match=missing):
        record.append_session(run_dir, row)

    assert not (run_dir / record.SESSIONS_FILE).exists()


def test_append_session_accepts_the_nullable_keys(tmp_path: Path) -> None:
    """A consolidation row names no cluster, and a killed one no session id."""
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)

    record.append_session(
        run_dir,
        _session_row(
            kind="consolidation",
            cluster_id=None,
            ordinal=None,
            session_id=None,
            exit_code=None,
        ),
    )

    row = json.loads((run_dir / record.SESSIONS_FILE).read_text())
    assert row["kind"] == "consolidation"
    assert row["cluster_id"] is None


def test_append_session_refuses_a_missing_run_directory(tmp_path: Path) -> None:
    """A session belongs to a run; it never creates the run it belongs to."""
    with pytest.raises(DriverError, match="not a directory"):
        record.append_session(tmp_path / "runs" / "absent", _session_row())


# --- spent(): the lifetime cap ----------------------------------------------


def test_spent_counts_a_moved_aside_run_by_value(tmp_path: Path) -> None:
    """The headline: a killed run's cost is still on the lifetime bill.

    Asserted as a **sum**, not as a glob. ``runs/*/events.jsonl`` already
    matches a moved-aside directory, so a test that checked the pattern would
    pass against an implementation that filtered the interrupted ones back out.
    """
    _transcript(tmp_path / record.RUNS_DIR / "20260910T120000Z", _result(1.25))
    killed = record.move_aside(
        tmp_path / record.RUNS_DIR / "20260910T120000Z", "interrupt"
    )
    _transcript(tmp_path / record.RUNS_DIR / "20260910T130000Z", _result(0.5))

    assert killed.name == "20260910T120000Z.interrupted-interrupt"
    assert record.spent(tmp_path) == pytest.approx(1.75)


def test_spent_without_the_killed_run_would_be_lower(tmp_path: Path) -> None:
    """Names the figure the cap would silently overspend by."""
    _transcript(tmp_path / record.RUNS_DIR / "20260910T130000Z", _result(0.5))

    assert record.spent(tmp_path) == pytest.approx(0.5)


def test_spent_sums_every_result_event_of_a_multi_session_run(
    tmp_path: Path,
) -> None:
    """One transcript, several sessions: the run's cost is all of them."""
    _transcript(
        tmp_path / record.RUNS_DIR / "r",
        _result(0.25, "s1"),
        json.dumps({"type": "assistant", "session_id": "s1"}),
        _result(0.75, "s2"),
    )

    assert record.spent(tmp_path) == pytest.approx(1.0)


def test_spent_is_zero_without_a_runs_directory(tmp_path: Path) -> None:
    """A workspace that has never been run has spent nothing."""
    assert record.spent(tmp_path) == pytest.approx(0.0)


def test_spent_reads_a_result_by_the_same_rule_as_a_session(
    tmp_path: Path,
) -> None:
    """Pinned to the session-side accounting, or the two figures cannot agree.

    Task 10 computes ``spent_before + Σ cost_usd`` and must be able to re-read
    :func:`spent` and get the same number. The private helper is reached into
    deliberately: what is being pinned is that one rule reads a result event,
    not that two independent rules happen to agree today.
    """
    lines = (
        _result(0.25),
        _result("not-a-number"),
        _result(0.5),
        '{"type": "result", "total_cost_usd": 0.1',
    )
    _transcript(tmp_path / record.RUNS_DIR / "r", *lines)
    events, damaged = session.salvage_stream(
        (tmp_path / record.RUNS_DIR / "r" / record.EVENTS_FILE).read_text()
    )
    from_session, _ = session._session_cost(events, 0)

    assert damaged == 1
    assert record.spent(tmp_path) == pytest.approx(from_session)
    assert record.spent(tmp_path) == pytest.approx(0.75)


def test_spent_survives_a_damaged_transcript(tmp_path: Path) -> None:
    """A truncated line is a floor on the figure, never a refusal to give one."""
    _transcript(
        tmp_path / record.RUNS_DIR / "r",
        _result(0.25),
        '{"type": "result", "total_cost_usd": 9.9',
    )

    assert record.spent(tmp_path) == pytest.approx(0.25)


def test_spent_ignores_a_run_directory_with_no_transcript(tmp_path: Path) -> None:
    """A run killed before its first session wrote nothing to count."""
    (tmp_path / record.RUNS_DIR / "empty").mkdir(parents=True)
    _transcript(tmp_path / record.RUNS_DIR / "r", _result(0.25))

    assert record.spent(tmp_path) == pytest.approx(0.25)


# --- attempts() -------------------------------------------------------------


def test_attempts_counts_rows_across_runs_including_moved_aside(
    tmp_path: Path,
) -> None:
    """A cluster's attempts accumulate over every run of the workspace."""
    first = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    first.mkdir(parents=True)
    record.append_session(first, _session_row(cluster_id="c1"))
    record.move_aside(first, "interrupt")
    second = tmp_path / record.RUNS_DIR / "20260910T130000Z"
    second.mkdir(parents=True)
    record.append_session(second, _session_row(cluster_id="c1"))

    assert record.attempts(tmp_path, "c1") == 2


def test_attempts_ignores_other_clusters_and_consolidations(
    tmp_path: Path,
) -> None:
    """A consolidation row names no cluster and is nobody's attempt."""
    run_dir = tmp_path / record.RUNS_DIR / "r"
    run_dir.mkdir(parents=True)
    record.append_session(run_dir, _session_row(cluster_id="c1"))
    record.append_session(run_dir, _session_row(session=2, cluster_id="c2"))
    record.append_session(
        run_dir,
        _session_row(session=3, kind="consolidation", cluster_id=None, ordinal=None),
    )

    assert record.attempts(tmp_path, "c1") == 1
    assert record.attempts(tmp_path, "c2") == 1


def test_attempts_is_zero_without_a_runs_directory(tmp_path: Path) -> None:
    """An un-run workspace has attempted nothing."""
    assert record.attempts(tmp_path, "c1") == 0


def test_attempts_skips_a_truncated_row(tmp_path: Path) -> None:
    """A kill can truncate the last append; the rows before it still count."""
    run_dir = tmp_path / record.RUNS_DIR / "r"
    run_dir.mkdir(parents=True)
    record.append_session(run_dir, _session_row(cluster_id="c1"))
    with (run_dir / record.SESSIONS_FILE).open("a") as handle:
        handle.write('{"cluster_id": "c1"')

    assert record.attempts(tmp_path, "c1") == 1


def test_attempts_refuses_an_empty_cluster_id(tmp_path: Path) -> None:
    """An empty id would match a row that names no cluster at all."""
    with pytest.raises(DriverError, match="cluster id"):
        record.attempts(tmp_path, "")


# --- move_aside(): names the cause, never deletes ---------------------------


def test_move_aside_keeps_every_byte_of_an_interrupted_run(
    tmp_path: Path,
) -> None:
    """The headline: the original is gone and the bytes survived it.

    "Never deletes" is the property, so the content is what is asserted — a
    directory that merely exists at the new name would satisfy a rename that
    had recreated it empty.
    """
    run_dir = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    _transcript(run_dir, _result(1.25))
    (run_dir / "nested").mkdir()
    (run_dir / "nested" / "stderr.log").write_text("half a line")
    before = (run_dir / record.EVENTS_FILE).read_bytes()

    moved = record.move_aside(run_dir, "interrupt")

    assert not run_dir.exists()
    assert moved == tmp_path / record.RUNS_DIR / (
        "20260910T120000Z.interrupted-interrupt"
    )
    assert (moved / record.EVENTS_FILE).read_bytes() == before
    assert (moved / "nested" / "stderr.log").read_text() == "half a line"


def test_move_aside_names_an_interrupted_checkpoint_by_timestamp(
    tmp_path: Path,
) -> None:
    """The other half of the rule: ``checkpoints/<id>.interrupted-<ts>/``."""
    checkpoint = tmp_path / "checkpoints" / "c1"
    checkpoint.mkdir(parents=True)
    (checkpoint / "manifest.yaml").write_text("structures: []\n")

    moved = record.move_aside(checkpoint, "20260910T120000Z")

    assert not checkpoint.exists()
    assert moved.name == "c1.interrupted-20260910T120000Z"
    assert (moved / "manifest.yaml").read_text() == "structures: []\n"


def test_move_aside_refuses_a_collision_rather_than_replacing(
    tmp_path: Path,
) -> None:
    """``rename`` silently replaces an empty target directory; that is a loss."""
    run_dir = tmp_path / record.RUNS_DIR / "r"
    _transcript(run_dir, _result(1.25))
    (tmp_path / record.RUNS_DIR / "r.interrupted-interrupt").mkdir()

    with pytest.raises(DriverError, match="already exists"):
        record.move_aside(run_dir, "interrupt")

    assert (run_dir / record.EVENTS_FILE).exists()


def test_move_aside_refuses_a_cause_that_could_escape_the_directory(
    tmp_path: Path,
) -> None:
    """A cause is interpolated into a path; a separator there forges one."""
    run_dir = tmp_path / record.RUNS_DIR / "r"
    _transcript(run_dir, _result(1.25))

    with pytest.raises(DriverError, match="cause"):
        record.move_aside(run_dir, "../../escaped")

    assert (run_dir / record.EVENTS_FILE).exists()


@pytest.mark.parametrize("cause", ["", "with space", "..", "a/b", "sub\\dir"])
def test_move_aside_refuses_an_unusable_cause(tmp_path: Path, cause: str) -> None:
    """Only a name that reads back as one path segment is accepted."""
    run_dir = tmp_path / record.RUNS_DIR / "r"
    _transcript(run_dir, _result(1.25))

    with pytest.raises(DriverError, match="cause"):
        record.move_aside(run_dir, cause)


def test_move_aside_refuses_a_path_that_is_not_there(tmp_path: Path) -> None:
    """Nothing to move aside is a caller's mistake, not a silent no-op."""
    with pytest.raises(DriverError, match="does not exist"):
        record.move_aside(tmp_path / "runs" / "absent", "interrupt")


def test_move_aside_refuses_a_directory_already_moved_aside(tmp_path: Path) -> None:
    """A leftover has no ``status.json`` either, so it re-qualifies forever.

    The resume scan looks for a run directory without a status record, and a
    moved-aside one satisfies that on every subsequent resume. Left unguarded,
    each resume appends another suffix until the name reaches ``ENAMETOOLONG``
    and a resume dies on an ``OSError`` in the middle of its scan.
    """
    run_dir = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    _transcript(run_dir, _result(1.25))
    moved = record.move_aside(run_dir, "budget")

    with pytest.raises(DriverError, match="already been moved aside"):
        record.move_aside(moved, "interrupt")

    assert moved.name == "20260910T120000Z.interrupted-budget"
    assert (moved / record.EVENTS_FILE).exists()
    assert record.spent(tmp_path) == pytest.approx(1.25)


def test_move_aside_refuses_a_second_suffix_however_it_was_named(
    tmp_path: Path,
) -> None:
    """The guard reads the name, not this module's own history of it."""
    stray = tmp_path / "checkpoints" / "c1.interrupted-20260910T120000Z"
    stray.mkdir(parents=True)

    with pytest.raises(DriverError, match="already been moved aside"):
        record.move_aside(stray, "20260910T130000Z")

    assert stray.is_dir()
    assert list((tmp_path / "checkpoints").iterdir()) == [stray]


def test_move_aside_accepts_the_stop_reason_vocabulary(tmp_path: Path) -> None:
    """A cause may be an identifier, and the grammar must not refuse one.

    Of these four, ``budget`` (``experiment/runner.py:292``) and
    ``surface_shortfall`` (``experiment/per_cluster.py:694``) exist today;
    ``wall_clock`` exists only as the display string ``"wall clock"``
    (``per_cluster.py:425,539``) and ``cluster_halted`` nowhere. They are the
    plan's names for Task 9's stop reasons and so are a **forward guess** —
    revisit this list when Task 9 lands and settles the vocabulary. What is
    being asserted meanwhile is the grammar, not the spelling: an identifier
    with underscores must be accepted, as must the ISO timestamp a checkpoint
    is moved aside under.
    """
    for cause in ("budget", "wall_clock", "cluster_halted", "surface_shortfall"):
        run_dir = tmp_path / record.RUNS_DIR / cause
        _transcript(run_dir, _result(0.1))
        moved = record.move_aside(run_dir, cause)
        assert moved.name == f"{cause}.interrupted-{cause}"


# --- the drift baseline -----------------------------------------------------


def test_previous_run_record_is_none_on_the_first_run(tmp_path: Path) -> None:
    """Drift has nothing to be measured against until a run has been recorded."""
    assert record.previous_run_record(tmp_path) is None


def test_previous_run_record_prefers_a_moved_aside_run(tmp_path: Path) -> None:
    """A killed run still rendered its prompts; its digests are the baseline."""
    first = tmp_path / record.RUNS_DIR / "20260910T120000Z"
    record.write_run_record(first, _run_record(run_id="first", prompt_sha256="a" * 64))
    record.move_aside(first, "interrupt")
    record.write_run_record(
        tmp_path / record.RUNS_DIR / "20260910T110000Z",
        _run_record(run_id="older", prompt_sha256="b" * 64),
    )

    previous = record.previous_run_record(tmp_path)

    assert previous is not None
    assert previous["run_id"] == "first"


def test_previous_run_record_takes_the_latest_by_run_directory(
    tmp_path: Path,
) -> None:
    """Latest by directory name, which is why run ids must sort chronologically."""
    for run_id in ("20260910T110000Z", "20260910T130000Z", "20260910T120000Z"):
        record.write_run_record(
            tmp_path / record.RUNS_DIR / run_id, _run_record(run_id=run_id)
        )

    previous = record.previous_run_record(tmp_path)

    assert previous is not None
    assert previous["run_id"] == "20260910T130000Z"


def test_previous_run_record_ignores_a_run_that_never_wrote_one(
    tmp_path: Path,
) -> None:
    """A run killed before its record exists cannot be a baseline."""
    record.write_run_record(
        tmp_path / record.RUNS_DIR / "20260910T110000Z", _run_record(run_id="older")
    )
    (tmp_path / record.RUNS_DIR / "20260910T130000Z").mkdir(parents=True)

    previous = record.previous_run_record(tmp_path)

    assert previous is not None
    assert previous["run_id"] == "older"
