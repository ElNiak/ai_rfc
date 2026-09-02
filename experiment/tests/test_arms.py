import importlib.util
import sys

import pytest

from experiment import ExperimentError
from experiment.arms import (
    ARMS,
    PROFILES,
    RAW_PREFIX,
    RAW_SUBSTRATE,
    arm_flags,
    arm_profile,
    claude_argv,
    mcp_config,
    shared_flags,
)

from .conftest import PANTHER_ROOT


def test_the_raw_prefix_names_a_package_that_exists():
    """Arm C's classifier matches recorded transcripts against this literal.

    A rename of the substrate package that misses it does not raise: every
    legitimate call reclassifies as bash:other, which reads as an integrity
    violation. Pinning it to an importable module makes the rename fail loudly.

    The PANTHER root is resolved from this file rather than relied on from an
    ambient sys.path: this repository is a submodule that must also collect
    standalone, and pytest's rootdir walk only reaches PANTHER when the clone
    happens to sit inside one.
    """
    module = RAW_PREFIX.rsplit(" ", 1)[-1]
    if str(PANTHER_ROOT) not in sys.path:
        sys.path.insert(0, str(PANTHER_ROOT))

    assert importlib.util.find_spec(module) is not None


def test_the_arm_c_substrate_is_derived_from_the_prefix():
    assert RAW_SUBSTRATE == f"Bash({RAW_PREFIX}*)"


def test_only_the_current_substrate_path_classifies():
    """One spelling, no legacy branch.

    The pilot's recorded transcripts name the old package, so `experiment
    audit` can no longer re-derive that campaign's metrics — an accepted,
    documented cost of carrying a single version rather than a compat layer.
    """
    from experiment.audit import _stage_surface

    assert _stage_surface(f"{RAW_PREFIX}.draft gate x") == "bash:python_ai_rfc"
    # Deliberately the pre-rename spelling. A blanket rename that rewrites this
    # literal makes the test assert nothing, so it is spelled by concatenation.
    stale = "python -m panther.plugins.services.testers." + "a_rfc.draft gate x"
    assert _stage_surface(stale) == "bash:other"


def _value(flags: list[str], name: str) -> str:
    return flags[flags.index(name) + 1]


def test_arm_a_has_no_bash_and_mounts_mcp(tmp_path):
    mcp_config_path = tmp_path / "ai_rfc.json"
    flags = arm_flags(arm_profile("A"), mcp_config_path)
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
        "mcp__ai_rfc",
    ]
    assert _value(flags, "--mcp-config") == str(mcp_config_path)
    assert "--strict-mcp-config" in flags

    argv = claude_argv(
        claude_bin="claude",
        prompt="go",
        this_arm=arm_profile("A"),
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
        "mcp__ai_rfc",
    ]
    assert _value(argv, "--mcp-config") == str(mcp_config_path)


def test_arms_b_and_c_allow_exactly_their_command_family():
    b = arm_flags(arm_profile("B"), None)
    c = arm_flags(arm_profile("C"), None)
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
        "Bash(ai_rfc *)",
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
        "Bash(python -m panther.plugins.services.testers.ai_rfc*)",
        "Bash(git *)",
        "Bash(sqlite3 *)",
    ]
    for flags in (b, c):
        assert "--mcp-config" not in flags and "--strict-mcp-config" in flags


def test_mcp_mount_is_required_and_refused_per_arm(tmp_path):
    with pytest.raises(ExperimentError):
        arm_flags(arm_profile("A"), None)
    with pytest.raises(ExperimentError):
        arm_flags(arm_profile("B"), tmp_path / "x.json")
    with pytest.raises(ExperimentError):
        arm_profile("D")
    assert ARMS == ("A", "B", "C")
    assert set(PROFILES) == set(ARMS)


def test_shared_flags_pin_the_harness(tmp_path):
    flags = shared_flags(
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
    server = config["mcpServers"]["ai_rfc"]
    assert server["command"] == "/venv/bin/python"
    assert server["args"][0] == "-c" and str(tmp_path / "src") in server["args"][1]
    assert server["env"] == {
        "PANTHER_REPO": str(tmp_path / "W"),
        "AI_RFC_WORKSPACE": str(tmp_path / "ws"),
    }


def test_claude_argv_starts_with_print_mode(tmp_path):
    argv = claude_argv(
        claude_bin="claude",
        prompt="go",
        this_arm=arm_profile("B"),
        mcp_config_path=None,
        model="m",
        effort="high",
        budget_usd=1,
        prompt_file=tmp_path / "p",
    )
    assert argv[:3] == ["claude", "-p", "go"]
    assert "--tools" in argv and "--allowedTools" in argv


def test_hook_events_are_always_streamed(tmp_path):
    flags = shared_flags(
        model="m", effort="high", budget_usd=1, prompt_file=tmp_path / "p"
    )
    assert "--include-hook-events" in flags
    assert _value(flags, "--output-format") == "stream-json"


def test_the_guard_mounts_only_when_a_settings_path_is_given(tmp_path):
    guard = tmp_path / "B.json"
    assert "--settings" not in arm_flags(arm_profile("B"), None)
    assert _value(arm_flags(arm_profile("B"), None, guard), "--settings") == str(guard)

    argv = claude_argv(
        claude_bin="claude",
        prompt="go",
        this_arm=arm_profile("B"),
        mcp_config_path=None,
        model="m",
        effort="high",
        budget_usd=1,
        prompt_file=tmp_path / "p",
        guard_settings=guard,
    )
    assert _value(argv, "--settings") == str(guard)
