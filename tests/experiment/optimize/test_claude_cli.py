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

    call(profile, tmp_path, effort="high", union_argv=False)("Rewrite it.")

    (recorded,) = calls(profile)
    assert recorded["argv"][1:] == SHARED_ARGV


@pytest.mark.parametrize("union_argv", [True, False])
def test_a_system_prompt_travels_as_a_flag_not_on_stdin(profile, tmp_path, union_argv):
    """It rides on its own argument rather than the gate, so naming one is
    never silently dropped by the regime the call runs under."""
    control(profile, reply="fenced", proposal="NEW")

    call(
        profile,
        tmp_path,
        effort="high",
        union_argv=union_argv,
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


def test_a_session_that_wrote_nothing_at_all_says_so(profile, tmp_path):
    """The guard runs before the result-event check, so an exit-0 empty
    stream lands here rather than at "ended without a result event". Both
    messages would be true of it; only one says what happened.

    ``hang`` for zero seconds is the stub's silent mode: it sleeps nothing
    and returns 0 without reaching the preamble, which is the one control
    that produces a clean exit and no events."""
    control(profile, reply="hang", seconds=0)

    with pytest.raises(ClaudeCliSurfaceError, match="wrote no events at all"):
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


@pytest.mark.parametrize("union_argv", [True, False])
def test_a_session_that_mounted_a_server_is_refused(profile, tmp_path, union_argv):
    """The spec's third settled fact: assert the init event, never the flags.

    Driven under both regimes because the flag is the one thing that could
    plausibly gate this and must not: the proposer runs with it off, and a
    guard that came off with it would leave the arm likeliest to mount a
    server as the only one not checked for having done so.
    """
    control(
        profile,
        reply="raw",
        text="{}",
        mcp_servers=[{"name": "x", "status": "connected"}],
    )

    with pytest.raises(ClaudeCliSurfaceError, match="mcp_servers") as caught:
        call(profile, tmp_path, union_argv=union_argv)("grade this")

    # It is a ClaudeCliError so that a caller written for the transport's
    # failures sees it at all; what a caller then does with it differs by
    # site, and ``optimize.judge`` singles this subclass out by name.
    assert isinstance(caught.value, ClaudeCliError)
    # Carried like its six siblings, for a reader of a failed run's log.
    assert caught.value.exit_code == 0 and caught.value.stderr_tail == ""


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
def test_a_failed_call_leaves_neither_the_cost_nor_the_surface_behind(
    profile, tmp_path, failure
):
    """Each mode raises from a different point of ``__call__``, which is what
    makes the four of them say where the two assignments sit.

    ``refused-surface`` raises inside the guard, so nothing an accepted init
    would have carried is recorded. ``error-result`` and ``empty-reply`` both
    raise *after* an init the guard accepted, and ``empty-reply`` after the
    result event the cost is read from: they are the two that refuse a record
    written the moment its source is in hand. ``nonzero`` never parses at all.
    """
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", cost=0.5)
    wrapper("grade this")

    control(profile, **failure)
    with pytest.raises(ClaudeCliError):
        wrapper("grade that")

    assert wrapper.last_cost_usd is None
    assert wrapper.last_init is None


# --- what the run has spent --------------------------------------------------


def test_nothing_is_spent_before_the_first_call(profile, tmp_path):
    wrapper = call(profile, tmp_path)

    assert wrapper.spend_usd == 0.0
    assert wrapper.unpriced_calls == 0


def test_spend_is_the_sum_of_the_calls_and_never_the_last_of_them(profile, tmp_path):
    """The one figure that can account for a run, which ``last_cost_usd``
    cannot: it describes a single call and the next call clears it."""
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", cost=0.25)

    wrapper("grade this")
    wrapper("grade that")

    assert wrapper.spend_usd == pytest.approx(0.5)
    assert wrapper.last_cost_usd == 0.25


@pytest.mark.parametrize(
    "failure,spent",
    [
        ({"reply": "nonzero", "stderr": "down\n", "cost": 0.5}, 0.0),
        ({"reply": "raw", "text": "{}", "tools": ["Bash"], "cost": 0.5}, 0.5),
        ({"reply": "error", "message": "no", "cost": 0.5}, 0.5),
        ({"reply": "raw", "text": "", "cost": 0.5}, 0.5),
    ],
    ids=["nonzero", "refused-surface", "error-result", "empty-reply"],
)
def test_a_call_that_was_billed_and_then_failed_still_counts_as_spend(
    profile, tmp_path, failure, spent
):
    """Three of these four are billed-and-refused: the session ran, the money
    went, and the call then failed. A manifest that dropped them would report
    a run as cheaper than it was.

    ``nonzero`` is the one that contributes nothing, and not because the
    control file withheld a figure — it names the same ``cost`` as the other
    three. The child exited non-zero, so the stream is never read for a
    result event at all; what it parses there it parses only to name a quota
    in the error, from stdout a failed child may have left half-written.
    """
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", cost=0.25)
    wrapper("grade this")

    control(profile, **failure)
    with pytest.raises(ClaudeCliError):
        wrapper("grade that")

    assert wrapper.spend_usd == pytest.approx(0.25 + spent)
    # The per-call figure is still cleared by the failure; only the total is
    # monotonic, so the two fields are shown to answer different questions.
    assert wrapper.last_cost_usd is None


def test_a_call_that_left_no_figure_is_counted_not_summed_as_nothing(profile, tmp_path):
    """A ``spend_usd`` of zero says either "nothing was billed" or "nothing
    was measured", and a field that cannot tell those apart is worse than no
    field. The count is what tells them apart."""
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", omit_result=["total_cost_usd"])

    wrapper("grade this")

    assert wrapper.spend_usd == 0.0
    assert wrapper.unpriced_calls == 1


def test_a_deep_copy_carries_the_running_total(profile, tmp_path):
    """``result.json`` deep-copies a run's settings, and the wrapper is one of
    them; a total that did not survive the copy would land there as zero."""
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="{}", cost=0.5)
    wrapper("grade this")

    twin = copy.deepcopy(wrapper)

    assert twin.spend_usd == pytest.approx(0.5)
    assert twin.unpriced_calls == 0


