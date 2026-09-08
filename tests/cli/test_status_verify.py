"""`ai-rfc status` and `ai-rfc verify`.

Both read the same config, the same layout and the same ledger as `run`.
"""

import json

from ai_rfc import cli


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


def test_verify_strict_records_every_unprocessed_cluster(initialised, capsys):
    """A run with no mining behind it is incomplete, and the record names which.

    ``verify`` prints one line per check and leaves the detail to each check's
    own report, so the unprocessed cluster ids are asserted where they are
    actually written — ``out/completeness.json`` — rather than on stderr, where
    ``draft completeness`` reports only a count.
    """
    config_path, root = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 3
    captured = capsys.readouterr()
    assert "completeness: findings" in captured.err
    assert "check: ok" in captured.err and "gate: ok" in captured.err
    assert "build: skipped" in captured.err
    # The exit code alone does not discriminate: lint independently returns 3
    # on this workspace, so 3 would be reached even if completeness had passed.
    ids = [
        json.loads(line)["id"]
        for line in (root / "timeline" / "clusters.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert ids
    recorded = json.loads((root / "out" / "completeness.json").read_text())
    assert sorted(recorded["unprocessed_clusters"]) == sorted(ids)


def test_verify_without_strict_reports_and_exits_zero(initialised, capsys):
    config_path, _ = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["verify", "--config", str(config_path)]) == 0


def test_verify_names_the_checks_it_could_not_run(initialised, capsys):
    """Exit 0 means nothing that ran failed, never that everything ran.

    On a workspace that was initialised but never run, ``gate``, ``completeness``
    and ``build`` are all skipped and contribute no exit code, so 0 alone cannot
    be told from a clean full pass. The tally is what makes the difference
    legible to an operator and greppable by a driver.
    """
    config_path, _ = initialised
    capsys.readouterr()
    assert cli.main(["verify", "--config", str(config_path)]) == 0
    err = capsys.readouterr().err
    assert "checks: 3 ran, 3 skipped (gate, completeness, build)" in err


def test_verify_builds_when_the_config_names_a_usable_toolchain(
    initialised, toolchain_record, capsys
):
    """The one branch that spawns the draft build, which every other test skips."""
    config_path, _ = initialised
    cli.main(["run", "--config", str(config_path)])
    config_path.write_text(config_path.read_text() + f"toolchain: {toolchain_record}\n")
    capsys.readouterr()
    cli.main(["verify", "--config", str(config_path), "--strict"])
    err = capsys.readouterr().err
    assert "build: skipped" not in err
    assert "checks: 6 ran, 0 skipped" in err


def test_verify_reports_a_refused_drift_as_a_finding(initialised, capsys):
    config_path, _ = initialised
    config_path.write_text(
        config_path.read_text().replace("draft-test-fixture", "draft-other")
    )
    assert cli.main(["verify", "--config", str(config_path), "--strict"]) == 3
    assert "drift: refused" in capsys.readouterr().err
