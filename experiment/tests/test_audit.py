import json

from experiment.audit import audit_campaign, audit_events, bash_family, classify
from experiment.matrix import execute
from experiment.stream import parse_stream

from .conftest import COMPLETE_STEPS


def test_bash_family_recognises_each_command_family():
    assert bash_family("arfc status") == "bash:arfc"
    assert (
        bash_family("python -m panther.plugins.services.testers.a_rfc m.yaml --out o")
        == "bash:python_a_rfc"
    )
    assert bash_family("git -C draft tag -a x -m y") == "bash:git"
    assert bash_family('sqlite3 corpus/index.sqlite "SELECT 1"') == "bash:sqlite3"
    assert bash_family("git -C d add -A && git -C d commit -m m") == "bash:git"
    assert bash_family("arfc status && echo x") == "bash:mixed"
    assert bash_family("echo hi") == "bash:other" and bash_family("") == "bash:other"


def test_classify_maps_tools_to_surfaces():
    assert classify("mcp__arfc__arfc_checkpoint", {"cluster_id": "c"}) == (
        "mcp",
        "arfc_checkpoint",
        "",
    )
    assert classify("mcp__other__thing", {}) == ("mcp:other", "mcp__other__thing", "")
    assert classify("Bash", {"command": "arfc gate --strict"}) == (
        "bash:arfc",
        "arfc",
        "",
    )
    assert classify("Edit", {"file_path": "/w/manifest.yaml"}) == (
        "edit",
        "Edit",
        "register",
    )
    assert classify("Write", {"file_path": "/w/draft/draft-x.md"}) == (
        "edit",
        "Write",
        "prose",
    )
    assert classify("Edit", {"file_path": "/w/notes.txt"}) == ("edit", "Edit", "other")
    assert classify("Grep", {"pattern": "x"}) == ("read", "Grep", "")
    assert classify("WebFetch", {}) == ("other", "WebFetch", "")


def test_audit_events_flags_an_executed_out_of_arm_call():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"arfc status"}}],"usage":{"input_tokens":1,"output_tokens":1}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":false,"content":"{}"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.1,"usage":{},"permission_denials":[]}\n'
    )
    audit = audit_events(events, "A")
    assert audit["integrity"] is False
    assert audit["executed_out_of_arm"][0]["surface"] == "bash:arfc"
    assert audit_events(events, "B")["integrity"] is True


def test_a_denial_is_recognised_from_its_id_alone():
    """The CLI's own permission_denials entry is authoritative, whatever the text."""
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"echo probe"}}],"usage":{"input_tokens":1,"output_tokens":1}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"some wording nobody predicted"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.1,"usage":{},'
        '"permission_denials":[{"tool_name":"Bash","tool_input":{"command":"echo probe"},"tool_use_id":"t1"}]}\n'
    )
    audit = audit_events(events, "C")
    assert audit["bypass_attempts"]["count"] == 1
    assert audit["integrity"] is True and audit["errors"]["class2"] == 0


def test_a_denial_that_never_reached_the_result_event_falls_back_to_its_text():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"mcp__arfc__arfc_status","input":{}}],"usage":{"input_tokens":1,"output_tokens":1}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"Permission denied: tool is not available in this session"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.1,"usage":{},"permission_denials":[]}\n'
    )
    audit = audit_events(events, "B")
    assert audit["bypass_attempts"]["count"] == 1 and audit["errors"]["class1"] == 0


def test_audit_over_fake_runs_counts_bypasses_and_errors(campaign, write_scenario):
    write_scenario(
        campaign.profile_dir,
        "A1",
        {
            "arm": "A",
            "steps": COMPLETE_STEPS
            + [
                {"kind": "denied", "command": "arfc status"},
                {"kind": "tool_error"},
                {"kind": "compact"},
            ],
        },
    )
    write_scenario(
        campaign.profile_dir,
        "B1",
        {
            "arm": "B",
            "steps": COMPLETE_STEPS + [{"kind": "mcp_denied"}, {"kind": "tool_error"}],
        },
    )
    write_scenario(
        campaign.profile_dir,
        "C1",
        {"arm": "C", "steps": COMPLETE_STEPS + [{"kind": "tool_error"}]},
    )
    execute(campaign, report=lambda _: None)
    audits = audit_campaign(campaign)
    assert set(audits) == {"A1", "B1", "C1"}
    a, b, c = audits["A1"], audits["B1"], audits["C1"]
    assert all(audit["integrity"] for audit in (a, b, c))
    assert a["bypass_attempts"]["count"] == 1
    assert a["bypass_attempts"]["by_surface"] == {"bash:arfc": 1}
    assert a["errors"] == {
        "class1": 1,
        "class2": 0,
        "first_failure_index": a["errors"]["first_failure_index"],
    }
    assert a["errors"]["first_failure_index"] is not None
    assert a["compaction_events"] == 1
    assert (
        b["bypass_attempts"]["by_surface"] == {"mcp": 1} and b["errors"]["class2"] == 1
    )
    assert c["errors"]["class2"] == 1 and c["bypass_attempts"]["count"] == 0
    assert c["hand_edits"] == {
        "manifest.yaml": 2,
        "questions.yaml": 0,
        "revisions.yaml": 1,
    }
    assert a["hand_edits"] == {
        "manifest.yaml": 0,
        "questions.yaml": 0,
        "revisions.yaml": 0,
    }
    assert a["prose_edits"] == 1 and c["prose_edits"] == 1
    assert a["tool_calls"]["by_surface"]["mcp"] >= 6
    stored = json.loads((campaign.audit_dir / "A1.json").read_text())
    assert stored == a