# --- what the session reported -----------------------------------------------


def test_the_model_recorded_is_the_one_that_answered_not_the_one_asked_for(
    profile, tmp_path
):
    """The spec's third settled fact reaches the model id too. Both halves are
    asserted here, or the test would not show the two sources disagreeing."""
    control(profile, reply="raw", text="ok", model="reported-model")
    wrapper = call(profile, tmp_path, model="asked-model")

    wrapper("grade this")

    assert wrapper.last_init["model"] == "reported-model"
    (recorded,) = calls(profile)
    assert recorded["argv"][recorded["argv"].index("--model") + 1] == "asked-model"


@pytest.mark.parametrize(
    "reported", [[], ["ai-rfc:draft", "ai-rfc:grade"]], ids=["empty", "populated"]
)
def test_the_slash_commands_recorded_are_the_ones_the_session_listed(
    profile, tmp_path, reported
):
    """The residual blinding leak the design spec accepts on condition the
    manifest states it. The populated case is what makes this a measurement:
    against the empty default alone, a recorder that invented ``[]`` would
    pass."""
    control(profile, reply="raw", text="ok", slash_commands=reported)
    wrapper = call(profile, tmp_path)

    wrapper("grade this")

    assert wrapper.last_init["slash_commands"] == reported


def test_a_key_the_session_never_reported_is_absent_rather_than_empty(
    profile, tmp_path
):
    """The distinction the surface guard exists for, kept at the reporting
    layer: the manifest's claim about the leak turns on which of the two it
    was, so one wrapper is driven through both."""
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="ok")
    wrapper("grade this")
    assert wrapper.last_init["slash_commands"] == []

    control(profile, reply="raw", text="ok", omit_init=["slash_commands"])
    wrapper("grade that")

    assert "slash_commands" not in wrapper.last_init


def test_the_session_id_and_key_source_are_carried_when_the_init_has_them(
    profile, tmp_path
):
    """Both differ from the stub's own defaults, so neither assertion could
    be met by a recorder that filled them in."""
    control(
        profile,
        reply="raw",
        text="ok",
        session_id="s-9",
        apiKeySource="temporary",
    )
    wrapper = call(profile, tmp_path)

    wrapper("grade this")

    assert wrapper.last_init["session_id"] == "s-9"
    assert wrapper.last_init["apiKeySource"] == "temporary"


def test_the_accepted_surface_is_recorded_as_the_empty_lists_it_reported(
    profile, tmp_path
):
    """These two can only ever be empty here — the guard refuses every other
    value — so the assertion pins that they are carried, not that they were
    read. What discriminates the reading is the refused-surface case in
    ``…leaves_neither_the_cost_nor_the_surface_behind``."""
    control(profile, reply="raw", text="ok")
    wrapper = call(profile, tmp_path)

    wrapper("grade this")

    assert wrapper.last_init["tools"] == []
    assert wrapper.last_init["mcp_servers"] == []


def test_there_is_no_surface_before_the_first_call(profile, tmp_path):
    assert call(profile, tmp_path).last_init is None


def test_a_later_call_never_leaves_the_earlier_surface_standing(profile, tmp_path):
    """One wrapper serves every judge call, so a surface held past its own
    call would be recorded as the next one's condition."""
    wrapper = call(profile, tmp_path)
    control(profile, reply="raw", text="ok", model="first-model")
    wrapper("grade this")
    assert wrapper.last_init["model"] == "first-model"

    control(profile, reply="raw", text="ok", model="second-model")
    wrapper("grade that")

    assert wrapper.last_init["model"] == "second-model"


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


def test_a_contaminated_call_leaves_the_batch_as_itself_not_as_a_judge_error(
    profile, tmp_path
):
    """The pair to the test above, driven through the same transport.

    A stub that exits non-zero is unreachable infrastructure and arrives as
    ``JudgeError``; a stub that answers while reporting a server it was not
    given is a different fault about the same batch, and the judge lets this
    one class out by name so that it is never averaged as a verdict.
    """
    control(
        profile,
        reply="raw",
        text="{}",
        mcp_servers=[{"name": "x", "status": "connected"}],
    )

    with pytest.raises(ClaudeCliSurfaceError, match="mcp_servers"):
        build_judge(call(profile, tmp_path))([hunk(), hunk("t:2.1", text="Two.")])

    # By the first hunk, so the second is never sent.
    assert len(calls(profile)) == 1


# --- as a settings value ----------------------------------------------------


def test_the_wrapper_survives_the_deep_copy_asdict_performs(profile, tmp_path):
    """Copied after a call, because the class docstring's claim is that it
    holds "the JSON one session reported" — a pre-call copy carries ``None``
    there and so tests the claim against the one state that cannot break
    it."""
    control(profile, reply="raw", text="ok", slash_commands=["ai-rfc:draft"], cost=0.5)
    wrapper = call(profile, tmp_path)
    wrapper("grade this")

    twin = copy.deepcopy(wrapper)

    assert repr(twin) == repr(wrapper) and twin.argv() == wrapper.argv()
    assert twin.last_init == wrapper.last_init and twin.last_cost_usd == 0.5
    # A copy, not the same mapping: the wrapper's next call replaces its own
    # attribute, and a settings snapshot must keep the call it recorded.
    assert twin.last_init is not wrapper.last_init
