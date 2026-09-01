import json
from pathlib import Path

from experiment.audit import (
    audit_campaign,
    audit_events,
    bash_surface,
    classify,
    edit_target,
    guard_stats,
)
from experiment.matrix import execute
from experiment.stream import parse_stream

from .conftest import COMPLETE_STEPS


def test_bash_surface_recognises_each_command_family():
    assert bash_surface("arfc status") == "bash:arfc"
    assert (
        bash_surface("python -m panther.plugins.services.testers.a_rfc m.yaml --out o")
        == "bash:python_a_rfc"
    )
    assert bash_surface("git -C draft tag -a x -m y") == "bash:git"
    assert bash_surface('sqlite3 corpus/index.sqlite "SELECT 1"') == "bash:sqlite3"
    assert bash_surface("git -C d add -A && git -C d commit -m m") == "bash:git"
    assert bash_surface("arfc status && echo x") == "bash:mixed"
    assert bash_surface("echo hi") == "bash:other" and bash_surface("") == "bash:other"


WS = Path("/w")


def test_classify_maps_tools_to_surfaces():
    assert classify("mcp__arfc__arfc_checkpoint", {"cluster_id": "c"}, WS) == (
        "mcp",
        "arfc_checkpoint",
        "",
    )
    assert classify("mcp__other__thing", {}, WS) == (
        "mcp:other",
        "mcp__other__thing",
        "",
    )
    assert classify("Bash", {"command": "arfc gate --strict"}, WS) == (
        "bash:arfc",
        "arfc",
        "",
    )
    assert classify("Edit", {"file_path": "/w/manifest.yaml"}, WS) == (
        "edit",
        "Edit",
        "register",
    )
    assert classify("Write", {"file_path": "/w/draft/draft-x.md"}, WS) == (
        "edit",
        "Write",
        "prose",
    )
    assert classify("Edit", {"file_path": "/w/notes.txt"}, WS) == (
        "edit",
        "Edit",
        "other",
    )
    assert classify("Grep", {"pattern": "x"}, WS) == ("read", "Grep", "")
    assert classify("WebFetch", {}, WS) == ("other", "WebFetch", "")


def test_edit_target_is_read_from_the_layout_not_the_path_shape():
    """The same basename outside the workspace is not a register edit.

    Both counters feed published measurements, so a basename match anywhere on
    disk, or any directory happening to be called ``draft``, would move the
    numbers without anything in the workspace changing.
    """
    assert edit_target("/w/manifest.yaml", WS) == "register"
    assert edit_target("/elsewhere/manifest.yaml", WS) == "other"
    assert edit_target("/w/sub/manifest.yaml", WS) == "other"
    assert edit_target("/w/draft/draft-x.md", WS) == "prose"
    assert edit_target("/elsewhere/draft/draft-x.md", WS) == "other"
    assert edit_target("/w/notes/draft/x.md", WS) == "other"
    assert edit_target("/w/draft/Makefile", WS) == "other"
    assert edit_target("", WS) == "other"


def test_audit_events_flags_an_executed_out_of_arm_call():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"arfc status"}}],"usage":{"input_tokens":1,"output_tokens":1}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":false,"content":"{}"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.1,"usage":{},"permission_denials":[]}\n'
    )
    audit = audit_events(events, "A", WS)
    assert audit["integrity"] is False
    assert audit["executed_out_of_arm"][0]["surface"] == "bash:arfc"
    assert audit_events(events, "B", WS)["integrity"] is True


def test_a_denial_is_recognised_from_its_id_alone():
    """The CLI's own permission_denials entry is authoritative, whatever the text."""
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"echo probe"}}],"usage":{"input_tokens":1,"output_tokens":1}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"some wording nobody predicted"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.1,"usage":{},'
        '"permission_denials":[{"tool_name":"Bash","tool_input":{"command":"echo probe"},"tool_use_id":"t1"}]}\n'
    )
    audit = audit_events(events, "C", WS)
    assert audit["bypass_attempts"]["count"] == 1
    assert audit["integrity"] is True and audit["errors"]["class2"] == 0


