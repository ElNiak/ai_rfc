import json
import sys

from experiment import cli

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE


def _init(tmp_path, pristine, panther_repo, capsys, *extra):
    code = cli.main(
        [
            "campaign",
            "init",
            "--root",
            str(tmp_path / "root"),
            "--id",
            "pilot-test",
            "--pristine",
            str(pristine),
            "--repeats",
            "1",
            "--seed",
            "3",
            "--model",
            "fake",
            "--budget",
            "1",
            "--timeout",
            "900",
            "--panther-repo",
            str(panther_repo),
            "--python",
            sys.executable,
            "--claude",
            str(FAKE_CLAUDE),
            "--skip-parity",
            *extra,
        ]
    )
    out = capsys.readouterr().out
    return code, out, tmp_path / "root" / "campaigns" / "pilot-test"


def test_campaign_init_run_audit_analyze_round_trip(
    tmp_path, pristine, panther_repo, write_scenario, capsys
):
    code, out, campaign_dir = _init(tmp_path, pristine, panther_repo, capsys)
    assert code == 0 and "run order:" in out and campaign_dir.exists()
    order = json.loads((campaign_dir / "campaign.json").read_text())["run_order"]
    for run_id in order:
        write_scenario(
            tmp_path / "root" / "profile",
            run_id,
            {"arm": run_id[0], "cost": 1.0, "steps": COMPLETE_STEPS},
        )
    assert cli.main(["run", str(campaign_dir), "--only", order[0]]) == 0
    assert cli.main(["run", str(campaign_dir)]) == 0
    err = capsys.readouterr().err
    assert err.count("launching") == 3 and "skipping" in err
    assert cli.main(["audit", str(campaign_dir)]) == 0
    assert "integrity=True" in capsys.readouterr().out
    assert cli.main(["analyze", str(campaign_dir)]) == 0
    assert (campaign_dir / "analysis" / "aggregate.json").exists()
    report = (campaign_dir / "analysis" / "report.md").read_text()
    assert "# Campaign pilot-test" in report and "| A |" in report


def test_run_parity_reports_the_suite(plugin_root):
    result = cli._run_parity(plugin_root, sys.executable)
    assert result["passed"] is True and "passed" in result["summary"]


def test_campaign_init_refuses_unknown_pristine(tmp_path, panther_repo, capsys):
    code = cli.main(
        [
            "campaign",
            "init",
            "--root",
            str(tmp_path / "root"),
            "--id",
            "x",
            "--pristine",
            "nope",
            "--panther-repo",
            str(panther_repo),
            "--claude",
            str(FAKE_CLAUDE),
            "--skip-parity",
        ]
    )
    assert code == 1 and "not a prepared pristine workspace" in capsys.readouterr().err
