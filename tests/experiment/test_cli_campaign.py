import json
import sys

import pytest

from ai_rfc.experiment import cli

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE


def _init(tmp_path, pristine, panther_repo, capsys, *extra, skip_parity=True):
    code = cli.main(
        [
            "campaign",
            "init",
            "--root",
            str(tmp_path / "root"),
            "--id",
            "pilot-test",
            "--baseline",
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
            *(["--skip-parity"] if skip_parity else []),
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


def test_run_returns_nonzero_when_a_launched_run_failed(
    tmp_path, pristine, panther_repo, write_scenario, capsys
):
    """A campaign driver must be able to branch on `run`'s exit code.

    Every run's exit code was printed and then discarded, so a script could not
    tell a campaign where every run failed from one where every run passed.
    """
    _, _, campaign_dir = _init(tmp_path, pristine, panther_repo, capsys)
    order = json.loads((campaign_dir / "campaign.json").read_text())["run_order"]
    for run_id in order:
        write_scenario(
            tmp_path / "root" / "profile",
            run_id,
            {"arm": run_id[0], "cost": 1.0, "steps": COMPLETE_STEPS, "exit_code": 1},
        )
    assert cli.main(["run", str(campaign_dir)]) == 1
    assert "exit=1" in capsys.readouterr().out


def test_a_failing_parity_suite_exits_three_not_two(
    tmp_path, pristine, panther_repo, capsys, monkeypatch
):
    """2 belongs to argparse, so a stop-ship gate must not also return it.

    The next test asserts 2 for a genuine parse error on this same CLI; if the
    parity gate returned 2 as well, a caller could not tell a mistyped flag from
    a suite that must stop the campaign.
    """
    monkeypatch.setattr(
        cli, "_run_parity", lambda *_, **__: {"passed": False, "summary": "1 failed"}
    )

    code, _, _ = _init(tmp_path, pristine, panther_repo, capsys, skip_parity=False)

    assert code == 3


def test_a_window_override_reaches_the_target_prepare_builds(tmp_path, monkeypatch):
    """A slice of a target, for a dry run that must not cost a whole sweep.

    The window is what made the pilot a pilot; without an override, trying two
    clusters of MARK means either editing a module constant or paying for
    sixty-nine.

    Asserting the parsed value alone proved nothing: deleting the
    dataclasses.replace that applies it left that test green. What matters is
    the Target prepare actually receives, so that is what is captured.
    """
    seen = {}

    def fake_prepare(target, **kwargs):
        seen["target"] = target
        (tmp_path / "pristine.json").write_text(
            '{"cluster_count": 69, "pre_seeded": [], "window": [49, 51]}'
        )
        return tmp_path

    monkeypatch.setattr(cli, "prepare_workspace", fake_prepare)

    cli.main(
        [
            "workspace",
            "prepare",
            "mark",
            "--panther-repo",
            ".",
            "--root",
            str(tmp_path / "root"),
            "--window",
            "49-51",
        ]
    )

    assert seen["target"].window == (49, 51)
    assert seen["target"].name == "mark"
    # The override must flow into the derived name too, or two differently
    # windowed slices of one target would collide in pristine/.
    assert seen["target"].pristine_name == "mark-w49-51"


def test_without_the_override_the_targets_own_window_is_used(tmp_path, monkeypatch):
    seen = {}

    def fake_prepare(target, **kwargs):
        seen["target"] = target
        (tmp_path / "pristine.json").write_text(
            '{"cluster_count": 69, "pre_seeded": [], "window": [1, 69]}'
        )
        return tmp_path

    monkeypatch.setattr(cli, "prepare_workspace", fake_prepare)

    cli.main(
        [
            "workspace",
            "prepare",
            "mark",
            "--panther-repo",
            ".",
            "--root",
            str(tmp_path / "root"),
        ]
    )

    assert seen["target"].window == (1, 69)


def test_a_malformed_window_is_refused_at_parse_time(capsys):
    parser = cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["workspace", "prepare", "mark", "--panther-repo", ".", "--window", "5"]
        )
    assert "window" in capsys.readouterr().err


def test_unknown_arm_is_refused_at_parse_time(capsys):
    """Catching it here saves the parity suite's runtime, which init runs first."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["campaign", "init", "--arms", "A,Z"])
    assert exit_info.value.code == 2
    assert "unknown arm(s) Z" in capsys.readouterr().err


def test_repeated_arm_is_refused_at_parse_time(capsys):
    with pytest.raises(SystemExit):
        cli.main(["campaign", "init", "--arms", "A,A"])
    assert "repeated arm" in capsys.readouterr().err


def test_unknown_effort_is_refused_at_parse_time(capsys):
    with pytest.raises(SystemExit):
        cli.main(["campaign", "init", "--effort", "hihg"])
    assert "invalid choice" in capsys.readouterr().err


def test_empty_model_is_refused_but_an_unknown_one_is_not(capsys):
    """The harness does not own the model vocabulary, only rejects a blank."""
    with pytest.raises(SystemExit):
        cli.main(["campaign", "init", "--model", "  "])
    assert "cannot be empty" in capsys.readouterr().err
    parsed = cli._parser().parse_args(
        [
            "campaign",
            "init",
            "--id",
            "x",
            "--baseline",
            "p",
            "--panther-repo",
            ".",
            "--model",
            "some-model-released-next-year",
        ]
    )
    assert parsed.model == "some-model-released-next-year"


def test_run_parity_reports_the_suite():
    result = cli._run_parity(sys.executable)
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
            "--baseline",
            "nope",
            "--panther-repo",
            str(panther_repo),
            "--claude",
            str(FAKE_CLAUDE),
            "--skip-parity",
        ]
    )
    assert code == 1 and "not a prepared pristine workspace" in capsys.readouterr().err


def test_questions_lists_only_the_open_ones_by_default(tmp_path, capsys):
    """A sweep accumulates a backlog nobody sees unless something prints it."""
    import yaml

    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "questions.yaml").write_text(
        yaml.safe_dump(
            {
                "questions": {
                    "q-001": {
                        "question": "Is the unit seconds?",
                        "claim_ids": ["mark:data.3"],
                        "asked_at": "2026-09-02",
                        "status": "open",
                    },
                    "q-002": {
                        "question": "Already settled.",
                        "claim_ids": [],
                        "asked_at": "2026-09-02",
                        "status": "answered",
                    },
                }
            }
        )
    )

    assert cli.main(["questions", str(tmp_path / "run")]) == 0
    out = capsys.readouterr().out
    assert "1 open of 2" in out
    assert "q-001" in out and "Is the unit seconds?" in out
    assert "q-002" not in out

    assert cli.main(["questions", str(tmp_path / "run"), "--all"]) == 0
    assert "q-002" in capsys.readouterr().out


def test_questions_on_a_run_without_the_file_is_an_error(tmp_path, capsys):
    (tmp_path / "run" / "workspace").mkdir(parents=True)

    assert cli.main(["questions", str(tmp_path / "run")]) == 1
    assert "could not read" in capsys.readouterr().err
