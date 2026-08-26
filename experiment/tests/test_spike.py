import json
from pathlib import Path

from experiment.spike import CHECKS, build_invocations, evaluate
from experiment.stream import parse_stream

FIXTURES = Path(__file__).parent / "fixtures" / "stream"


def _outcome(events, exit_code=0):
    return {"exit_code": exit_code, "events": events, "stderr": "", "timed_out": False}


def _invocations(tmp_path):
    return build_invocations(
        root=tmp_path / "root",
        panther_repo=tmp_path / "W",
        plugin_dir=tmp_path / "plugin",
        workspace=tmp_path / "ws",
        claude_bin="claude",
        model="claude-opus-5",
    )


def test_invocations_cover_every_check_in_order(tmp_path):
    names = [inv.name for inv in _invocations(tmp_path)]
    assert names == [
        "auth",
        "hooks_isolated",
        "hooks_control",
        "claude_md_control",
        "claude_md_isolated",
        "arm_surface_A",
        "arm_surface_B",
        "arm_surface_C",
        "draft_commit",
        "plugin_mcp_env",
        "plugin_mcp_noenv",
        "denial",
        "append_prompt",
    ]
    assert len(CHECKS) == 9


def test_isolated_invocations_use_the_profile_and_controls_do_not(tmp_path):
    by_name = {inv.name: inv for inv in _invocations(tmp_path)}
    profile = str(tmp_path / "root" / "profile")
    assert by_name["auth"].env["CLAUDE_CONFIG_DIR"] == profile
    assert "CLAUDE_CONFIG_DIR" not in by_name["hooks_control"].env
    assert by_name["hooks_control"].cwd == tmp_path / "W"
    assert (
        by_name["claude_md_control"].cwd
        == tmp_path / "root" / "spike" / "canary" / "sub"
    )
    assert by_name["plugin_mcp_env"].env["PANTHER_REPO"] == str(tmp_path / "W")
    assert "PANTHER_REPO" not in by_name["plugin_mcp_noenv"].env
    for name, inv in by_name.items():
        assert inv.argv[:2] == ("claude", "-p"), name
        assert "--max-budget-usd" in inv.argv, name


def test_arm_surface_invocations_carry_their_arm_flags(tmp_path):
    by_name = {inv.name: inv for inv in _invocations(tmp_path)}
    a = by_name["arm_surface_A"].argv
    assert "--mcp-config" in a and "Bash" not in a[a.index("--tools") + 1].split(",")
    b = by_name["arm_surface_B"].argv
    assert "Bash(arfc *)" in b[b.index("--allowedTools") + 1]
    assert "--disable-slash-commands" in a and "--disable-slash-commands" in b


def test_evaluate_denial_check_on_fixture(tmp_path):
    events = parse_stream((FIXTURES / "denied-bash.jsonl").read_text())
    checks = {c["check"]: c for c in evaluate({"denial": _outcome(events)}, tmp_path)}
    assert checks["denial"]["passed"] is True
    leaked = json.loads(json.dumps(events))
    leaked[2]["message"]["content"][0]["is_error"] = False
    leaked[2]["message"]["content"][0]["content"] = "bypass-probe"
    leaked[4]["permission_denials"] = []
    checks = {c["check"]: c for c in evaluate({"denial": _outcome(leaked)}, tmp_path)}
    assert checks["denial"]["passed"] is False


def test_evaluate_marks_missing_outcomes_failed(tmp_path):
    checks = {c["check"]: c for c in evaluate({}, tmp_path)}
    assert set(checks) == set(CHECKS)
    assert all(check["passed"] is False for check in checks.values())
    assert (
        checks["auth"]["required"] is True
        and checks["draft_commit"]["required"] is False
    )
