"""The ``judge`` verb, and the manifest that says what its scores are worth.

Every test drives the ``claude-lm`` stub through the real verb, so nothing
here reaches a model. What the stub is for is that it can *disagree with its
own argv*: it reports the model, the slash commands and the cost its control
file names rather than the ones its flags asked for, and it can omit any of
them. A manifest that read the flags, or defaulted an absent key, would be
indistinguishable from a correct one against a stub that could not lie.

The draft is a real kramdown-rfc document rather than a paragraph of prose:
``blinded_body`` splits on the section markers, so a file without them has no
abstract, middle or back, and the verb would refuse it before any manifest
was written. What the blinding itself removes is ``test_judge.py``'s subject,
not this file's.
"""

import hashlib
import json
from pathlib import Path

import pytest

from ai_rfc.experiment import cli
from ai_rfc.experiment.judge import RUBRIC, JudgeReport

from .conftest import FAKE_CLAUDE_LM as STUB

pytestmark = pytest.mark.unit

#: A draft with the three section markers ``_parts`` splits on, so it has a
#: body to grade. Its front matter names an author and a target, which is what
#: the blinding takes off before a judge sees any of it.
DRAFT = """\
---
title: Example
docname: draft-elniak-example-latest
author:
  name: ai-rfc harness
--- abstract

This document specifies an example endpoint.

--- middle

# Introduction

A peer MUST close the connection on a malformed frame. `ai_rfc:t:1.1`

--- back

# Acknowledgements

Nobody yet.
"""

#: What the verb grades when ``--dimension`` names nothing, spelled out here
#: rather than read off the constant under test: a default derived from the
#: implementation moves with any mutation of it and pins nothing.
DEFAULT_DIMENSIONS = ["structure", "precision", "completeness", "readability"]


def _judge(
    tmp_path: Path,
    lm_profile: Path,
    *,
    argv: tuple[str, ...] = ("--dimension", "structure"),
    **control,
) -> tuple[int, Path]:
    """Run the verb against the LM stub. Never reaches a real model.

    Args:
        tmp_path: The test's own directory; the draft and the output land in it.
        lm_profile: The stub's ``CLAUDE_CONFIG_DIR``, from the shared fixture.
        argv: Extra arguments, defaulting to the one dimension whose grade the
            replies below carry.
        **control: The stub's control file.

    Returns:
        The exit code, and the output directory.
    """
    (lm_profile / "fake-lm.json").write_text(json.dumps(control))
    tmp_path.mkdir(parents=True, exist_ok=True)
    draft = tmp_path / "draft.md"
    draft.write_text(DRAFT)
    out = tmp_path / "out"
    code = cli.main(
        [
            "judge",
            str(draft),
            "--out",
            str(out),
            "--model",
            "fake",
            "--claude-bin",
            str(STUB),
            "--profile-dir",
            str(lm_profile),
            *argv,
        ]
    )
    return code, out


def _judgement(out: Path) -> dict:
    """The written judgement, refusing the absence the dispatch guard catches."""
    path = out / "judge.json"
    assert path.exists(), "the verb wrote nothing"
    return json.loads(path.read_text())


