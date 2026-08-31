import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiment import ExperimentError
from experiment.stream import (
    assistant_text,
    denials,
    init_event,
    parse_stream,
    merge_results,
    result_event,
    result_events,
    tool_results,
    tool_uses,
    usage_series,
)

FIXTURES = Path(__file__).parent / "fixtures" / "stream"
GUARD = Path(__file__).resolve().parents[1] / "guard.py"


@pytest.fixture
def denied():
    return parse_stream((FIXTURES / "denied-bash.jsonl").read_text())


def test_parse_stream_rejects_malformed_lines():
    with pytest.raises(ExperimentError) as excinfo:
        parse_stream('{"type":"result"}\nnot json\n')
    assert "line 2" in str(excinfo.value)
    assert parse_stream("\n\n") == []


def test_init_and_result_events(denied):
    assert init_event(denied)["tools"][0] == "Bash"
    assert result_event(denied)["total_cost_usd"] == 0.038631
    assert init_event([]) is None and result_event([]) is None


def test_tool_uses_and_results_are_linked(denied):
    uses = tool_uses(denied)
    assert uses == [
        {
            "index": 2,
            "id": "toolu_018QkF8RV3XbXftt2hKpQeqV",
            "name": "Bash",
            "input": {
                "command": "echo bypass-probe",
                "description": "Echo bypass-probe",
            },
        }
    ]
    result = tool_results(denied)["toolu_018QkF8RV3XbXftt2hKpQeqV"]
    assert result["is_error"] is True
    assert result["text"].startswith("PreToolUse:Bash hook error:")
    assert "denied: this arm may run only" in result["text"]


def test_denials_come_from_both_sources(denied):
    found = denials(denied)
    assert [d["source"] for d in found] == ["tool_result", "result"]
    assert found[0]["tool"] == "Bash" and found[1]["tool"] == "Bash"


def test_assistant_text_and_usage_series(denied):
    assert assistant_text(denied).endswith("DONE")
    assert "blocked the command" in assistant_text(denied)
    series = usage_series(denied)
    assert [s["total"] for s in series] == [29343, 59075]
    assert series[-1]["cache_read_input_tokens"] == 58650


def test_the_guards_own_message_is_recognised_as_a_denial():
    """``denials`` sees the tool-result side only if the guard says "denied"."""
    completed = subprocess.run(
        [sys.executable, str(GUARD), "git "],
        input=json.dumps({"tool_input": {"command": "echo bypass-probe"}}),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    events = parse_stream(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": (
                                f"PreToolUse:Bash hook error: [guard]: "
                                f"{completed.stderr}"
                            ),
                        }
                    ]
                },
            }
        )
    )
    assert [d["source"] for d in denials(events)] == ["tool_result"]


def test_usage_series_counts_each_message_once():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"a"}],"usage":{"input_tokens":5,"output_tokens":1}}}\n'
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"b"}],"usage":{"input_tokens":5,"output_tokens":1}}}\n'
    )
    assert [s["total"] for s in usage_series(events)] == [6]


def _result(cost, *, denials_=(), subtype="success", tokens=10, model="opus"):
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": subtype != "success",
        "total_cost_usd": cost,
        "num_turns": 3,
        "duration_ms": 1000,
        "duration_api_ms": 900,
        "usage": {"input_tokens": tokens, "service_tier": "standard"},
        "modelUsage": {model: {"inputTokens": tokens}},
        "permission_denials": list(denials_),
        "session_id": f"s-{cost}",
    }


def test_result_events_returns_one_per_session():
    events = [_result(1.0), {"type": "assistant"}, _result(2.0)]
    assert [r["total_cost_usd"] for r in result_events(events)] == [1.0, 2.0]
    assert result_event(events)["total_cost_usd"] == 2.0


def test_merging_one_session_returns_it_unchanged():
    """Nothing downstream may be able to tell how many sessions ran.

    The cost table, the denial count and the campaign report all read this one
    record, so the single-session case has to be the identity — including the
    fields nothing here interprets.
    """
    only = _result(1.5)
    assert merge_results([only]) == only
    assert merge_results([]) is None


def test_merging_sums_cost_rather_than_reporting_the_last_session():
    """Taking the last would report a whole run's spend as its final cluster's."""
    merged = merge_results([_result(1.0), _result(2.0), _result(4.0)])
    assert merged["total_cost_usd"] == 7.0
    assert merged["num_turns"] == 9
    assert merged["duration_ms"] == 3000
    assert merged["session_count"] == 3


def test_merging_sums_usage_leaves_and_keeps_labels():
    merged = merge_results([_result(1.0, tokens=10), _result(2.0, tokens=5)])
    assert merged["usage"]["input_tokens"] == 15
    assert merged["usage"]["service_tier"] == "standard"
    assert merged["modelUsage"]["opus"]["inputTokens"] == 15


def test_merging_keeps_per_model_usage_separate():
    merged = merge_results(
        [_result(1.0, tokens=10, model="opus"), _result(2.0, tokens=5, model="haiku")]
    )
    assert merged["modelUsage"]["opus"]["inputTokens"] == 10
    assert merged["modelUsage"]["haiku"]["inputTokens"] == 5


def test_merging_concatenates_denials_from_every_session():
    """A denial in session one is a bypass attempt whatever session nine did."""
    merged = merge_results(
        [
            _result(1.0, denials_=[{"tool_use_id": "t1"}]),
            _result(2.0),
            _result(3.0, denials_=[{"tool_use_id": "t9"}]),
        ]
    )
    assert [d["tool_use_id"] for d in merged["permission_denials"]] == ["t1", "t9"]


def test_one_failed_session_makes_the_run_a_failure():
    merged = merge_results(
        [_result(1.0), _result(2.0, subtype="error_max_budget"), _result(3.0)]
    )
    assert merged["subtype"] == "error_max_budget"
    assert merged["is_error"] is True
