"""``claude -p`` as a one-shot model: argv, stdin, environment, and failures.

Every test drives the ``claude-lm`` stub that ships with the tests. Nothing
here reaches a real CLI; the one real call is the row's probe step.
"""

import copy
import json

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.optimize.claude_cli import (
    PREFIX,
    ClaudeCliCall,
    ClaudeCliError,
    cli_model,
    quota,
)
from ai_rfc.experiment.optimize.judge import RUBRIC, JudgeError, build_judge
from ai_rfc.experiment.optimize.scoring import ClaimHunk, Judgement

from ..conftest import FAKE_CLAUDE_LM as STUB


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "profile"
    path.mkdir()
    return path


def control(profile, **payload):
    (profile / "fake-lm.json").write_text(json.dumps(payload))


def calls(profile):
    folder = profile / "fake-lm-calls"
    return [json.loads(p.read_text()) for p in sorted(folder.iterdir())]


def call(profile, tmp_path, model="some-model", **overrides):
    return ClaudeCliCall(str(STUB), profile, model, cwd=tmp_path / "cwd", **overrides)


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


# --- the form -------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("claude-cli:claude-opus-5", "claude-opus-5"),
        ("claude-cli: opus ", "opus"),
        ("anthropic/claude-sonnet-4-6", None),
        ("claude-opus-5", None),
    ],
)
def test_cli_model_reads_the_form_and_ignores_every_other_id(value, expected):
    assert cli_model(value) == expected


def test_cli_model_refuses_the_prefix_alone():
    with pytest.raises(ExperimentError, match="names no model"):
        cli_model("claude-cli:")


def test_repr_is_the_form_the_result_file_records(profile, tmp_path):
    assert (
        repr(call(profile, tmp_path, model="claude-opus-5")) == PREFIX + "claude-opus-5"
    )


# --- the call -------------------------------------------------------------


def test_the_argv_is_exact_and_the_prompt_travels_on_stdin(profile, tmp_path):
    control(profile, reply="fenced", proposal="NEW")

    reply = call(profile, tmp_path, effort="high")("Rewrite it.")

    assert reply == "```\nNEW\n```"
    (recorded,) = calls(profile)
    assert recorded["argv"][1:] == [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "some-model",
        "--effort",
        "high",
        "--safe-mode",
        "--setting-sources",
        "",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--no-session-persistence",
    ]
    assert recorded["stdin"] == "Rewrite it."
    assert "Rewrite it." not in " ".join(recorded["argv"])
    assert recorded["cwd"] == str(tmp_path / "cwd")


