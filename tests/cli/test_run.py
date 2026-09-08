"""`ai-rfc run`: perform what is next, stop at the boundary with the ledger in hand."""

from ai_rfc import cli
from ai_rfc.lifecycle.workspace import Layout


def test_run_performs_the_deterministic_stages_and_stops_at_mining(initialised, capsys):
    config_path, root = initialised
    assert cli.main(["run", "--config", str(config_path)]) == 0
    ws = Layout(root)
    assert ws.commits.exists() and ws.timeline_json.exists()
    assert any((ws.clusters).glob("*/view.json"))
    err = capsys.readouterr().err
    assert "history" in err and "timeline" in err and "views" in err
    assert "boundary: mining" in err
    assert "clusters: 0 of" in err and "done" in err
    assert "sessions: not configured" in err


def test_a_second_run_performs_nothing_new(initialised, capsys):
    config_path, _ = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["run", "--config", str(config_path)]) == 0
    err = capsys.readouterr().err
    assert "performed: nothing" in err and "boundary: mining" in err


def test_until_stops_after_the_named_stage(initialised, capsys):
    config_path, root = initialised
    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0
    ws = Layout(root)
    assert ws.commits.exists() and not ws.timeline_json.exists()
    assert "stopped after history" in capsys.readouterr().err


def test_until_stops_again_when_the_named_stage_is_already_current(initialised, capsys):
    """``--until`` bounds the walk, not only the stages this invocation performed.

    The first run performs ``history`` and stops. The second finds it already
    done, and must still stop there rather than stepping over the bound and
    performing ``timeline`` and ``views`` — a documented flag overrunning on
    its second invocation.
    """
    config_path, root = initialised
    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0
    capsys.readouterr()
    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0
    err = capsys.readouterr().err
    assert "stopped after history" in err
    assert not Layout(root).timeline_json.exists()


def test_a_drifted_pin_is_refused_before_anything_runs(initialised, capsys):
    config_path, root = initialised
    config_path.write_text(config_path.read_text().replace("pin: main", "pin: v9"))
    assert cli.main(["run", "--config", str(config_path)]) == 1
    err = capsys.readouterr().err
    assert "refused" in err and "source.pin" in err
    assert not Layout(root).commits.exists()


def test_a_noted_drift_is_reported_and_run_continues(initialised, capsys):
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + "sessions:\n  budget_usd: 5\n")
    assert cli.main(["run", "--config", str(config_path)]) == 0
    err = capsys.readouterr().err
    assert "note: config drift" in err and "sessions.budget_usd" in err
    assert "sessions: configured" in err


def test_run_needs_an_initialised_workspace(tmp_path, source_repo, capsys):
    config_path = tmp_path / "recon.yaml"
    config_path.write_text(
        f"name: fixture\nworkspace: {tmp_path / 'nope'}\nsource:\n"
        f"  repo: {source_repo}\n  host: none\n  pin: main\n"
        "draft:\n  name: draft-test-fixture\n"
    )
    assert cli.main(["run", "--config", str(config_path)]) == 1
    assert "ai-rfc init" in capsys.readouterr().err
