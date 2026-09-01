import json
import os
import sys
from pathlib import Path

from experiment.preflight import (
    CHECKS,
    CLAUDE_MD_CANARY,
    Invocation,
    build_invocations,
    evaluate,
    run_invocation,
)
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
        "denial_control",
        "append_prompt",
    ]
    assert len(CHECKS) == 9


def test_isolated_invocations_use_the_profile_and_controls_do_not(tmp_path):
    by_name = {inv.name: inv for inv in _invocations(tmp_path)}
    profile = str(tmp_path / "root" / "profile")
    assert by_name["auth"].env["CLAUDE_CONFIG_DIR"] == profile
    # The positive control is isolated like every other invocation and carries
    # its own always-allow hook, so nothing in the spike reads the real config.
    control = by_name["hooks_control"]
    assert control.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "root" / "profile")
    assert control.cwd == tmp_path / "root" / "spike" / "cwd"
    assert control.argv[control.argv.index("--settings") + 1].endswith(
        "guard-allow.json"
    )
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
    assert "Bash(ai_rfc *)" in b[b.index("--allowedTools") + 1]
    assert "--disable-slash-commands" in a and "--disable-slash-commands" in b


def _ran_in_prefix():
    return _outcome(
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": False,
                            "content": "git version 2.0",
                        }
                    ]
                },
            }
        ]
    )


def test_evaluate_denial_check_on_fixture(tmp_path):
    events = parse_stream((FIXTURES / "denied-bash.jsonl").read_text())
    outcomes = {"denial": _outcome(events), "denial_control": _ran_in_prefix()}
    checks = {c["check"]: c for c in evaluate(outcomes, tmp_path)}
    assert checks["denial"]["passed"] is True

    leaked = json.loads(json.dumps(events))
    for event in leaked:
        if event.get("type") == "user":
            block = event["message"]["content"][0]
            block["is_error"] = False
            block["content"] = "bypass-probe"
        if event.get("type") == "result":
            event["permission_denials"] = []
    checks = {
        c["check"]: c
        for c in evaluate(
            {"denial": _outcome(leaked), "denial_control": _ran_in_prefix()}, tmp_path
        )
    }
    assert checks["denial"]["passed"] is False


def test_a_guard_that_blocks_its_own_family_is_not_enforcement(tmp_path):
    events = parse_stream((FIXTURES / "denied-bash.jsonl").read_text())
    over_blocking = _outcome([])
    checks = {
        c["check"]: c
        for c in evaluate(
            {"denial": _outcome(events), "denial_control": over_blocking}, tmp_path
        )
    }
    assert checks["denial"]["passed"] is False
    assert checks["denial"]["evidence"]["in_family_ran"] is False


def test_evaluate_marks_missing_outcomes_failed(tmp_path):
    checks = {c["check"]: c for c in evaluate({}, tmp_path)}
    assert set(checks) == set(CHECKS)
    assert all(check["passed"] is False for check in checks.values())
    assert (
        checks["auth"]["required"] is True
        and checks["draft_commit"]["required"] is False
    )


def test_hooks_and_claude_md_require_their_positive_controls(tmp_path):
    no_hook_events = {"exit_code": 0, "events": [], "stderr": "", "timed_out": False}
    outcomes = {"hooks_isolated": no_hook_events, "hooks_control": no_hook_events}
    checks = {c["check"]: c for c in evaluate(outcomes, tmp_path)}
    assert checks["hooks"]["passed"] is False

    outcomes["hooks_control"] = {
        "exit_code": 0,
        "events": [
            {
                "type": "system",
                "subtype": "hook_started",
                "hook_event": "PreToolUse",
                "hook_name": "guard",
            }
        ],
        "stderr": "",
        "timed_out": False,
    }
    checks = {c["check"]: c for c in evaluate(outcomes, tmp_path)}
    assert checks["hooks"]["passed"] is True

    def result_outcome(answer):
        return {
            "exit_code": 0,
            "events": [{"type": "result", "result": answer}],
            "stderr": "",
            "timed_out": False,
        }

    outcomes = {
        "claude_md_isolated": result_outcome("NONE"),
        "claude_md_control": result_outcome("NONE"),
    }
    checks = {c["check"]: c for c in evaluate(outcomes, tmp_path)}
    assert checks["claude_md"]["passed"] is False

    outcomes["claude_md_control"] = result_outcome(CLAUDE_MD_CANARY)
    checks = {c["check"]: c for c in evaluate(outcomes, tmp_path)}
    assert checks["claude_md"]["passed"] is True


def test_run_invocation_timeout_degrades_gracefully(tmp_path):
    invocation = Invocation(
        name="timeout_probe",
        argv=(
            sys.executable,
            "-c",
            'import sys, time; sys.stdout.write(\'{"type":"system","subtype":"init"}\\n{"type":"assis\'); sys.stdout.flush(); sys.stderr.write(\'warn\\n\'); sys.stderr.flush(); time.sleep(30)',
        ),
        env={"PATH": os.environ["PATH"]},
        cwd=tmp_path,
    )
    outcome = run_invocation(invocation, timeout_s=1)
    assert outcome["timed_out"] is True
    assert outcome["exit_code"] is None
    assert isinstance(outcome["stderr"], str)
    assert "warn" in outcome["stderr"]
    assert outcome["events"] == []
    json.dumps(outcome)