@pytest.fixture(autouse=True)
def _no_ambient_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take ``CLAUDE_CONFIG_DIR`` off the version probe's inherited environment.

    The probe runs ``<claude-bin> --version`` with the environment it was
    launched under, and the stub reads ``CLAUDE_CONFIG_DIR`` before it reads
    its argv. With the variable set it answers the probe with stream-json and
    the manifest records a JSON line as a version; without it, it exits
    non-zero and the manifest records that it reported none. Only the second
    is a property of the verb rather than of whoever ran the suite.
    """
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def test_the_judge_verb_is_dispatched_not_silently_zero(tmp_path, lm_profile):
    """D-3: the if/elif chain has no else; an unwired verb exits 0 doing nothing."""
    code, out = _judge(
        tmp_path, lm_profile, reply="raw", text='{"scores":{"structure":4}}'
    )
    assert (out / "judge.json").exists(), "verb parsed but never dispatched"
    assert code == 0


def test_the_manifest_records_the_permanent_blinding_leak(tmp_path, lm_profile):
    """D-1: never "until an API key is provisioned" — this project has none.

    ``--bare`` is the one flag that stops ``CLAUDE.md`` being auto-discovered
    and it refuses OAuth outright, so under a subscription the leak has no
    end date to wait for.
    """
    _, out = _judge(
        tmp_path, lm_profile, reply="raw", text='{"scores":{"structure":4}}'
    )
    manifest = _judgement(out)["manifest"]
    blinding = manifest["blinding"]
    assert blinding["regime"] == "oauth-permanent"
    # D-43: the leak is a CLAIM inherited from a 2.1.259 probe under different
    # flags, not something this argv re-measured. A bare boolean would convert
    # "unmeasured" into "asserted", which is what the spec's third settled
    # fact exists to prevent.
    assert blinding["claude_md_loaded"] is True
    assert blinding["evidence"] == (
        "measured 2026-09-03 on claude 2.1.259 under the design spec's probe "
        "argv; this judge runs the different argv ClaudeCliCall.argv() "
        "documents, and no probe has re-measured the leak under it or under "
        "the claude_version recorded beside this field"
    )
    assert {"argv", "model", "claude_version", "rubric_sha256"} <= manifest.keys()


def test_the_manifest_records_the_model_that_answered_not_the_one_asked_for(
    tmp_path, lm_profile
):
    """The spec's third settled fact: assert the init event, never the flags.

    Three distinct literals, so the assertion discriminates precedence rather
    than presence: ``--model fake`` is what the verb asked for, ``sonnet`` is
    what the session says answered, and ``fake-lm`` is what the stub reports
    when neither says anything.
    """
    _, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        model="sonnet",
    )
    manifest = _judgement(out)["manifest"]
    assert manifest["model"] == "sonnet"
    assert "--model" in manifest["argv"]
    assert manifest["argv"][manifest["argv"].index("--model") + 1] == "fake"


def test_a_model_the_session_never_reported_is_recorded_as_unreported(
    tmp_path, lm_profile
):
    """Never ``last_init.get("model", "")``: an empty id reads as measured."""
    _, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        omit_init=["model"],
    )
    assert _judgement(out)["manifest"]["model"] == "not reported"


def test_the_manifest_records_the_skills_the_session_listed(tmp_path, lm_profile):
    """The second accepted leak: ``--tools ""`` leaves no Skill tool, and the
    names are still in the session's prompt, so they are scoring-relevant."""
    _, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        slash_commands=["/ivy-debug", "/review-plan"],
    )
    blinding = _judgement(out)["manifest"]["blinding"]
    assert blinding["slash_commands"] == ["/ivy-debug", "/review-plan"]


def test_an_unreported_slash_commands_is_not_a_leak_that_was_closed(
    tmp_path, lm_profile
):
    """An absent key means the leak was not measured, and ``[]`` says the
    opposite — that it was measured and found closed. The two must not collapse
    onto one value, so the empty case is asserted beside the absent one."""
    _, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        omit_init=["slash_commands"],
    )
    assert _judgement(out)["manifest"]["blinding"]["slash_commands"] == "not reported"

    _, measured = _judge(
        tmp_path / "empty",
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        slash_commands=[],
    )
    assert _judgement(measured)["manifest"]["blinding"]["slash_commands"] == []


def test_the_manifest_prices_the_run_from_what_the_session_reported(
    tmp_path, lm_profile
):
    """The figure comes off the result event, not off the model's word for it."""
    _, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        cost=0.25,
    )
    manifest = _judgement(out)["manifest"]
    assert manifest["spend_usd"] == 0.25
    assert manifest["unpriced_calls"] == 0


def test_a_run_that_priced_nothing_says_so_rather_than_reading_zero(
    tmp_path, lm_profile
):
    """``spend_usd`` of 0.0 alone cannot distinguish nothing-billed from
    nothing-measured, which is why ``unpriced_calls`` travels with it.

    It also pins which attribute the manifest reads: ``last_cost_usd`` is
    ``None`` for this call and ``spend_usd`` is ``0.0``, so a manifest built
    from the wrong one records a null here.
    """
    _, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text='{"scores":{"structure":4}}',
        omit_result=["total_cost_usd"],
    )
    manifest = _judgement(out)["manifest"]
    assert manifest["spend_usd"] == 0.0
    assert manifest["unpriced_calls"] == 1