def test_the_environment_is_the_profile_and_nothing_the_shell_holds(
    profile, tmp_path, monkeypatch
):
    """The proposer reads every skill body; a key in its environment would be
    one process away from the model. It gets five variables and no more."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")

    call(profile, tmp_path)("x")

    (recorded,) = calls(profile)
    assert recorded["env"]["CLAUDE_CONFIG_DIR"] == str(profile)
    assert "ANTHROPIC_API_KEY" not in recorded["env"]
    assert "CLAUDECODE" not in recorded["env"]
    # The child's os.environ may gain a loader variable or two on macOS, so
    # the proof of "nothing inherited" is the two planted names being absent.
    assert {"HOME", "USER", "PATH", "LANG", "CLAUDE_CONFIG_DIR"} <= set(recorded["env"])


def test_a_message_list_is_flattened_onto_stdin(profile, tmp_path):
    call(profile, tmp_path)(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": [{"type": "text", "text": "Rewrite it."}]},
        ]
    )

    (recorded,) = calls(profile)
    assert recorded["stdin"] == "[System]\nBe brief.\n\n[User]\nRewrite it."


def test_the_cwd_is_created_on_the_first_call_not_before(profile, tmp_path):
    wrapper = call(profile, tmp_path)
    assert not (tmp_path / "cwd").exists()

    wrapper("x")

    assert (tmp_path / "cwd").is_dir()


# --- failures -------------------------------------------------------------


def test_a_nonzero_exit_raises_with_the_code_and_the_stderr_tail(profile, tmp_path):
    control(profile, reply="nonzero", stderr="x" * 3000 + "TAIL\n", exit_code=7)

    with pytest.raises(ClaudeCliError) as caught:
        call(profile, tmp_path)("x")

    error = caught.value
    assert error.exit_code == 7
    assert error.stderr_tail.endswith("TAIL\n") and len(error.stderr_tail) <= 2000
    assert "exited 7" in str(error) and "TAIL" in str(error)
    assert "claude-cli:some-model" in str(error)


def test_a_nonzero_exit_keeps_the_quota_the_stream_already_carried(profile, tmp_path):
    """A hard usage-limit trip is the failure this design is metered against,
    and it may exit non-zero after the quota event is already on stdout: the
    window has to survive into the error the operator reads."""
    control(
        profile,
        reply="nonzero",
        stderr="limit\n",
        exit_code=1,
        rate_limit={"unifiedWindows": {"five_hour": {"utilization": 1.0}}},
    )

    with pytest.raises(ClaudeCliError) as caught:
        call(profile, tmp_path)("x")

    error = caught.value
    assert error.exit_code == 1
    assert "exited 1" in str(error)
    assert "limit" in str(error) and "five_hour" in str(error)


def test_a_timeout_raises_and_names_the_cap(profile, tmp_path):
    control(profile, reply="hang", seconds=5)

    with pytest.raises(ClaudeCliError, match="within 1 s") as caught:
        call(profile, tmp_path, timeout_s=1)("x")

    assert caught.value.exit_code is None


def test_an_error_result_raises_with_its_message_and_the_quota(profile, tmp_path):
    control(
        profile,
        reply="error",
        message="usage limit reached",
        rate_limit={"unifiedWindows": {"seven_day": {"utilization": 1.0}}},
    )

    with pytest.raises(ClaudeCliError) as caught:
        call(profile, tmp_path)("x")

    assert "usage limit reached" in str(caught.value)
    assert "seven_day" in str(caught.value)
    assert caught.value.exit_code == 0


def test_output_that_is_not_stream_json_raises(profile, tmp_path):
    control(profile, reply="garbage")

    with pytest.raises(ClaudeCliError, match="not stream-json"):
        call(profile, tmp_path)("x")


def test_a_missing_binary_raises_naming_it(profile, tmp_path):
    wrapper = ClaudeCliCall(
        str(tmp_path / "no-such-claude"), profile, "m", cwd=tmp_path / "cwd"
    )

    with pytest.raises(ClaudeCliError, match="no-such-claude"):
        wrapper("x")


def test_a_binary_that_is_not_executable_raises_naming_it(profile, tmp_path):
    """A --claude-bin that exists but carries no execute bit fails inside the
    child's exec, not at lookup, so it is a PermissionError rather than the
    FileNotFoundError a missing path gives."""
    binary = tmp_path / "not-executable-claude"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o644)
    wrapper = ClaudeCliCall(str(binary), profile, "m", cwd=tmp_path / "cwd")

    with pytest.raises(ClaudeCliError) as caught:
        wrapper("x")

    assert "not-executable-claude" in str(caught.value)
    assert "claude-cli:m" in str(caught.value)


def test_a_cwd_that_cannot_be_created_raises_naming_it(profile, tmp_path):
    """The cwd is made on the first call, so a path already held by a file is
    a failure of the call rather than of construction."""
    blocked = tmp_path / "cwd"
    blocked.write_text("")
    wrapper = ClaudeCliCall(str(STUB), profile, "m", cwd=blocked)

    with pytest.raises(ClaudeCliError) as caught:
        wrapper("x")

    assert str(blocked) in str(caught.value)
    assert "claude-cli:m" in str(caught.value)


def test_quota_reads_the_last_rate_limit_event():
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "rate_limit_event", "rate_limit_info": {"a": 1}},
        {"type": "rate_limit_event", "rate_limit_info": {"a": 2}},
        {"type": "result", "result": "x"},
    ]
    assert quota(events) == {"a": 2}
    assert quota([{"type": "result"}]) is None


# --- as the judge's transport ------------------------------------------------


def test_build_judge_reads_the_verdict_through_the_wrapper(profile, tmp_path):
    control(profile, reply="verdict", score=0.5)
    wrapper = call(profile, tmp_path, model="judge-model", effort="low", timeout_s=30)

    (judgement,) = build_judge(wrapper)([hunk()])

    assert judgement == Judgement("t:1.1", 0.5, "stub verdict")
    (recorded,) = calls(profile)
    assert recorded["stdin"].startswith(RUBRIC)
    assert recorded["argv"][recorded["argv"].index("--effort") + 1] == "low"


def test_a_call_failing_on_every_claim_is_the_harness_fault_the_evaluator_retries(
    profile, tmp_path
):
    """One stub answers every call the same way, so every hunk fails, and
    that is the case ``build_judge`` turns into a ``JudgeError`` rather than
    a batch of zeros: the evaluator reads it as infrastructure and retries."""
    control(profile, reply="nonzero", stderr="down\n")

    with pytest.raises(JudgeError, match="exited 3: down"):
        build_judge(call(profile, tmp_path))([hunk(), hunk("t:2.1", text="Two.")])


# --- as a settings value ----------------------------------------------------


def test_the_wrapper_survives_the_deep_copy_asdict_performs(profile, tmp_path):
    wrapper = call(profile, tmp_path)

    twin = copy.deepcopy(wrapper)

    assert repr(twin) == repr(wrapper) and twin.argv() == wrapper.argv()