def test_a_denial_that_never_reached_the_result_event_falls_back_to_its_text():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"mcp__arfc__arfc_status","input":{}}],"usage":{"input_tokens":1,"output_tokens":1}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"Permission denied: tool is not available in this session"}]}}\n'
        '{"type":"result","subtype":"success","total_cost_usd":0.1,"usage":{},"permission_denials":[]}\n'
    )
    audit = audit_events(events, "B", WS)
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
    # The guard evidence is recorded at launch and re-checked here, so a run
    # carries its own proof that the settings it was confined by are the ones
    # still on disk.
    for arm, audit in (("A", a), ("B", b), ("C", c)):
        assert audit["guard"]["digest_recorded"] is True
        assert audit["guard"]["unmodified"] is True
        assert audit["guard"]["expected_no_bash"] is (arm == "A")


def test_bash_surface_reads_a_command_the_way_the_guard_does():
    """A guard-legal command must not be classified out of its own arm.

    Both quoted shapes below are one in-prefix command to the guard, as is a
    command paging its own output. Classifying any of them as ``bash:mixed``
    would report an integrity violation for a call the arm was entitled to
    make.
    """
    assert (
        bash_surface('arfc corpus-query "SELECT sha FROM commits; SELECT 1"')
        == "bash:arfc"
    )
    assert bash_surface('arfc corpus-query "SELECT a || b FROM c"') == "bash:arfc"
    assert (
        bash_surface("arfc cluster-get c1 --patch 2>&1 | head -c 20000") == "bash:arfc"
    )
    assert bash_surface('sqlite3 c.db "SELECT 1; SELECT 2"') == "bash:sqlite3"
    # Leaving the family through a pipe is still mixed.
    assert bash_surface("arfc status | sh") == "bash:mixed"
    assert bash_surface("arfc status | tee /tmp/x") == "bash:mixed"
    # A command that cannot be read at all is not credited to any family.
    assert bash_surface('arfc corpus-query "unterminated') == "bash:other"


def _hook_start(name="PreToolUse"):
    return {"type": "system", "subtype": "hook_started", "hook_event": name}


def _bash_call(index, command):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": f"t{index}",
                    "name": "Bash",
                    "input": {"command": command},
                }
            ]
        },
    }


def test_guard_stats_pairs_the_digest_with_the_hook_evidence():
    events = [_bash_call(1, "arfc status"), _hook_start()]
    report = guard_stats(events, "B", "abc", "abc")
    assert report["unmodified"] is True
    assert report["bash_calls"] == 1 and report["pretooluse_hook_starts"] == 1
    assert report["fired_for_every_bash_call"] is True


def test_guard_stats_catches_a_settings_file_edited_after_mount():
    events = [_bash_call(1, "arfc status"), _hook_start()]
    report = guard_stats(events, "B", "abc", "def")
    assert report["unmodified"] is False
    # The hook still fired; the two halves fail independently.
    assert report["fired_for_every_bash_call"] is True


def test_guard_stats_catches_a_guard_that_never_ran():
    events = [_bash_call(1, "arfc status"), _bash_call(2, "arfc gate")]
    report = guard_stats(events, "B", "abc", "abc")
    assert report["unmodified"] is True
    assert report["pretooluse_hook_starts"] == 0
    assert report["fired_for_every_bash_call"] is False


def test_guard_stats_treats_arm_a_silence_as_the_expected_state():
    """Arm A has no Bash surface, so firing no PreToolUse hook is correct."""
    report = guard_stats([], "A", "abc", "abc")
    assert report["expected_no_bash"] is True
    assert report["bash_calls"] == 0 and report["pretooluse_hook_starts"] == 0
    assert report["fired_for_every_bash_call"] is True


def test_guard_stats_flags_a_run_that_recorded_no_digest():
    """A run from before the digest existed must not read as verified."""
    report = guard_stats([], "B", "", "abc")
    assert report["digest_recorded"] is False
    assert report["unmodified"] is False
