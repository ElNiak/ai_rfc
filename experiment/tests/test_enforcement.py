import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiment.arms import arm_profile
from experiment.enforcement import (
    bash_prefixes,
    command_groups,
    is_allowed,
    render_settings,
)

GUARD = Path(__file__).resolve().parents[1] / "guard.py"


def test_prefixes_come_from_each_arm_allowlist():
    assert bash_prefixes(arm_profile("A")) == ()
    assert bash_prefixes(arm_profile("B")) == ("arfc ",)
    assert bash_prefixes(arm_profile("C")) == (
        "python -m panther.plugins.services.testers.a_rfc",
        "git ",
        "sqlite3 ",
    )


def test_segments_split_on_every_control_operator():
    assert command_groups("arfc status && echo x") == [["arfc status"], ["echo x"]]
    assert command_groups("a; b | c || d & e") == [["a"], ["b", "c"], ["d"], ["e"]]
    assert command_groups("   ") == []
    # A backslash-newline continues one command; it does not start a second.
    (continued,) = command_groups("arfc up x \\\n  --text y")
    assert len(continued) == 1
    assert continued[0].split() == ["arfc", "up", "x", "--text", "y"]
    # 2>&1 and &> redirect; they do not run anything.
    assert command_groups("arfc x 2>&1") == [["arfc x 2>&1"]]
    assert command_groups("arfc x &>log") == [["arfc x &>log"]]


@pytest.mark.parametrize(
    "command",
    [
        "arfc status",
        "arfc claim upsert --id 1",
        "arfc status > out.txt",
    ],
)
def test_in_prefix_commands_are_allowed(command):
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


def test_an_arm_without_prefixes_allows_nothing():
    assert is_allowed("arfc status", ()) is False


def test_settings_use_the_nested_hook_shape():
    document = render_settings(
        python="/venv/bin/python", guard=Path("/g/guard.py"), prefixes=("arfc ",)
    )
    assert set(document) == {"hooks"}
    entry = document["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    hook = entry["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"].startswith("/venv/bin/python /g/guard.py ")
    assert "'arfc '" in hook["command"]


def _run_guard_raw(payload: str, *prefixes: str) -> int:
    return subprocess.run(
        [sys.executable, str(GUARD), *prefixes],
        input=payload,
        capture_output=True,
        text=True,
    ).returncode


def _run_guard(command: str, *prefixes: str) -> int:
    return _run_guard_raw(json.dumps({"tool_input": {"command": command}}), *prefixes)


def test_guard_exits_zero_only_for_an_in_prefix_command():
    assert _run_guard("arfc status", "arfc ") == 0
    assert _run_guard("echo bypass-probe", "arfc ") == 2
    assert _run_guard("arfc status && echo x", "arfc ") == 2


def test_guard_blocks_an_unreadable_payload():
    assert _run_guard_raw("not json", "arfc ") == 2


def test_guard_blocks_a_payload_that_parses_but_is_not_an_object():
    """Valid JSON that is not a mapping must block, not crash.

    Only exit 2 blocks; an uncaught exception exits 1 and the command runs.
    """
    for payload in ("5", "[1, 2]", '"echo pwned"', "null", "true"):
        assert _run_guard_raw(payload, "arfc ") == 2, payload


def test_guard_blocks_a_non_object_tool_input():
    """``tool_input`` is truthy but not a mapping, so ``or {}`` does not fire."""
    assert _run_guard_raw(json.dumps({"tool_input": "echo pwned"}), "arfc ") == 2
    assert _run_guard_raw(json.dumps({"tool_input": ["echo pwned"]}), "arfc ") == 2


def test_a_continued_command_is_one_command():
    """Observed live in pilot run B1: a multi-line claim-upsert was refused."""
    command = (
        'arfc claim-upsert pkg.1 \\\n  --text "A conforming distribution MUST ship it"'
    )
    assert is_allowed(command, ("arfc ",))
    # The continuation must not launder a second command past the check.
    assert not is_allowed("arfc status \\\n && echo bypass-probe", ("arfc ",))
    assert not is_allowed("arfc status \\\n ; echo bypass-probe", ("arfc ",))


def test_an_in_prefix_command_may_page_its_own_output():
    """Also observed in B1: `arfc cluster-get --patch ... 2>&1 | head` was refused."""
    prefixes = ("arfc ",)
    assert is_allowed("arfc cluster-get c --patch 2>&1 | head -c 20000", prefixes)
    assert is_allowed("arfc status | head -20", prefixes)
    assert is_allowed("arfc status | tail -5 | wc -l", prefixes)
    assert is_allowed("arfc status > out.txt", prefixes)
    # Only the pagers, and only after something in family.
    assert not is_allowed("arfc status | sh", prefixes)
    assert not is_allowed("arfc status | tee /tmp/x", prefixes)
    assert not is_allowed("echo bypass | head", prefixes)
