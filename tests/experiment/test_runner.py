import dataclasses
import json
import shlex
import time
from pathlib import Path

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.runner import (
    EVENTS_FILE,
    GUARD_FILE,
    RESULT_FILE,
    build_env,
    launch,
    load_status,
    prepare_run_argv,
    run_ref,
)
from ai_rfc.experiment.workspace import copy_workspace

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE


def _ready(campaign, run_id):
    ref = run_ref(campaign, run_id)
    ref.run_dir.mkdir(parents=True)
    copy_workspace(campaign.pristine_dir, ref.workspace)
    return ref


def test_launch_streams_events_and_records_status(campaign, write_scenario):
    ref = _ready(campaign, "A1")
    write_scenario(
        campaign.profile_dir, "A1", {"arm": "A", "cost": 1.25, "steps": COMPLETE_STEPS}
    )
    status = launch(campaign, ref)
    assert status.complete and status.exit_code == 0 and not status.timed_out
    assert status.claude_version == "fake-claude 0.0.0"
    events = (ref.run_dir / EVENTS_FILE).read_text().splitlines()
    assert json.loads(events[0])["subtype"] == "init"
    assert json.loads((ref.run_dir / RESULT_FILE).read_text())["total_cost_usd"] == 1.25
    assert load_status(ref.run_dir) == status
    argv = json.loads((ref.run_dir / "argv.json").read_text())
    assert argv[:2] == [str(FAKE_CLAUDE), "-p"]
    assert "--append-system-prompt-file" in argv
    env = json.loads((ref.run_dir / "env.json").read_text())
    assert env["PATH"].startswith(str(campaign.bin_dir))
    assert env["AI_RFC_WORKSPACE"] == str(ref.workspace)
    assert set(env) == {
        "CLAUDE_CONFIG_DIR",
        "AI_RFC_WORKSPACE",
        "PATH",
        "HOME",
        "USER",
        "LANG",
    }
    prompt = (ref.run_dir / "prompt.md").read_text()
    assert "ai_rfc_cluster_next" in prompt and "ordinals 2 through 2" in prompt
    calls = json.loads((campaign.profile_dir / "fake-calls" / "A1.json").read_text())
    assert calls["cwd"] == str(ref.workspace)
    assert any(
        p.name.startswith("c0002-") and not (p / "harness.json").exists()
        for p in (ref.workspace / "checkpoints").iterdir()
    )


def test_arm_a_mounts_mcp_and_has_no_bash(campaign):
    ref_a = _ready(campaign, "A1")
    argv = prepare_run_argv(campaign, ref_a)
    assert "--mcp-config" in argv and (ref_a.run_dir / "ai_rfc.json").exists()
    assert "Bash" not in argv[argv.index("--tools") + 1].split(",")
    ref_b = _ready(campaign, "B1")
    argv_b = prepare_run_argv(campaign, ref_b)
    assert "--mcp-config" not in argv_b
    assert "Bash(ai_rfc *)" in argv_b[argv_b.index("--allowedTools") + 1]
    assert build_env(campaign, ref_b)["CLAUDE_CONFIG_DIR"] == str(campaign.profile_dir)


def test_every_run_mounts_its_arms_guard(campaign):
    """Without this the arms are capability-identical: --allowedTools does not
    confine a built-in tool (spike S0, CLI 2.1.247)."""
    expected = {
        "A1": (),
        "B1": ("ai_rfc ",),
        "C1": (
            "python -m ai_rfc",
            "git ",
            "sqlite3 ",
        ),
    }
    for run_id, families in expected.items():
        ref = _ready(campaign, run_id)
        argv = prepare_run_argv(campaign, ref)
        settings = Path(argv[argv.index("--settings") + 1])
        assert settings == ref.run_dir / GUARD_FILE
        hook = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Bash"
        # The hook command is a shell string, so assert on how a shell reads it:
        # a family carrying a space must survive as one argument.
        parsed = shlex.split(hook["hooks"][0]["command"])
        assert parsed[0] == campaign.python
        assert parsed[1].endswith("/guard.py") and Path(parsed[1]).exists()
        assert tuple(parsed[2:]) == families


def test_launch_times_out_and_kills_the_process_group(campaign, write_scenario):
    ref = _ready(campaign, "C1")
    write_scenario(campaign.profile_dir, "C1", {"arm": "C", "sleep": 30, "steps": []})
    short = dataclasses.replace(campaign, timeout_s=1)
    started = time.monotonic()
    status = launch(short, ref)
    assert status.timed_out and status.exit_code is None and not status.complete
    assert time.monotonic() - started < 40
    assert (ref.run_dir / RESULT_FILE).read_text() == "null\n"


def test_launch_records_a_nonzero_exit(campaign, write_scenario):
    ref = _ready(campaign, "B1")
    write_scenario(
        campaign.profile_dir, "B1", {"arm": "B", "exit_code": 3, "steps": []}
    )
    status = launch(campaign, ref)
    assert status.complete and status.exit_code == 3


def test_launch_refuses_to_relaunch(campaign, write_scenario):
    ref = _ready(campaign, "B1")
    write_scenario(campaign.profile_dir, "B1", {"arm": "B", "steps": []})
    launch(campaign, ref)
    with pytest.raises(ExperimentError):
        launch(campaign, ref)
    assert load_status(run_ref(campaign, "C1").run_dir) is None
