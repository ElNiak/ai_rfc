import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiment.arms import profile
from experiment.enforcement import (
    bash_families,
    command_segments,
    is_allowed,
    render_settings,
)

GUARD = Path(__file__).resolve().parents[1] / "guard.py"


def test_families_come_from_each_arm_allowlist():
    assert bash_families(profile("A")) == ()
    assert bash_families(profile("B")) == ("arfc ",)
    assert bash_families(profile("C")) == (
        "python -m panther.plugins.services.testers.a_rfc",
        "git ",
        "sqlite3 ",
    )


def test_segments_split_on_every_control_operator():
    assert command_segments("arfc status && echo x") == ["arfc status", "echo x"]
    assert command_segments("a; b | c || d & e") == ["a", "b", "c", "d", "e"]
    assert command_segments("   ") == []


@pytest.mark.parametrize(
    "command",
    [
        "arfc status",
        "arfc claim upsert --id 1",
        "arfc status > out.txt",
    ],
)
def test_in_family_commands_are_allowed(command):
    assert is_allowed(command, ("arfc ",)) is True


@pytest.mark.parametrize(
    "command",
    [
        "echo bypass-probe",
        "arfc status && echo bypass-probe",
        "arfc status; rm -rf /",
        "arfc status | sh",
        "echo $(arfc status)",
        "echo `arfc status`",
        "arfc status && echo ${HOME}",
        "",
    ],
)
def test_out_of_family_and_substitution_are_blocked(command):
    assert is_allowed(command, ("arfc ",)) is False


def test_an_arm_without_families_allows_nothing():
    assert is_allowed("arfc status", ()) is False


def test_settings_use_the_nested_hook_shape():
    document = render_settings(
        python="/venv/bin/python", guard=Path("/g/guard.py"), families=("arfc ",)
    )
    assert set(document) == {"hooks"}
    entry = document["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    hook = entry["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"].startswith("/venv/bin/python /g/guard.py ")
    assert "'arfc '" in hook["command"]


def _run_guard(command: str, *families: str) -> int:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(GUARD), *families],
        input=payload,
        capture_output=True,
        text=True,
    ).returncode


def test_guard_exits_zero_only_for_an_in_family_command():
    assert _run_guard("arfc status", "arfc ") == 0
    assert _run_guard("echo bypass-probe", "arfc ") == 2
    assert _run_guard("arfc status && echo x", "arfc ") == 2


def test_guard_blocks_an_unreadable_payload():
    result = subprocess.run(
        [sys.executable, str(GUARD), "arfc "],
        input="not json",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
