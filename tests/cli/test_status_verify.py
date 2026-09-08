"""`ai-rfc status` and `ai-rfc verify`.

Both read the same config, the same layout and the same ledger as `run`.
"""

import json
from pathlib import Path

import pytest

from ai_rfc import cli


@pytest.fixture(autouse=True)
def _experiments_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every defaulted path resolves under the test's own tree, never $HOME."""
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))


def test_status_json_carries_stages_ledger_init_and_drift(initialised, capsys):
    config_path, root = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["status", "--config", str(config_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"] == str(root)
    assert {s["name"] for s in payload["stages"]} >= {
        "history",
        "timeline",
        "views",
        "mining",
    }
    assert payload["ledger"]["done"] == 0
    assert payload["ledger"]["outstanding"] == payload["ledger"]["in_window"]
    assert payload["next_cluster"]["ordinal"] == 1
    assert payload["init"]["resolved_pin"]
    assert payload["drift"] == {"refused": [], "noted": []}


def test_status_prints_a_human_table(initialised, capsys):
    config_path, _ = initialised
    capsys.readouterr()
    assert cli.main(["status", "--config", str(config_path)]) == 0
    out = capsys.readouterr().out
    assert "history" in out and "clusters:" in out and "next:" in out


def test_verify_strict_names_every_unprocessed_cluster(initialised, capsys):
    config_path, _ = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 3
    captured = capsys.readouterr()
    assert "completeness: findings" in captured.err
    assert "check: ok" in captured.err and "gate: ok" in captured.err
    assert "build: skipped" in captured.err


def test_verify_without_strict_reports_and_exits_zero(initialised, capsys):
    config_path, _ = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["verify", "--config", str(config_path)]) == 0


def test_verify_reports_a_refused_drift_as_a_finding(initialised, capsys):
    config_path, _ = initialised
    config_path.write_text(
        config_path.read_text().replace("draft-test-fixture", "draft-other")
    )
    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 3
    assert "drift: refused" in capsys.readouterr().err
