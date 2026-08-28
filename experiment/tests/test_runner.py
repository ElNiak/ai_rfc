import dataclasses
import json
import shlex
import time
from pathlib import Path

import pytest

from experiment import ExperimentError
from experiment.runner import (
    EVENTS_FILE,
    GUARD_FILE,
    RESULT_FILE,
    build_env,
    build_run_argv,
    launch,
    load_status,
    run_spec,
)
from experiment.workspace import copy_workspace

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE


def _ready(campaign, run_id):
    spec = run_spec(campaign, run_id)
    spec.run_dir.mkdir(parents=True)
    copy_workspace(campaign.pristine_dir, spec.workspace)
    return spec


def test_launch_streams_events_and_records_status(campaign, write_scenario):
    spec = _ready(campaign, "A1")
    write_scenario(
        campaign.profile_dir, "A1", {"arm": "A", "cost": 1.25, "steps": COMPLETE_STEPS}
    )
    status = launch(campaign, spec)
    assert status.complete and status.exit_code == 0 and not status.timed_out
    assert status.claude_version == "fake-claude 0.0.0"
    events = (spec.run_dir / EVENTS_FILE).read_text().splitlines()
    assert json.loads(events[0])["subtype"] == "init"
    assert (
        json.loads((spec.run_dir / RESULT_FILE).read_text())["total_cost_usd"] == 1.25
    )
    assert load_status(spec.run_dir) == status
    argv = json.loads((spec.run_dir / "argv.json").read_text())
    assert argv[:2] == [str(FAKE_CLAUDE), "-p"]
    assert "--append-system-prompt-file" in argv
    env = json.loads((spec.run_dir / "env.json").read_text())
    assert env["PATH"].startswith(str(campaign.bin_dir))
    assert env["ARFC_WORKSPACE"] == str(spec.workspace)
    assert set(env) == {
        "CLAUDE_CONFIG_DIR",
        "PANTHER_REPO",
        "ARFC_WORKSPACE",
        "PATH",
        "HOME",
        "USER",
        "LANG",
    }
    prompt = (spec.run_dir / "prompt.md").read_text()
    assert "arfc_cluster_next" in prompt and "ordinals 2 through 2" in prompt
    calls = json.loads((campaign.profile_dir / "fake-calls" / "A1.json").read_text())
    assert calls["cwd"] == str(spec.workspace)
    assert any(
        p.name.startswith("c0002-") and not (p / "harness.json").exists()
        for p in (spec.workspace / "checkpoints").iterdir()
    )


def test_arm_a_mounts_mcp_and_has_no_bash(campaign):
    spec_a = _ready(campaign, "A1")
    argv = build_run_argv(campaign, spec_a)
    assert "--mcp-config" in argv and (spec_a.run_dir / "arfc.json").exists()
    assert "Bash" not in argv[argv.index("--tools") + 1].split(",")
    spec_b = _ready(campaign, "B1")
    argv_b = build_run_argv(campaign, spec_b)
    assert "--mcp-config" not in argv_b
    assert "Bash(arfc *)" in argv_b[argv_b.index("--allowedTools") + 1]
    assert build_env(campaign, spec_b)["CLAUDE_CONFIG_DIR"] == str(campaign.profile_dir)


def test_every_run_mounts_its_arms_guard(campaign):
    """Without this the arms are capability-identical: --allowedTools does not
    confine a built-in tool (spike S0, CLI 2.1.247)."""
    expected = {
        "A1": (),
        "B1": ("arfc ",),
        "C1": (
            "python -m panther.plugins.services.testers.a_rfc",
            "git ",
            "sqlite3 ",
        ),
    }
    for run_id, families in expected.items():
        spec = _ready(campaign, run_id)
        argv = build_run_argv(campaign, spec)
        settings = Path(argv[argv.index("--settings") + 1])
        assert settings == spec.run_dir / GUARD_FILE
        hook = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Bash"
        # The hook command is a shell string, so assert on how a shell reads it:
        # a family carrying a space must survive as one argument.
        parsed = shlex.split(hook["hooks"][0]["command"])
        assert parsed[0] == campaign.python
        assert parsed[1].endswith("/guard.py") and Path(parsed[1]).exists()
        assert tuple(parsed[2:]) == families


def test_launch_times_out_and_kills_the_process_group(campaign, write_scenario):
    spec = _ready(campaign, "C1")
    write_scenario(campaign.profile_dir, "C1", {"arm": "C", "sleep": 30, "steps": []})
    short = dataclasses.replace(campaign, timeout_s=1)
    started = time.monotonic()
    status = launch(short, spec)
    assert status.timed_out and status.exit_code is None and not status.complete
    assert time.monotonic() - started < 40
    assert (spec.run_dir / RESULT_FILE).read_text() == "null\n"


def test_launch_records_a_nonzero_exit(campaign, write_scenario):
    spec = _ready(campaign, "B1")
    write_scenario(
        campaign.profile_dir, "B1", {"arm": "B", "exit_code": 3, "steps": []}
    )
    status = launch(campaign, spec)
    assert status.complete and status.exit_code == 3


def test_launch_refuses_to_relaunch(campaign, write_scenario):
    spec = _ready(campaign, "B1")
    write_scenario(campaign.profile_dir, "B1", {"arm": "B", "steps": []})
    launch(campaign, spec)
    with pytest.raises(ExperimentError):
        launch(campaign, spec)
    assert load_status(run_spec(campaign, "C1").run_dir) is None