@pytest.mark.parametrize(
    "control,argv,code,unpriced",
    [
        (
            {"reply": "raw", "text": '{"scores":{"structure":4}}', "cost": 0.0},
            ("--dimension", "structure"),
            0,
            0,
        ),
        (
            {"reply": "hang", "seconds": 5},
            ("--dimension", "structure", "--timeout-s", "1"),
            1,
            1,
        ),
        (
            {"reply": "nonzero", "stderr": "the stub was told to fail\n"},
            ("--dimension", "structure"),
            1,
            1,
        ),
    ],
    ids=["a-measured-zero", "a-timeout", "a-non-zero-exit"],
)
def test_the_manifest_never_reports_an_unmeasured_call_as_a_measured_zero(
    tmp_path, lm_profile, control, argv, code, unpriced
):
    """All three manifests carry ``spend_usd: 0.0``, so the count is the only
    field that can say which of them measured it.

    The first id is what makes the other two mean anything: it is a call that
    reported ``total_cost_usd: 0.0`` and was believed — nothing billed, and
    that was measured. The timeout and the non-zero exit both launched a
    child that may well have been billed for the thinking it had already
    done, and neither left a figure behind. A manifest that reported the
    three alike would be telling a reader that a killed call cost nothing,
    which is the defect this whole pair of fields exists to prevent — and the
    timeout is the failure a whole-draft call is likeliest to hit first.
    """
    got, out = _judge(tmp_path, lm_profile, argv=argv, **control)

    assert got == code
    manifest = _judgement(out)["manifest"]
    assert manifest["spend_usd"] == 0.0
    assert manifest["unpriced_calls"] == unpriced


def test_a_billed_and_refused_call_still_records_what_it_spent(tmp_path, lm_profile):
    """An error result is billed: the session ran, the money went, the call
    then failed. Writing the manifest only on success would lose that figure,
    and a refused call is the one an operator most needs accounted for."""
    code, out = _judge(
        tmp_path,
        lm_profile,
        reply="error",
        message="the stub refused",
        cost=0.25,
    )
    assert code == 1
    judgement = _judgement(out)
    assert judgement["scores"] is None
    assert judgement["quotes"] is None
    assert judgement["unverified"] is None
    assert "the stub refused" in judgement["error"]
    assert judgement["manifest"]["spend_usd"] == 0.25
    # The transport clears ``last_init`` on a raise, so the session's own
    # report of itself is gone with the reply it came beside.
    assert judgement["manifest"]["model"] == "not reported"


def test_a_quote_the_draft_does_not_contain_ships_the_scores_and_says_so(
    tmp_path, lm_profile, capsys
):
    """The rubric promises the model that an unfound quote comes back as a
    finding, so refusing the reply would break the contract the prompt states.
    The scores ship; the exit code and the output carry the finding."""
    code, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text=json.dumps(
            {
                "scores": {"structure": 4},
                "quotes": [
                    "A peer MUST close the connection on a malformed frame.",
                    "A peer MUST send a GOAWAY frame first.",
                ],
            }
        ),
    )
    assert code == 3
    judgement = _judgement(out)
    assert judgement["scores"] == {"structure": 4}
    assert judgement["unverified"] == ["A peer MUST send a GOAWAY frame first."]
    captured = capsys.readouterr()
    assert "quotes: 1 of 2 verified" in captured.out
    assert "A peer MUST send a GOAWAY frame first." in captured.err


def test_a_fully_verified_judgement_says_that_too(tmp_path, lm_profile, capsys):
    """The same line on both paths, so a reader who sees only one can still
    tell which it is; an absent finding is not a claim that every quote held."""
    code, out = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text=json.dumps(
            {
                "scores": {"structure": 4},
                "quotes": ["A peer MUST close the connection on a malformed frame."],
            }
        ),
    )
    assert code == 0
    assert _judgement(out)["unverified"] == []
    assert "quotes: 1 of 1 verified" in capsys.readouterr().out


