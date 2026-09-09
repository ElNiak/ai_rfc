import json
import sys
from pathlib import Path

import pytest

from ai_rfc.experiment import cli

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE


def _init(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, *extra, skip_parity=True
):
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
            "--toolchain",
            str(toolchain_record),
            *(["--skip-parity"] if skip_parity else []),
            *extra,
        ]
    )
    out = capsys.readouterr().out
    return code, out, tmp_path / "root" / "campaigns" / "pilot-test"


def test_campaign_init_run_audit_analyze_round_trip(
    tmp_path, pristine, panther_repo, write_scenario, capsys, toolchain_record
):
    code, out, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
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
    tmp_path, pristine, panther_repo, write_scenario, capsys, toolchain_record
):
    """A campaign driver must be able to branch on `run`'s exit code.

    Every run's exit code was printed and then discarded, so a script could not
    tell a campaign where every run failed from one where every run passed.
    """
    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
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
    tmp_path, pristine, panther_repo, capsys, monkeypatch, toolchain_record
):
    """2 belongs to argparse, so a stop-ship gate must not also return it.

    The next test asserts 2 for a genuine parse error on this same CLI; if the
    parity gate returned 2 as well, a caller could not tell a mistyped flag from
    a suite that must stop the campaign.
    """
    monkeypatch.setattr(
        cli, "_run_parity", lambda *_, **__: {"passed": False, "summary": "1 failed"}
    )

    code, _, _ = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record, skip_parity=False
    )

    assert code == 3


def _recon(tmp_path: Path) -> Path:
    """A minimal recon.yaml the CLI can load without reaching the network."""
    path = tmp_path / "recon.yaml"
    path.write_text(
        "name: mark\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "source:\n"
        f"  repo: {tmp_path / 'source'}\n"
        "  host: none\n"
        "  pin: main\n"
        "window: [1, 69]\n"
        "draft:\n"
        "  name: draft-test-mark\n"
        f"toolchain: {tmp_path / 'tools' / 'toolchain.json'}\n"
    )
    return path


def test_a_window_override_reaches_the_config_prepare_builds(tmp_path, monkeypatch):
    """A slice of a reconstruction, for a dry run that must not cost a sweep.

    The window is what made the pilot a pilot; without an override, trying two
    clusters of MARK means either editing the config or paying for sixty-nine.

    Asserting the parsed value alone proved nothing: deleting the
    dataclasses.replace that applies it left that test green. What matters is
    the config prepare actually receives, so that is what is captured.
    """
    seen = {}

    def fake_prepare(config, **kwargs):
        seen["config"] = config
        (tmp_path / "pristine.json").write_text(
            '{"cluster_count": 69, "pre_seeded": [], "window": [49, 51]}'
        )
        return tmp_path

    monkeypatch.setattr(cli, "prepare_workspace", fake_prepare)

    cli.main(
        [
            "workspace",
            "prepare",
            "--config",
            str(_recon(tmp_path)),
            "--root",
            str(tmp_path / "root"),
            "--window",
            "49-51",
        ]
    )

    assert seen["config"].window == (49, 51)
    assert seen["config"].name == "mark"


def test_without_the_override_the_configs_own_window_is_used(tmp_path, monkeypatch):
    seen = {}

    def fake_prepare(config, **kwargs):
        seen["config"] = config
        (tmp_path / "pristine.json").write_text(
            '{"cluster_count": 69, "pre_seeded": [], "window": [1, 69]}'
        )
        return tmp_path

    monkeypatch.setattr(cli, "prepare_workspace", fake_prepare)

    cli.main(
        [
            "workspace",
            "prepare",
            "--config",
            str(_recon(tmp_path)),
            "--root",
            str(tmp_path / "root"),
        ]
    )

    assert seen["config"].window == (1, 69)


def test_a_malformed_window_is_refused_at_parse_time(capsys):
    parser = cli._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["workspace", "prepare", "--config", "recon.yaml", "--window", "5"]
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


def test_campaign_init_refuses_unknown_pristine(
    tmp_path, panther_repo, capsys, toolchain_record
):
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
            "--toolchain",
            str(toolchain_record),
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


def _finished_run(campaign_dir: Path, run_id: str, revisions: str) -> Path:
    """A run directory holding the revision map a finished sweep left behind.

    Args:
        campaign_dir: The campaign the run belongs to.
        run_id: The run's id in the frozen order.
        revisions: The body of its ``revisions.yaml``.

    Returns:
        The run's workspace.
    """
    workspace = campaign_dir / "runs" / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "revisions.yaml").write_text(revisions)
    return workspace


ONE_UNCONSOLIDATED_CLUSTER = (
    "revisions:\n"
    "  draft-t-01:\n"
    "    cluster_id: c1\n"
    f"    checkpoint_manifest_sha256: {'0' * 64}\n"
    "    normative_change: true\n"
)

#: What every `--task consolidation` call must say out loud. The verb appends a
#: session to a transcript `status.json` already describes, which the sweep's
#: own launcher refuses to do, so the tests below carry the acknowledgment
#: rather than each restating why it is there.
ACKNOWLEDGE = "--append-to-finished-run"


