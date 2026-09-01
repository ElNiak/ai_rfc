import json
import os
import subprocess
import sys
from pathlib import Path

from experiment.stream import (
    denials,
    parse_stream,
    result_event,
    tool_results,
    tool_uses,
)
from experiment.workspace import copy_workspace

from .conftest import FAKE_CLAUDE


def _launch(profile: Path, workspace: Path, panther_repo: Path, *argv: str):
    env = {
        "CLAUDE_CONFIG_DIR": str(profile),
        "PANTHER_REPO": str(panther_repo),
        "AI_RFC_WORKSPACE": str(workspace),
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
    }
    completed = subprocess.run(
        [str(FAKE_CLAUDE), "-p", "go", *argv],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stderr
    return parse_stream(completed.stdout)


def test_fake_replays_a_complete_loop_in_every_arm(
    pristine, panther_repo, tmp_path, write_scenario
):
    steps = [
        {"kind": "claim", "id": "t:3.1", "section": "3.1"},
        {"kind": "record_status"},
        {"kind": "checkpoint", "ordinal": 2},
        {"kind": "prose", "line": "Thing three MAY hold. `a_rfc:t:3.1`"},
        {
            "kind": "revision",
            "ordinal": 2,
            "tag": "draft-test-fixture-00",
            "normative": True,
        },
        {"kind": "tag", "tag": "draft-test-fixture-00"},
        {"kind": "citation_gate"},
    ]
    for arm in "ABC":
        profile = tmp_path / f"profile-{arm}"
        run_dir = tmp_path / "runs" / f"{arm}1"
        workspace = copy_workspace(pristine, run_dir / "workspace")
        write_scenario(profile, f"{arm}1", {"arm": arm, "cost": 1.25, "steps": steps})
        events = _launch(
            profile,
            workspace,
            panther_repo,
            "--tools",
            "Read,Edit" + (",Bash" if arm != "A" else ""),
        )
        names = [use["name"] for use in tool_uses(events)]
        assert any(
            p.name.startswith("c0002-") and not (p / "harness.json").exists()
            for p in (workspace / "checkpoints").iterdir()
        ), arm
        tags = subprocess.run(
            ["git", "-C", str(workspace / "draft"), "tag", "-l"],
            capture_output=True,
            text=True,
        ).stdout.split()
        assert tags == ["draft-test-fixture-00"], arm
        final = result_event(events)
        assert final["total_cost_usd"] == 1.25 and final["num_turns"] == len(names)
        if arm == "A":
            assert all(n.startswith("mcp__arfc__") or n == "Edit" for n in names), names
        elif arm == "B":
            assert any(n == "Bash" for n in names) and not any(
                n.startswith("mcp__") for n in names
            )
        else:
            assert "Edit" in names and any(
                "python -m panther" in use["input"].get("command", "")
                for use in tool_uses(events)
            )
        calls = json.loads((profile / "fake-calls" / f"{arm}1.json").read_text())
        assert calls["cwd"] == str(workspace)


def test_fake_records_denials_and_exit_codes(
    pristine, panther_repo, tmp_path, write_scenario
):
    profile = tmp_path / "profile"
    workspace = copy_workspace(pristine, tmp_path / "runs" / "A1" / "workspace")
    write_scenario(
        profile,
        "A1",
        {
            "arm": "A",
            "exit_code": 0,
            "steps": [
                {"kind": "denied", "command": "arfc status"},
                {"kind": "mcp_denied"},
            ],
        },
    )
    events = _launch(profile, workspace, panther_repo)
    assert len(denials(events)) == 4
    first = result_event(events)["permission_denials"][0]
    assert first["tool_input"] == {"command": "arfc status"}
    # The shape the guard really produces: hook events bracket the refused call,
    # and the denial names the call it refused.
    hooks = [e for e in events if str(e.get("subtype", "")).startswith("hook_")]
    assert [e["subtype"] for e in hooks] == ["hook_started", "hook_response"]
    assert all(e["hook_event"] == "PreToolUse" for e in hooks)
    bash_call = next(u for u in tool_uses(events) if u["name"] == "Bash")
    assert first["tool_use_id"] == bash_call["id"]
    text = tool_results(events)[bash_call["id"]]["text"]
    assert text.startswith("PreToolUse:Bash hook error:")
    assert "refused: arfc status" in text


def test_fake_answers_version():
    completed = subprocess.run(
        [str(FAKE_CLAUDE), "--version"], capture_output=True, text=True
    )
    assert completed.stdout.strip() == "fake-claude 0.0.0"
