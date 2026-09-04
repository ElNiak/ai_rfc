"""The claim judge: prompt shape, reply parsing, caching, and the transport.

Every test here drives a stub transport. The one test that calls a model is
marked ``manual`` and skips unless both its environment variables are set.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

from ai_rfc.experiment.optimize.judge import (
    RUBRIC,
    JudgeError,
    anthropic_transport,
    build_judge,
)
from ai_rfc.experiment.optimize.scoring import ClaimHunk, Judgement


def hunk(claim_id="t:1.1", text="A peer MUST close the connection.", body="def x():"):
    return ClaimHunk(
        claim_id=claim_id,
        text=text,
        level="MUST",
        path="src/peer.py",
        commit="a" * 40,
        line=12,
        hunk=body,
    )


def transport_returning(*replies):
    """A transport that records its prompts and returns canned replies."""
    prompts = []
    queue = list(replies)

    def send(prompt):
        prompts.append(prompt)
        return queue.pop(0) if queue else replies[-1]

    send.prompts = prompts
    return send


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("0", 0.0), ("0.5", 0.5), ("1", 1.0)])
def test_each_permitted_score_is_parsed(raw, expected):
    transport = transport_returning(f'{{"score": {raw}, "rationale": "because"}}')

    (judgement,) = build_judge(transport)([hunk()])

    assert judgement == Judgement("t:1.1", expected, "because")


def test_json_wrapped_in_prose_is_still_read():
    transport = transport_returning(
        'Here is my verdict.\n\n{"score": 0.5, "rationale": "close, wrong level"}\n'
        "Hope that helps."
    )

    (judgement,) = build_judge(transport)([hunk()])

    assert judgement.score == 0.5
    assert judgement.rationale == "close, wrong level"


def test_a_brace_inside_the_rationale_does_not_truncate_the_object():
    transport = transport_returning(
        '{"score": 1, "rationale": "the guard on {closed} is the behaviour"}'
    )

    (judgement,) = build_judge(transport)([hunk()])

    assert judgement.score == 1.0
    assert judgement.rationale == "the guard on {closed} is the behaviour"


@pytest.mark.parametrize(
    "reply",
    [
        "no json at all",
        '{"score": 0.7, "rationale": "off the scale"}',
        '{"score": "1", "rationale": "a string is not a number"}',
        '{"score": true, "rationale": "a bool is not a score"}',
        '{"rationale": "no score at all"}',
        '{"score": 1, "rationale":',
    ],
)
def test_a_reply_that_breaks_the_contract_scores_zero_and_says_so(reply):
    transport = transport_returning(reply)

    (judgement,) = build_judge(transport)([hunk()])

    assert judgement.score == 0.0
    assert judgement.rationale.startswith("unparseable judge reply: ")
    assert reply[:20] in judgement.rationale


def test_the_unparseable_rationale_keeps_only_the_head_of_the_reply():
    transport = transport_returning("x" * 500)

    (judgement,) = build_judge(transport)([hunk()])

    assert judgement.rationale == "unparseable judge reply: " + "x" * 80


# --- failure isolation -----------------------------------------------------


def test_a_transport_failure_scores_that_claim_zero_without_aborting_the_batch():
    """One unlucky call must not void the verdicts either side of it."""
    calls = []

    def send(prompt):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("connection reset")
        return '{"score": 1, "rationale": "implements it"}'

    first, second, third = build_judge(send)(
        [hunk("t:1.1"), hunk("t:2.1", text="Two."), hunk("t:3.1", text="Three.")]
    )

    assert first.score == 1.0 and third.score == 1.0
    assert second == Judgement("t:2.1", 0.0, "judge error: connection reset")
    assert len(calls) == 3


def test_an_empty_batch_calls_nothing():
    transport = transport_returning('{"score": 1, "rationale": "x"}')

    assert build_judge(transport)([]) == []
    assert transport.prompts == []


# --- caching ---------------------------------------------------------------


def test_a_repeated_claim_and_hunk_is_not_sent_twice():
    transport = transport_returning('{"score": 0.5, "rationale": "partly"}')
    cache = {}
    judge = build_judge(transport, cache=cache)

    judge([hunk()])
    (again,) = judge([hunk()])

    assert len(transport.prompts) == 1
    assert again.score == 0.5 and again.rationale == "partly"
    assert len(cache) == 1


def test_a_cache_hit_is_relabelled_with_the_claim_it_was_asked_about():
    """The key is the text and the hunk, so two ids can share one entry.

    Returning the cached judgement unchanged would attribute the verdict to
    whichever claim happened to be judged first, and the score's ``anchored``
    list would then disagree with its ``judgements`` list.
    """
    transport = transport_returning('{"score": 1, "rationale": "yes"}')
    judge = build_judge(transport, cache={})

    judge([hunk("t:1.1")])
    (again,) = judge([hunk("t:9.9")])

    assert len(transport.prompts) == 1
    assert again.claim_id == "t:9.9"


def test_relabelling_the_level_asks_the_judge_again():
    """The level is half the question, so it cannot be left out of the key.

    The rubric docks a claim whose level is stronger or weaker than the code
    enforces. If a relabelled claim hit the earlier verdict, that penalty
    would never fire and mislabelling would become free.
    """
    transport = transport_returning('{"score": 1, "rationale": "yes"}')
    judge = build_judge(transport, cache={})
    must = hunk()
    may = ClaimHunk(
        claim_id=must.claim_id,
        text=must.text,
        level="MAY",
        path=must.path,
        commit=must.commit,
        line=must.line,
        hunk=must.hunk,
    )

    judge([must])
    judge([may])

    assert len(transport.prompts) == 2


def test_a_different_hunk_for_the_same_claim_is_sent_again():
    transport = transport_returning('{"score": 1, "rationale": "yes"}')
    judge = build_judge(transport, cache={})

    judge([hunk(body="def x():")])
    judge([hunk(body="def y():")])

    assert len(transport.prompts) == 2


def test_without_a_cache_each_batch_still_asks_once_per_hunk():
    transport = transport_returning('{"score": 1, "rationale": "yes"}')
    judge = build_judge(transport)

    judge([hunk(), hunk()])

    assert len(transport.prompts) == 2


def test_a_failed_reply_is_not_remembered_as_the_verdict():
    """A retry must get a real judgement, not a cached parse failure."""
    replies = ["not json", '{"score": 1, "rationale": "yes"}']
    transport = transport_returning(*replies)
    judge = build_judge(transport, cache={})

    first = judge([hunk()])[0]
    second = judge([hunk()])[0]

    assert first.score == 0.0 and second.score == 1.0


# --- the prompt ------------------------------------------------------------


def test_the_prompt_carries_the_claim_and_the_code_and_never_names_the_rubric():
    """The rubric is hidden from the candidate, so it must not be labelled."""
    transport = transport_returning('{"score": 1, "rationale": "yes"}')

    build_judge(transport)([hunk()])

    (prompt,) = transport.prompts
    assert "A peer MUST close the connection." in prompt
    assert "def x():" in prompt
    assert "src/peer.py" in prompt and "a" * 40 in prompt
    assert "t:1.1" in prompt and "MUST" in prompt
    assert "RUBRIC" not in prompt
    assert RUBRIC in prompt


def test_the_rubric_states_all_three_grades():
    for grade in ("1", "0.5", "0"):
        assert grade in RUBRIC
    assert "RUBRIC" not in RUBRIC


def test_the_rubric_asks_about_the_change_not_the_snapshot():
    """The judge is shown a diff plus context, so it must be told which is which.

    Asking whether a file *snapshot* "introduces or changes" behaviour is a
    question the snapshot cannot answer, and a judge answering it anyway
    scores long-standing code as though the cluster had written it.
    """
    assert "unified diff" in RUBRIC
    assert "context" in RUBRIC


# --- the anthropic transport -----------------------------------------------


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, payload):
    seen = {}

    def urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_a_missing_credential_fails_at_construction(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(JudgeError, match="ANTHROPIC_API_KEY"):
        anthropic_transport("claude-opus-5")


def test_the_request_carries_the_headers_and_the_body_the_api_expects(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = _capture(
        monkeypatch, {"content": [{"type": "text", "text": '{"score": 1}'}]}
    )

    reply = anthropic_transport("claude-opus-5", max_tokens=99, timeout_s=7.0)("ask")

    request = seen["request"]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == "sk-test"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {
        "model": "claude-opus-5",
        "max_tokens": 99,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": "ask"}],
    }
    assert seen["timeout"] == 7.0
    assert reply == '{"score": 1}'


def test_the_first_text_block_is_returned_past_any_other_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _capture(
        monkeypatch,
        {
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "the verdict"},
                {"type": "text", "text": "ignored"},
            ]
        },
    )

    assert anthropic_transport("m")("ask") == "the verdict"


def test_a_reply_with_no_text_block_is_a_judge_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _capture(monkeypatch, {"content": [{"type": "thinking", "thinking": "hmm"}]})

    with pytest.raises(JudgeError, match="no text block"):
        anthropic_transport("m")("ask")


def test_an_http_failure_names_its_status_and_body(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", {}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(JudgeError, match="429"):
        anthropic_transport("m")("ask")


def test_an_unreachable_host_is_a_judge_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def urlopen(request, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(JudgeError, match="could not be reached"):
        anthropic_transport("m")("ask")


def test_a_transport_error_reaches_the_batch_as_a_zero(monkeypatch):
    """JudgeError is an exception like any other; the batch survives it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def urlopen(request, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    (judgement,) = build_judge(anthropic_transport("m"))([hunk()])

    assert judgement.score == 0.0
    assert judgement.rationale.startswith("judge error: ")


@pytest.mark.manual
@pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("AI_RFC_JUDGE_LIVE")),
    reason="set ANTHROPIC_API_KEY and AI_RFC_JUDGE_LIVE=1 to call a real model",
)
def test_a_live_model_grades_a_hunk_that_plainly_implements_its_claim():
    judge = build_judge(anthropic_transport("claude-opus-5"))

    (judgement,) = judge(
        [
            ClaimHunk(
                claim_id="t:1.1",
                text="A peer MUST close the connection when the handshake times out.",
                level="MUST",
                path="src/peer.py",
                commit="a" * 40,
                line=3,
                hunk=(
                    "def on_handshake_timeout(self):\n"
                    "    self.transport.close()\n"
                    "    self.state = CLOSED\n"
                ),
            )
        ]
    )

    assert judgement.score == 1.0
    assert judgement.rationale