def _consolidate(campaign_dir: Path, *only: str, acknowledge: bool = True) -> int:
    """Run one consolidation round through the parser.

    Args:
        campaign_dir: The campaign directory.
        only: Run ids for ``--only``; none omits the flag entirely.
        acknowledge: Whether to pass the append acknowledgment.

    Returns:
        The command's exit code.
    """
    return cli.main(
        [
            "run",
            str(campaign_dir),
            "--task",
            "consolidation",
            *(["--only", ",".join(only)] if only else []),
            *([ACKNOWLEDGE] if acknowledge else []),
        ]
    )


def test_campaign_init_takes_a_consolidation_interval(
    tmp_path, pristine, panther_repo, capsys, toolchain_record
):
    """The cadence is a property of the campaign, so it is frozen with it."""
    _, _, campaign_dir = _init(
        tmp_path,
        pristine,
        panther_repo,
        capsys,
        toolchain_record,
        "--consolidate-every",
        "3",
    )

    frozen = json.loads((campaign_dir / "campaign.json").read_text())
    assert frozen["consolidate_every"] == 3


def test_the_consolidation_interval_defaults_to_recon_yamls_own(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """One default for the interval, so an operator's and a campaign's agree.

    Two halves, because comparing the two numbers while they happen to agree
    would pass against a parser that restated the literal. The first reaches
    the operator-facing value through the config loader; the second moves the
    source and requires a campaign frozen with no flag to follow it.
    """
    from ai_rfc.config import load_config

    recon = _recon(tmp_path)
    recon.write_text(recon.read_text() + "sessions:\n  budget_usd: 1.0\n")
    assert (
        load_config(recon).sessions.consolidate_every == cli.DEFAULT_CONSOLIDATE_EVERY
    )

    monkeypatch.setattr(cli, "DEFAULT_CONSOLIDATE_EVERY", 7)
    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )

    frozen = json.loads((campaign_dir / "campaign.json").read_text())
    assert frozen["consolidate_every"] == 7


def test_a_negative_consolidation_interval_is_refused(
    tmp_path, pristine, panther_repo, capsys, toolchain_record
):
    """A minus sign inverts the flag's meaning instead of narrowing it.

    The schedule asks whether the clusters since the last round reach the
    interval, so any negative value is reached by the first cluster and buys an
    editorial pass after every one of them. recon.yaml's loader already refuses
    a negative integer; the flag says the same thing rather than less.

    The refusal is read off the message, not only off the code: a parser that
    does not carry the flag at all also exits 2, and would satisfy a test that
    asked no more than that.
    """
    with pytest.raises(SystemExit) as raised:
        _init(
            tmp_path,
            pristine,
            panther_repo,
            capsys,
            toolchain_record,
            "--consolidate-every",
            "-1",
        )

    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "--consolidate-every" in err and "non-negative" in err


def test_one_consolidation_runs_against_a_finished_workspace(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """One paid round on a finished copy, with no sweep around it.

    ``at_end`` is what the round is asked with even here: a consolidation run
    by hand consolidates whatever the workspace still has outstanding, which is
    not a question the interval answers.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "A1", ONE_UNCONSOLIDATED_CLUSTER)
    seen: dict = {}

    def fake_consolidation(campaign, ref, due, **kwargs):
        seen["at_end"] = kwargs["at_end"]
        seen["ordinal"] = due.ordinal
        seen["base"] = due.base_cluster
        seen["run_id"] = ref.run_id
        return True, False

    monkeypatch.setattr(per_cluster, "_run_consolidation", fake_consolidation)

    code = _consolidate(campaign_dir, "A1")

    assert code == 0
    assert seen == {"at_end": True, "ordinal": 1, "base": "c1", "run_id": "A1"}


def test_a_manual_consolidation_that_recorded_nothing_exits_nonzero(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """The operator paid for a round; whether it landed is the exit code."""
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "A1", ONE_UNCONSOLIDATED_CLUSTER)
    monkeypatch.setattr(
        per_cluster, "_run_consolidation", lambda *_a, **_k: (False, False)
    )

    assert _consolidate(campaign_dir, "A1") == 1


def test_a_manual_consolidation_launches_nothing_when_none_is_due(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """A revisions map that will not scan answers None rather than raising.

    The schedule swallows a malformed document on purpose — it is the gate's
    finding, not a reason to schedule an editorial pass over it. Outside a
    sweep that same None must stop a session the operator is paying for,
    instead of launching one with nothing to tell it what to consolidate.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "A1", "revisions: [\n")

    def refuse(*_args, **_kwargs):
        raise AssertionError("no round is due; nothing may be launched")

    monkeypatch.setattr(per_cluster, "_run_consolidation", refuse)

    assert _consolidate(campaign_dir, "A1") == 1
    assert "nothing to consolidate" in capsys.readouterr().err


