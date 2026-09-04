import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from ai_rfc.draft.questions import load_questions
from ai_rfc.experiment.stream import (
    denials,
    parse_stream,
    result_event,
    tool_results,
    tool_uses,
)
from ai_rfc.experiment.workspace import copy_workspace

from .conftest import (
    FAKE_CLAUDE,
    INTERVIEW_AUTHOR,
    INTERVIEW_TRANSCRIPT,
    interview_good_steps,
    interview_trap_steps,
)

CLAIM_TEXT = "Thing three MAY hold."
QUOTE = f'Confirmed as written: "{CLAIM_TEXT}"'


def _plant_transcript(workspace: Path) -> None:
    (workspace / "interviews" / INTERVIEW_TRANSCRIPT).write_text(
        f"# Interview 001\n\n## t:3.1\n\n{QUOTE}\n"
    )


def _claim(workspace: Path) -> dict:
    requirements = yaml.safe_load((workspace / "manifest.yaml").read_text())
    return requirements["requirements"]["t:3.1"]


def _launch(profile: Path, workspace: Path, panther_repo: Path, *argv: str):
    env = {
        "CLAUDE_CONFIG_DIR": str(profile),
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
        {"kind": "prose", "line": "Thing three MAY hold. `ai_rfc:t:3.1`"},
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
            assert all(
                n.startswith("mcp__ai_rfc__") or n == "Edit" for n in names
            ), names
        elif arm == "B":
            assert any(n == "Bash" for n in names) and not any(
                n.startswith("mcp__") for n in names
            )
        else:
            assert "Edit" in names and any(
                "python -m ai_rfc" in use["input"].get("command", "")
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
                {"kind": "denied", "command": "ai_rfc status"},
                {"kind": "mcp_denied"},
            ],
        },
    )
    events = _launch(profile, workspace, panther_repo)
    assert len(denials(events)) == 4
    first = result_event(events)["permission_denials"][0]
    assert first["tool_input"] == {"command": "ai_rfc status"}
    # The shape the guard really produces: hook events bracket the refused call,
    # and the denial names the call it refused.
    hooks = [e for e in events if str(e.get("subtype", "")).startswith("hook_")]
    assert [e["subtype"] for e in hooks] == ["hook_started", "hook_response"]
    assert all(e["hook_event"] == "PreToolUse" for e in hooks)
    bash_call = next(u for u in tool_uses(events) if u["name"] == "Bash")
    assert first["tool_use_id"] == bash_call["id"]
    text = tool_results(events)[bash_call["id"]]["text"]
    assert text.startswith("PreToolUse:Bash hook error:")
    assert "refused: ai_rfc status" in text


def test_fake_replays_an_interview_into_the_register_and_the_manifest(
    pristine, panther_repo, tmp_path, write_scenario
):
    steps = [
        {"kind": "claim", "id": "t:3.1", "section": "3.1", "text": CLAIM_TEXT},
        *interview_good_steps(["t:3.1"], {"t:3.1": QUOTE}),
    ]
    for arm in "ABC":
        profile = tmp_path / f"profile-{arm}"
        workspace = copy_workspace(
            pristine, tmp_path / "runs" / f"{arm}1" / "workspace"
        )
        _plant_transcript(workspace)
        write_scenario(profile, f"{arm}1", {"arm": arm, "steps": steps})
        events = _launch(profile, workspace, panther_repo)

        names = [use["name"] for use in tool_uses(events)]
        commands = [use["input"].get("command", "") for use in tool_uses(events)]
        assert not any(
            result["is_error"] for result in tool_results(events).values()
        ), arm
        if arm == "A":
            assert "mcp__ai_rfc__ai_rfc_question_draft" in names
            assert "mcp__ai_rfc__ai_rfc_answer_record" in names
        elif arm == "B":
            assert any("ai_rfc question-draft" in c for c in commands)
            assert any("ai_rfc answer-record" in c for c in commands)
            assert not any(name.startswith("mcp__") for name in names)
        else:
            edits = [
                use["input"]["file_path"]
                for use in tool_uses(events)
                if use["name"] == "Edit"
            ]
            assert edits.count(str(workspace / "questions.yaml")) == 2, arm
            assert not any(name.startswith("mcp__") for name in names)

        entries = load_questions(workspace / "questions.yaml")
        assert [(entry.id, entry.status.value) for entry in entries] == [
            ("q-001", "answered")
        ], arm
        assert entries[0].claim_ids == ("t:3.1",)
        assert entries[0].answered_by == INTERVIEW_AUTHOR

        claim = _claim(workspace)
        assert {
            "evidence_class": "interview",
            "locator": "int-001",
        } in claim["anchors"], arm
        assert claim["signed_off_by"] == INTERVIEW_AUTHOR


def test_fake_types_a_guardrail_refusal_instead_of_crashing(
    pristine, panther_repo, tmp_path, write_scenario
):
    profile = tmp_path / "profile"
    workspace = copy_workspace(pristine, tmp_path / "runs" / "A1" / "workspace")
    _plant_transcript(workspace)
    steps = [
        {"kind": "claim", "id": "t:3.1", "section": "3.1", "text": CLAIM_TEXT},
        *interview_good_steps(["t:3.1"], {"t:3.1": ""}),
    ]
    write_scenario(profile, "A1", {"arm": "A", "steps": steps})
    events = _launch(profile, workspace, panther_repo)

    call = next(
        use
        for use in tool_uses(events)
        if use["name"] == "mcp__ai_rfc__ai_rfc_answer_record"
    )
    result = tool_results(events)[call["id"]]
    assert result["is_error"]
    assert result["text"].startswith("GuardrailError: quote must be a non-empty")
    assert result_event(events)["subtype"] == "success"

    entries = load_questions(workspace / "questions.yaml")
    assert [entry.status.value for entry in entries] == ["open"]
    claim = _claim(workspace)
    assert "signed_off_by" not in claim
    assert all(anchor["evidence_class"] != "interview" for anchor in claim["anchors"])


def test_interview_builders_differ_only_in_the_paraphrase_sign_off():
    claim_ids = ["t:1.1", "t:2.1", "t:3.1"]
    quotes = {claim_id: f"line for {claim_id}" for claim_id in claim_ids}

    good = interview_good_steps(claim_ids, quotes)
    trap = interview_trap_steps(claim_ids, quotes)

    assert [step["kind"] for step in good] == ["question_draft"] * 3 + [
        "answer_record"
    ] * 3
    assert [step["id"] for step in good[:3]] == ["q-001", "q-002", "q-003"]
    assert [step["claim_ids"] for step in good[:3]] == [[c] for c in claim_ids]
    assert [step["confirmed"] for step in good[3:]] == [True, False, False]
    assert [step["confirmed"] for step in trap[3:]] == [True, True, False]
    assert [step["quote"] for step in good[3:]] == [quotes[c] for c in claim_ids]
    assert {step["transcript"] for step in good[3:]} == {INTERVIEW_TRANSCRIPT}


def test_fake_answers_version():
    completed = subprocess.run(
        [str(FAKE_CLAUDE), "--version"], capture_output=True, text=True
    )
    assert completed.stdout.strip() == "fake-claude 0.0.0"
