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
    result_event,
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