def test_a_judgement_whose_quotes_were_never_checked_is_not_a_clean_one(
    tmp_path, lm_profile, monkeypatch, capsys
):
    """``None`` is not ``()``: unchecked and checked-and-clean are different
    claims, and only the second earns a zero exit.

    ``judge_draft`` always checks, so the branch is unreachable through it and
    is driven by substituting it. It still decides an exit code, and a branch
    that decides one while nothing exercises it is a claim waiting to be paid:
    a later producer of a report — one that grades from a cached reply, say —
    would otherwise inherit success by default.
    """
    from ai_rfc.experiment import judge as judge_module

    monkeypatch.setattr(
        judge_module,
        "judge_draft",
        lambda text, transport, *, dimensions: JudgeReport(
            scores={name: 3 for name in dimensions}, body=text, unverified=None
        ),
    )
    code, out = _judge(tmp_path, lm_profile, reply="raw", text="never sent")
    assert code == 3
    assert _judgement(out)["unverified"] is None
    assert "quotes: not checked" in capsys.readouterr().out


def test_a_model_controlled_quote_cannot_forge_a_line_of_output(
    tmp_path, lm_profile, capsys
):
    """What a judge quotes is its own text, and it is printed when it does not
    check out. A quote carrying newlines would otherwise reach stderr as
    several lines, one of which can be spelled to read like the verb's own."""
    code, _ = _judge(
        tmp_path,
        lm_profile,
        reply="raw",
        text=json.dumps(
            {
                "scores": {"structure": 4},
                "quotes": ["forged\nfinding: nothing to see"],
            }
        ),
    )
    assert code == 3
    errors = capsys.readouterr().err.splitlines()
    assert not any(line.startswith("finding: nothing to see") for line in errors)
    assert any("forged\\nfinding: nothing to see" in line for line in errors)


def test_the_manifest_records_the_rubric_the_grades_were_given_under(
    tmp_path, lm_profile
):
    """A score is a number on one rubric's scale; which rubric is provenance."""
    _, out = _judge(
        tmp_path, lm_profile, reply="raw", text='{"scores":{"structure":4}}'
    )
    digest = _judgement(out)["manifest"]["rubric_sha256"]
    assert digest == hashlib.sha256(RUBRIC.encode()).hexdigest()
    assert len(digest) == 64


def test_the_manifest_records_the_argv_the_call_was_made_with(tmp_path, lm_profile):
    """What was asked for, beside what answered; the two are recorded apart."""
    _, out = _judge(
        tmp_path, lm_profile, reply="raw", text='{"scores":{"structure":4}}'
    )
    argv = _judgement(out)["manifest"]["argv"]
    assert argv[0] == str(STUB)
    assert "--safe-mode" in argv
    assert "--setting-sources" in argv


def test_a_binary_that_reports_no_version_is_recorded_as_unreported(
    tmp_path, lm_profile
):
    """The stub answers ``--version`` with nothing, and that is the honest
    record: an empty string in its place would read as measured-and-blank."""
    _, out = _judge(
        tmp_path, lm_profile, reply="raw", text='{"scores":{"structure":4}}'
    )
    assert _judgement(out)["manifest"]["claude_version"] == "not reported"


def test_the_default_dimensions_are_the_ones_graded(tmp_path, lm_profile):
    """``--dimension`` naming nothing must still put a full rubric in front of
    the judge; a default nobody wired would refuse every reply as off-shape."""
    _, out = _judge(
        tmp_path,
        lm_profile,
        argv=(),
        reply="raw",
        text=json.dumps(
            {"scores": {name: 3 for name in DEFAULT_DIMENSIONS}, "quotes": []}
        ),
    )
    judgement = _judgement(out)
    assert sorted(judgement["scores"]) == sorted(DEFAULT_DIMENSIONS)
    assert judgement["manifest"]["dimensions"] == DEFAULT_DIMENSIONS


def test_a_reply_off_the_rubric_scale_refuses_without_writing_scores(
    tmp_path, lm_profile
):
    """A grade off the scale is a fault, never a low score; the manifest is
    still written, because the call that produced it was still billed."""
    code, out = _judge(
        tmp_path, lm_profile, reply="raw", text='{"scores":{"structure":9}}'
    )
    assert code == 1
    judgement = _judgement(out)
    assert judgement["scores"] is None
    assert "off the 1 to 5 scale" in judgement["error"]