def test_arm_c_is_refused_a_consolidation_by_hand_too(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """The sweep declines C's rounds, and a flag must not route around that.

    Nothing downstream would refuse it: ``consolidation-C.md`` is rendered like
    every other arm's prompt, so the round would launch, and spend, on an arm
    whose frozen tool surface leaves every command of it missing.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "C1", ONE_UNCONSOLIDATED_CLUSTER)

    def refuse(*_args, **_kwargs):
        raise AssertionError("arm C must not launch a consolidation")

    monkeypatch.setattr(per_cluster, "_run_consolidation", refuse)

    assert _consolidate(campaign_dir, "C1") == 1
    assert "arm C" in capsys.readouterr().err


def test_an_arm_the_verb_refuses_is_never_sent_to_fetch_a_flag_first(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """The refusal an operator cannot argue with comes before the one they can.

    Ordered the other way, an arm C operator is told to add a flag whose own
    message calls it dangerous, adds it, and is then refused for a reason that
    had nothing to do with the flag — having been walked through accepting a
    risk that was never on the table.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "C1", ONE_UNCONSOLIDATED_CLUSTER)

    def refuse(*_args, **_kwargs):
        raise AssertionError("arm C must not launch a consolidation")

    monkeypatch.setattr(per_cluster, "_run_consolidation", refuse)

    assert _consolidate(campaign_dir, "C1", acknowledge=False) == 1
    err = capsys.readouterr().err
    assert "arm C" in err and ACKNOWLEDGE not in err


def test_a_zero_consolidation_interval_survives_into_the_campaign(
    tmp_path, pristine, panther_repo, capsys, toolchain_record
):
    """0 is a value, not an absence: it keeps the sweep-end round and no other.

    Worth its own assertion because 0 is the one setting a falsy-default
    shortcut would silently rewrite, and every other test on this flag would
    stay green while it did.
    """
    _, _, campaign_dir = _init(
        tmp_path,
        pristine,
        panther_repo,
        capsys,
        toolchain_record,
        "--consolidate-every",
        "0",
    )

    frozen = json.loads((campaign_dir / "campaign.json").read_text())
    assert frozen["consolidate_every"] == 0


def test_a_manual_consolidation_is_refused_without_the_acknowledgment(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """Appending to a finished run crosses an invariant, so it is said out loud.

    The launcher refuses to relaunch a run in place, and a run directory exists
    only because it launched once. This verb appends a session anyway, leaving
    `status.json` describing a prefix of `events.jsonl` — defensible, but not
    something to do silently, since the audit reads both.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "A1", ONE_UNCONSOLIDATED_CLUSTER)

    def refuse(*_args, **_kwargs):
        raise AssertionError("the append was not acknowledged; nothing may run")

    monkeypatch.setattr(per_cluster, "_run_consolidation", refuse)

    assert _consolidate(campaign_dir, "A1", acknowledge=False) == 1
    err = capsys.readouterr().err
    assert ACKNOWLEDGE in err and "status.json" in err


def test_a_manual_consolidation_records_that_it_extended_the_transcript(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """A reader of the run must be able to tell the extension was deliberate.

    `status.json` is written once and never revised, so after this verb it
    describes a prefix of the transcript. The marker says where that prefix
    ends and what was appended past it.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    workspace = _finished_run(campaign_dir, "A1", ONE_UNCONSOLIDATED_CLUSTER)
    (workspace.parent / "events.jsonl").write_text('{"a": 1}\n{"b": 2}\n')
    monkeypatch.setattr(
        per_cluster, "_run_consolidation", lambda *_a, **_k: (True, False)
    )

    assert _consolidate(campaign_dir, "A1") == 0

    appended = [
        json.loads(line)
        for line in (workspace.parent / "appended.jsonl").read_text().splitlines()
    ]
    assert len(appended) == 1
    assert appended[0]["kind"] == "consolidation"
    assert appended[0]["ordinal"] == 1
    assert appended[0]["base_cluster"] == "c1"
    assert appended[0]["events_lines_before"] == 2
    assert appended[0]["recorded"] is True
    assert appended[0]["timed_out"] is False
    assert appended[0]["appended_at"]


def test_a_manual_consolidation_needs_exactly_one_run(
    tmp_path, pristine, panther_repo, capsys, toolchain_record, monkeypatch
):
    """The round edits one workspace, and --only is the only thing that says which.

    Both shapes, because they fail for opposite reasons: with no --only the
    sweep's default is every run in the frozen order, and with two ids there is
    no answer to which workspace the single round belongs to.
    """
    from ai_rfc.experiment import per_cluster

    _, _, campaign_dir = _init(
        tmp_path, pristine, panther_repo, capsys, toolchain_record
    )
    _finished_run(campaign_dir, "A1", ONE_UNCONSOLIDATED_CLUSTER)

    def refuse(*_args, **_kwargs):
        raise AssertionError("no single run was named; nothing may run")

    monkeypatch.setattr(per_cluster, "_run_consolidation", refuse)

    assert _consolidate(campaign_dir) == 1
    assert "--only" in capsys.readouterr().err

    assert _consolidate(campaign_dir, "A1", "B1") == 1
    assert "--only" in capsys.readouterr().err
