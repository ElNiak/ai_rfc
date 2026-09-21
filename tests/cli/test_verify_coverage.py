"""``ai-rfc verify``'s coverage check: which checkpoints nothing vouches for.

The workspace here is initialised but never run, so ``gate`` and
``completeness`` have nothing to read and ``perform`` is stubbed clean — which
leaves ``coverage`` as the only check that can contribute a finding. That is
what makes the exit code attributable to it rather than to the lint an unrun
workspace always reports.
"""

import json
from types import SimpleNamespace

import pytest

from ai_rfc import cli, ledger
from ai_rfc.lifecycle.verify import cli as verify_cli

WITH_ROW = "c0001-epoch-1111aaaa2222"
PRE_SEEDED = "c0002-pr-3333bbbb4444"
HAND_WRITTEN = "c0003-epoch-5555cccc6666"
RUN_ID = "20260921T101500Z"


@pytest.fixture
def clean_checks(monkeypatch):
    """Every gate `perform` drives reports clean, so only coverage can speak."""
    monkeypatch.setattr(
        verify_cli, "perform", lambda *a, **k: SimpleNamespace(exit_code=0)
    )


def _checkpoint(root, cluster, *, pre_seeded=False):
    directory = root / "checkpoints" / cluster
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ledger.CHECKPOINT_FILE).write_text(
        json.dumps({"cluster_id": cluster}, sort_keys=True) + "\n"
    )
    if pre_seeded:
        (directory / ledger.PRESEED_MARKER).write_text("{}\n")


def _run(root, *, rows=(), events=()):
    run_dir = root / "runs" / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        (run_dir / "sessions.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        )
    if events:
        (run_dir / "events.jsonl").write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
        )
    return run_dir


def _three_checkpoints(root):
    """One covered by a row, one pre-seeded, one nothing vouches for."""
    _checkpoint(root, WITH_ROW)
    _checkpoint(root, PRE_SEEDED, pre_seeded=True)
    _checkpoint(root, HAND_WRITTEN)
    _run(root, rows=[{"cluster_id": WITH_ROW, "kind": "cluster"}])


def test_an_unverified_checkpoint_is_one_finding_and_exit_three(
    initialised, clean_checks, capsys
):
    config_path, root = initialised
    _three_checkpoints(root)
    capsys.readouterr()

    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 3
    err = capsys.readouterr().err
    assert "coverage: findings" in err
    assert err.count("finding: ") == 1
    assert (
        f"finding: checkpoint {HAND_WRITTEN} is unverified: no session row, "
        "no pre-seed marker, no receipt" in err
    )


def test_the_same_workspace_exits_zero_without_strict(
    initialised, clean_checks, capsys
):
    config_path, root = initialised
    _three_checkpoints(root)
    capsys.readouterr()

    assert cli.main(["verify", "--config", str(config_path)]) == 0
    assert "coverage: findings" in capsys.readouterr().err


def test_the_record_names_the_route_of_every_checkpoint(
    initialised, clean_checks, capsys
):
    config_path, root = initialised
    _three_checkpoints(root)
    capsys.readouterr()

    cli.main(["verify", "--config", str(config_path), "--strict"])
    recorded = json.loads((root / "out" / "coverage.json").read_text())
    assert recorded["checkpoints"] == {
        WITH_ROW: "session",
        PRE_SEEDED: "pre_seed",
        HAND_WRITTEN: None,
    }
    assert recorded["unreadable"] == []


def test_the_record_is_byte_stable_across_two_runs(initialised, clean_checks, capsys):
    config_path, root = initialised
    _three_checkpoints(root)
    capsys.readouterr()

    cli.main(["verify", "--config", str(config_path), "--strict"])
    first = (root / "out" / "coverage.json").read_bytes()
    cli.main(["verify", "--config", str(config_path), "--strict"])
    assert (root / "out" / "coverage.json").read_bytes() == first


def test_a_workspace_that_was_never_run_says_nothing_about_the_absent_directory(
    initialised, clean_checks, capsys
):
    """A hand-driven or MCP-only workspace never gets a ``runs/`` at all.

    Absence is ordinary, not suspicious: the two remaining routes decide, and
    the directory that is not there is never mentioned.
    """
    config_path, root = initialised
    _checkpoint(root, PRE_SEEDED, pre_seeded=True)
    assert not (root / "runs").exists()
    capsys.readouterr()

    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 0
    err = capsys.readouterr().err
    assert "coverage: ok" in err
    assert "runs" not in err
    assert json.loads((root / "out" / "coverage.json").read_text())["checkpoints"] == {
        PRE_SEEDED: "pre_seed"
    }


def test_a_receipt_in_the_workspaces_own_transcript_covers_a_checkpoint(
    initialised, clean_checks, capsys
):
    config_path, root = initialised
    _checkpoint(root, HAND_WRITTEN)
    written = root / "checkpoints" / HAND_WRITTEN
    envelope = json.dumps(
        {
            "exit_code": 0,
            "stderr": [f"note: checkpoint written to {written}"],
            "manifest_sha256": "0" * 64,
        }
    )
    _run(
        root,
        events=[
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "use-1",
                            "name": "mcp__ai_rfc__ai_rfc_checkpoint",
                            "input": {"cluster_id": HAND_WRITTEN},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "use-1",
                            "content": [{"type": "text", "text": envelope}],
                        }
                    ]
                },
            },
        ],
    )
    capsys.readouterr()

    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 0
    assert "coverage: ok" in capsys.readouterr().err
    assert json.loads((root / "out" / "coverage.json").read_text())["checkpoints"] == {
        HAND_WRITTEN: "receipt"
    }


def test_a_damaged_transcript_is_named_and_is_neither_a_finding_nor_an_error(
    initialised, clean_checks, capsys
):
    """``verify`` maps any code that is not 0 or 3 to 1, so nothing may escape."""
    config_path, root = initialised
    _checkpoint(root, PRE_SEEDED, pre_seeded=True)
    run_dir = _run(root)
    (run_dir / "events.jsonl").write_text('{"type": "assistant"}\n{"type": "us')
    capsys.readouterr()

    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 0
    err = capsys.readouterr().err
    assert "cannot adjudicate: " in err
    assert "finding: " not in err
    assert "coverage: ok" in err
    recorded = json.loads((root / "out" / "coverage.json").read_text())
    assert len(recorded["unreadable"]) == 1
    assert recorded["unreadable"][0].startswith("cannot adjudicate: ")


def test_coverage_is_counted_among_the_checks_that_ran(
    initialised, clean_checks, capsys
):
    config_path, _root = initialised
    capsys.readouterr()

    assert cli.main(["verify", "--config", str(config_path)]) == 0
    assert "checks: 4 ran, 3 skipped (gate, completeness, build)" in (
        capsys.readouterr().err
    )
