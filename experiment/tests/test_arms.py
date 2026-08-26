import pytest

from experiment import ExperimentError
from experiment.arms import (
    ARMS,
    PROFILES,
    arm_flags,
    build_argv,
    constant_flags,
    mcp_config,
    profile,
)


def _value(flags: list[str], name: str) -> str:
    return flags[flags.index(name) + 1]


def test_arm_a_has_no_bash_and_mounts_mcp(tmp_path):
    mcp_config_path = tmp_path / "arfc.json"
    flags = arm_flags(profile("A"), mcp_config_path)
    assert _value(flags, "--tools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
    ]
    assert _value(flags, "--allowedTools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "mcp__arfc",
    ]
    assert _value(flags, "--mcp-config") == str(mcp_config_path)
    assert "--strict-mcp-config" in flags

    argv = build_argv(
        claude_bin="claude",
        prompt="go",
        arm_profile=profile("A"),
        mcp_config_path=mcp_config_path,
        model="m",
        effort="high",
        budget_usd=1,
        prompt_file=tmp_path / "p",
    )
    assert argv[:3] == ["claude", "-p", "go"]
    assert _value(argv, "--tools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
    ]
    assert _value(argv, "--allowedTools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "mcp__arfc",
    ]
    assert _value(argv, "--mcp-config") == str(mcp_config_path)


def test_arms_b_and_c_allow_exactly_their_command_family():
    b = arm_flags(profile("B"), None)
    c = arm_flags(profile("C"), None)
    assert _value(b, "--tools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "Bash",
    ]
    assert _value(b, "--allowedTools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "Bash(arfc *)",
    ]
    assert _value(c, "--tools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "Bash",
    ]
    assert _value(c, "--allowedTools").split(",") == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "Bash(python -m panther.plugins.services.testers.a_rfc*)",
        "Bash(git *)",
        "Bash(sqlite3 *)",
    ]
    for flags in (b, c):
        assert "--mcp-config" not in flags and "--strict-mcp-config" in flags


def test_mcp_mount_is_required_and_refused_per_arm(tmp_path):
    with pytest.raises(ExperimentError):
        arm_flags(profile("A"), None)
    with pytest.raises(ExperimentError):
        arm_flags(profile("B"), tmp_path / "x.json")
    with pytest.raises(ExperimentError):
        profile("D")
    assert ARMS == ("A", "B", "C")
    assert set(PROFILES) == set(ARMS)


def test_constant_flags_pin_the_harness(tmp_path):
    flags = constant_flags(
        model="claude-opus-5", effort="high", budget_usd=25, prompt_file=tmp_path / "p"
    )
    assert _value(flags, "--model") == "claude-opus-5"
    assert _value(flags, "--effort") == "high"
    assert _value(flags, "--permission-mode") == "dontAsk"
    assert _value(flags, "--max-budget-usd") == "25"
    assert _value(flags, "--setting-sources") == "project"
    assert _value(flags, "--output-format") == "stream-json"
    assert "--disable-slash-commands" in flags and "--verbose" in flags
    assert _value(flags, "--append-system-prompt-file") == str(tmp_path / "p")


def test_mcp_config_uses_absolute_paths(tmp_path):
    config = mcp_config(
        python="/venv/bin/python",
        server_src=tmp_path / "src",
        panther_repo=tmp_path / "W",
        workspace=tmp_path / "ws",
    )
    server = config["mcpServers"]["arfc"]
    assert server["command"] == "/venv/bin/python"
    assert server["args"][0] == "-c" and str(tmp_path / "src") in server["args"][1]
    assert server["env"] == {
        "PANTHER_REPO": str(tmp_path / "W"),
        "ARFC_WORKSPACE": str(tmp_path / "ws"),
    }


def test_build_argv_starts_with_print_mode(tmp_path):
    argv = build_argv(
        claude_bin="claude",
        prompt="go",
        arm_profile=profile("B"),
        mcp_config_path=None,
        model="m",
        effort="high",
        budget_usd=1,
        prompt_file=tmp_path / "p",
    )
    assert argv[:3] == ["claude", "-p", "go"]
    assert "--tools" in argv and "--allowedTools" in argv
