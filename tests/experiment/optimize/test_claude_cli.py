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
    ClaudeCliSurfaceError,
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


#: The argv every call carries, whichever regime it runs under.
SHARED_ARGV = [
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

#: What the strict regime adds on top, in the order ``argv`` emits it.
STRICT_ARGV = ["--strict-mcp-config", "--exclude-dynamic-system-prompt-sections"]


def test_the_argv_is_exact_and_the_prompt_travels_on_stdin(profile, tmp_path):
    control(profile, reply="fenced", proposal="NEW")

    reply = call(profile, tmp_path, effort="high")("Rewrite it.")

    assert reply == "```\nNEW\n```"
    (recorded,) = calls(profile)
    assert recorded["argv"][1:] == SHARED_ARGV + STRICT_ARGV
    assert recorded["stdin"] == "Rewrite it."
    assert "Rewrite it." not in " ".join(recorded["argv"])
    assert recorded["cwd"] == str(tmp_path / "cwd")


def test_the_proposer_regime_argv_is_the_one_measured_on_2_1_260(profile, tmp_path):
    """U1 ruled the union for the judge and said nothing about the GEPA
    proposer, which shares this class. Off the gate, its argv is byte-for-byte
    the list that loop has always sent."""
    control(profile, reply="fenced", proposal="NEW")

    call(profile, tmp_path, effort="high", strict_surface=False)("Rewrite it.")

    (recorded,) = calls(profile)
    assert recorded["argv"][1:] == SHARED_ARGV


@pytest.mark.parametrize("strict_surface", [True, False])
def test_a_system_prompt_travels_as_a_flag_not_on_stdin(
    profile, tmp_path, strict_surface
):
    """It rides on its own argument rather than the gate, so naming one is
    never silently dropped by the regime the call runs under."""
    control(profile, reply="fenced", proposal="NEW")

    call(
        profile,
        tmp_path,
        effort="high",
        strict_surface=strict_surface,
        system_prompt="Grade one claim.",
    )("Rewrite it.")

    (recorded,) = calls(profile)
    argv = recorded["argv"][1:]
    assert argv[-2:] == ["--system-prompt", "Grade one claim."]
    assert recorded["stdin"] == "Rewrite it."


def test_no_system_prompt_means_no_flag(profile, tmp_path):
    call(profile, tmp_path)("x")

    (recorded,) = calls(profile)
    assert "--system-prompt" not in recorded["argv"]


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
    assert "exited 1: limit" in str(error) and "five_hour" in str(error)


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


# --- the surface the session reports ---------------------------------------


def test_a_session_that_reported_no_init_is_refused(profile, tmp_path):
    """A guard written ``if init is not None`` passes this case without ever
    evaluating, so the session that sends no init is the one that proves the
    guard is not vacuous."""
    control(profile, reply="raw", text="{}", no_init=True)

    with pytest.raises(ClaudeCliSurfaceError, match="no init event"):
        call(profile, tmp_path)("grade this")


@pytest.mark.parametrize("key", ["tools", "mcp_servers"])
def test_a_session_whose_init_omits_the_key_is_refused(profile, tmp_path, key):
    """Absent is not empty. ``init.get(key, [])`` and ``init.get(key) or []``
    both read a key that was never reported as a surface measured empty."""
    control(profile, reply="raw", text="{}", omit_init=[key])

    with pytest.raises(ClaudeCliSurfaceError, match=key):
        call(profile, tmp_path)("grade this")


@pytest.mark.parametrize("key", ["tools", "mcp_servers"])
def test_a_session_reporting_the_key_as_null_is_refused(profile, tmp_path, key):
    """``or []`` collapses absent, null and empty into one. Null is a session
    declining to answer, so it is refused like an absent key."""
    control(profile, reply="raw", text="{}", **{key: None})

    with pytest.raises(ClaudeCliSurfaceError, match=key):
        call(profile, tmp_path)("grade this")


def test_a_session_that_held_tools_is_refused(profile, tmp_path):
    control(profile, reply="raw", text="{}", tools=["Bash"])

    with pytest.raises(ClaudeCliSurfaceError, match="tools") as caught:
        call(profile, tmp_path)("grade this")

    assert "Bash" in str(caught.value)


def test_a_session_that_mounted_a_server_is_refused(profile, tmp_path):
    """The spec's third settled fact: assert the init event, never the flags."""
    control(
        profile,
        reply="raw",
        text="{}",
        mcp_servers=[{"name": "x", "status": "connected"}],
    )

    with pytest.raises(ClaudeCliSurfaceError, match="mcp_servers") as caught:
        call(profile, tmp_path)("grade this")

    # Every caller of this transport catches ClaudeCliError, so a refusal has
    # to arrive inside that hierarchy rather than past it.
    assert isinstance(caught.value, ClaudeCliError)


def test_a_server_named_as_a_bare_string_is_refused(profile, tmp_path):
    """``stream.mcp_servers`` keeps only the entries that are mappings, so a
    guard written on its result reads a list of bare names as no servers at
    all. The guard reads the reported value itself."""
    control(profile, reply="raw", text="{}", mcp_servers=["x"])

    with pytest.raises(ClaudeCliSurfaceError, match="mcp_servers"):
        call(profile, tmp_path)("grade this")


def test_the_empty_surface_the_argv_produces_is_accepted(profile, tmp_path):
    """The passing side of every refusal above: the stub derives its ``tools``
    from ``--tools ""`` and reports no servers, and that call goes through."""
    control(profile, reply="raw", text="ok")

    assert call(profile, tmp_path)("grade this") == "ok"


# --- what the call cost ------------------------------------------------------


def test_the_transport_records_what_the_call_cost(profile, tmp_path):
    control(profile, reply="raw", text="{}", cost=0.0125)
    wrapper = call(profile, tmp_path)

    wrapper("grade this")

    assert wrapper.last_cost_usd == 0.0125


def test_there_is_no_cost_before_the_first_call(profile, tmp_path):
    assert call(profile, tmp_path).last_cost_usd is None


@pytest.mark.parametrize(
    "payload",
    [
        {"omit_result": ["total_cost_usd"]},
        {"cost": None},
        {"cost": "0.0125"},
        {"cost": True},
    ],
    ids=["absent", "null", "string", "bool"],
)
def test_a_cost_that_is_not_a_number_is_recorded_as_unmeasured(
    profile, tmp_path, payload
):
    """A reply is not refused over this field — the envelope's shape is
    version-dependent — but an unmeasured cost is recorded as unmeasured
    rather than as a figure someone could report."""
    control(profile, reply="raw", text="{}", **payload)
    wrapper = call(profile, tmp_path)

    wrapper("grade this")

    assert wrapper.last_cost_usd is None


def test_a_later_call_never_leaves_the_earlier_cost_standing(profile, tmp_path):
    """One wrapper serves every judge call, so a cost held past its own call
    would be recorded against the next one."""
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", cost=0.5)
    wrapper("grade this")
    assert wrapper.last_cost_usd == 0.5

    control(profile, reply="raw", text="{}", omit_result=["total_cost_usd"])
    wrapper("grade that")

    assert wrapper.last_cost_usd is None


@pytest.mark.parametrize(
    "failure",
    [
        {"reply": "nonzero", "stderr": "down\n"},
        {"reply": "raw", "text": "{}", "tools": ["Bash"]},
        {"reply": "error", "message": "no"},
        {"reply": "raw", "text": "", "cost": 0.3},
    ],
    ids=["nonzero", "refused-surface", "error-result", "empty-reply"],
)
def test_a_failed_call_leaves_no_cost_behind(profile, tmp_path, failure):
    """Each mode raises from a different point of ``__call__``, and the last
    one raises *past* the result event the cost is read from. Only that mode
    tells where the assignment sits; the first three would pass with it
    anywhere below the parse, and the error result carries no cost key at all.
    """
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", cost=0.5)
    wrapper("grade this")

    control(profile, **failure)
    with pytest.raises(ClaudeCliError):
        wrapper("grade that")

    assert wrapper.last_cost_usd is None


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
