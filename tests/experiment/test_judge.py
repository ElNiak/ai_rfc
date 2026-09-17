"""A blinded, quote-verified judge over one draft's prose.

Every test drives the ``claude-lm`` stub that ships with the tests, or a bare
callable. Nothing here reaches a real CLI.

The blinding tests grade the real skeleton rather than a synthetic body: a
hand-written "body" carries only the leaks its author thought of, so it would
pass a blinding that does nothing. The skeleton is the front matter every
draft this tool produces starts from, ``author:`` block and ``docname``
included.
"""

import json
import string
from pathlib import Path

import pytest

from ai_rfc.experiment.judge import (
    RUBRIC,
    JudgeError,
    JudgeReport,
    _prompt,
    blinded_body,
    judge_draft,
    judge_transport,
    verify_quotes,
)
from ai_rfc.lifecycle.workspace import DRAFT_SKELETON

from .conftest import FAKE_CLAUDE_LM as STUB

#: Normative sentences with a citation in each of the two spellings the lint
#: recognises, and one structure block whose delimiters carry the same token
#: outside backticks. None of the three is in the skeleton; all three are in
#: real drafts.
AUTHORED = (
    "\n## Connection Close\n\n"
    "A peer MUST close the connection on a malformed frame. `ai_rfc:t:1.1`\n\n"
    "A peer MUST NOT reuse the identifier afterwards. `a_rfc:t:1.2`\n\n"
    "{::comment}\nai_rfc:struct:header begin\n{:/comment}\n"
    "| Field | Width |\n"
    "{::comment}\nai_rfc:struct:header end\n{:/comment}\n"
)

#: A real draft: the skeleton's own front matter, filled as ``scaffold`` fills
#: it, with a cluster's prose appended under the middle.
DRAFT = (
    string.Template(DRAFT_SKELETON.read_text()).substitute(
        title='"Reconstructed aioquic"',
        abbrev='"aioquic"',
        docname='"draft-elniak-aioquic-reconstructed-latest"',
        source_title='"The aioquic implementation"',
        source_org='"The aioquic developers"',
        target="aioquic",
    )
    + AUTHORED
)

DIMENSIONS = ("structure", "clarity")

#: Every string the blinding has to take out, each one present in ``DRAFT``.
#: The first six come from the front matter, the last three from the body --
#: a backticked citation in both spellings, and the bare structure delimiters
#: inside a comment block.
LEAKS = (
    "elniak",
    "docname",
    "Crochet",
    "christophe",
    "UCLouvain",
    "trust200902",
    "ai_rfc",
    "a_rfc",
    "struct:header",
)


def reply_text(scores=None, **extra):
    """One judge reply in the pinned shape, as the stub's ``raw`` mode takes it."""
    payload = {
        "scores": scores if scores is not None else {"structure": 4, "clarity": 3}
    }
    payload.update(extra)
    return json.dumps(payload)


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


# --- what the judge is shown -------------------------------------------------


def test_the_blinding_keeps_the_prose_it_is_grading():
    """Asserted first, and asserted at all, because every test below it is a
    negative: a blinding that returned the empty string would pass all of them
    and grade nothing."""
    body = blinded_body(DRAFT)

    assert "A peer MUST close the connection on a malformed frame." in body
    assert "# Security Considerations" in body
    assert "This document has no IANA actions." in body


@pytest.mark.parametrize("leak", LEAKS)
def test_the_fixture_carries_each_leak_before_it_is_blinded(leak):
    """What makes the next test discriminate anything.

    A negative assertion over an input that never carried the thing passes for
    a blinding that does nothing at all, and an earlier draft of this file had
    exactly that: it asserted the legacy ``a_rfc:`` spelling was gone from a
    fixture that had never contained one.
    """
    assert leak in DRAFT


@pytest.mark.parametrize("leak", LEAKS)
def test_the_prompt_carries_none_of_them(leak):
    """``docname: draft-elniak-…`` names the author and the target, the
    ``author:`` block names the author outright, and the body names the
    harness beside every normative sentence. Body-only closes the first two;
    "no path in the prompt" would close neither, since both are inside the
    text rather than around it."""
    assert leak not in _prompt(DRAFT, dimensions=DIMENSIONS)


def test_a_citation_leaves_a_placeholder_where_it_stood():
    """Whether a normative sentence carries a citation is a quality property,
    so the sentence must not silently lose its shape when the id goes."""
    body = blinded_body(DRAFT)

    assert "malformed frame. `[citation]`" in body


def test_the_prompt_names_no_file():
    """A claim about the scaffolding, not about the body.

    ``judge_draft`` is handed text and never a path, so the only way a
    filename could reach the prompt is the rubric or the framing around it.
    This fixture's body carries no ``.md``, which is what leaves the
    assertion pointed at the scaffolding -- and is also why it would not
    catch a body that named a file, which nothing here promises it does.
    """
    assert ".md" not in _prompt(DRAFT, dimensions=DIMENSIONS)


def test_the_rubric_itself_leaks_nothing():
    """The rubric is most of the prompt, so the prompt assertions above would
    hold for a leaky rubric only as long as nobody wrote the leak into it."""
    for leak in ("ai_rfc", "elniak", "PANTHER", ".md", "docname"):
        assert leak not in RUBRIC


def test_the_prompt_names_every_dimension_it_asks_for():
    prompt = _prompt(DRAFT, dimensions=DIMENSIONS)

    assert "structure" in prompt and "clarity" in prompt


def test_a_draft_whose_body_is_empty_is_refused():
    """A front matter with no body blinds down to nothing, and grading nothing
    would come back with scores that look like a verdict on a draft."""
    with pytest.raises(JudgeError, match="no body"):
        _prompt("---\ntitle: T\ndocname: draft-x-latest\n---\n", dimensions=DIMENSIONS)


def test_asking_for_no_dimension_is_refused():
    with pytest.raises(JudgeError, match="dimension"):
        _prompt(DRAFT, dimensions=())


# --- reading the reply -------------------------------------------------------


def test_a_reply_wrapped_in_prose_is_read():
    """Copied from the optimize judge's parser, which tolerates prose around
    the object because models write it."""
    report = judge_draft(
        DRAFT,
        lambda prompt: f"Here is my verdict.\n\n{reply_text()}\n\nHope that helps.",
        dimensions=DIMENSIONS,
    )

    assert report.scores == {"structure": 4, "clarity": 3}


def test_quotes_are_optional_and_default_to_empty():
    report = judge_draft(DRAFT, lambda prompt: reply_text(), dimensions=DIMENSIONS)

    assert report.quotes == ()


def test_quotes_are_carried_in_the_order_given():
    report = judge_draft(
        DRAFT,
        lambda prompt: reply_text(quotes=["first span", "second span"]),
        dimensions=DIMENSIONS,
    )

    assert report.quotes == ("first span", "second span")


@pytest.mark.parametrize(
    "reply",
    [
        "no object here at all",
        "[1, 2]",
        '{"scores": [4, 3]}',
        '{"quotes": []}',
        '{"scores": {"structure": 4}}',
        '{"scores": {"structure": 4, "clarity": 3, "tone": 5}}',
        '{"scores": {"structure": 4.5, "clarity": 3}}',
        '{"scores": {"structure": "4", "clarity": 3}}',
        '{"scores": {"structure": true, "clarity": 3}}',
        '{"scores": {"structure": 4, "clarity": 3}, "quotes": "one"}',
        '{"scores": {"structure": 4, "clarity": 3}, "quotes": [7]}',
        '{"scores": {"structure": 4, "clarity": 3}, "quotes": null}',
    ],
    ids=[
        "no-object",
        "not-a-mapping",
        "scores-not-a-mapping",
        "scores-absent",
        "dimension-missing",
        "dimension-invented",
        "score-not-an-integer",
        "score-a-string",
        "score-a-boolean",
        "quotes-a-string",
        "quotes-of-non-strings",
        "quotes-explicitly-null",
    ],
)
def test_a_reply_outside_the_pinned_shape_is_refused(reply):
    """The shape is the contract Task 8 reads, so anything else is a fault and
    not a low score. ``true`` is called out because ``True == 1`` in Python and
    would otherwise be graded as a one, and an explicit ``null`` because an
    omission and a null are different claims."""
    with pytest.raises(JudgeError):
        judge_draft(DRAFT, lambda prompt: reply, dimensions=DIMENSIONS)


def test_a_score_outside_the_rubric_scale_is_still_read():
    """The pinned shape says "an int" and says nothing about a range, so a 9 is
    a datum about the judge rather than a parse failure. Task 8 decides what to
    do with it; this refuses to decide for it."""
    report = judge_draft(
        DRAFT,
        lambda prompt: reply_text({"structure": 9, "clarity": 0}),
        dimensions=DIMENSIONS,
    )

    assert report.scores == {"structure": 9, "clarity": 0}


# --- which model answered ----------------------------------------------------


def test_the_model_recorded_is_the_one_that_answered(profile, tmp_path):
    """Both halves are asserted, or the test would not show the two sources
    disagreeing: the transport was asked for one model and the session reported
    another."""
    control(profile, reply="raw", text=reply_text(), model="reported-model")
    transport = judge_transport(str(STUB), profile, "asked-for-model")

    report = judge_draft(DRAFT, transport, dimensions=DIMENSIONS)

    assert report.model == "reported-model"
    assert transport.model == "asked-for-model"


def test_a_transport_that_reports_nothing_leaves_the_model_unmeasured():
    """A bare callable has no init event to read, and an unmeasured model is
    recorded as unmeasured rather than as the id somebody asked for."""
    report = judge_draft(DRAFT, lambda prompt: reply_text(), dimensions=DIMENSIONS)

    assert report.model is None


# --- where the judge runs ----------------------------------------------------


def test_the_judge_runs_from_a_directory_that_names_no_project(profile, tmp_path):
    """The first probe for this row named "PANTHER" from its cwd string alone,
    with nothing in its prompt to say so."""
    control(profile, reply="raw", text=reply_text())

    judge_draft(DRAFT, judge_transport(str(STUB), profile, "m"), dimensions=DIMENSIONS)

    (recorded,) = calls(profile)
    for token in ("ai_rfc", "arfc", "PANTHER"):
        assert token not in recorded["cwd"]
    repository = Path(__file__).resolve().parents[2]
    assert not recorded["cwd"].startswith(str(repository))


def test_what_actually_reaches_the_child_is_blinded(profile, tmp_path):
    """The assertions above read ``_prompt``'s return; this reads the bytes the
    stub was handed, so nothing between the two can put a leak back."""
    control(profile, reply="raw", text=reply_text())

    judge_draft(DRAFT, judge_transport(str(STUB), profile, "m"), dimensions=DIMENSIONS)

    (recorded,) = calls(profile)
    for leak in ("ai_rfc", "elniak", "docname", "Crochet"):
        assert leak not in recorded["stdin"]
    assert "A peer MUST close the connection" in recorded["stdin"]


# --- quote verification ------------------------------------------------------


def test_a_quote_the_draft_does_not_contain_is_a_finding():
    """Quote-verified is the property that makes a secondary judge tolerable:
    a judge that cites text the draft does not contain has not graded it."""
    report = JudgeReport(
        scores={"structure": 4},
        quotes=("a sentence the draft never had",),
        model="fake-lm",
    )

    assert verify_quotes(report, "the draft body") == (
        "a sentence the draft never had",
    )


def test_a_quote_differing_only_in_whitespace_verifies():
    report = JudgeReport(scores={"structure": 4}, quotes=("two  words",))

    assert verify_quotes(report, "two words here") == ()


def test_a_quote_the_draft_wrapped_across_lines_verifies():
    """A model quoting a sentence off a hard-wrapped draft sends it on one
    line, so normalising only the quote side would fail every long quote."""
    report = JudgeReport(scores={"structure": 4}, quotes=("close the connection",))

    assert verify_quotes(report, "A peer MUST close the\nconnection.") == ()


def test_an_empty_quote_is_a_finding():
    """It is a substring of everything and cites nothing, so a substring check
    alone would call it verified."""
    report = JudgeReport(scores={"structure": 4}, quotes=("", "   "))

    assert verify_quotes(report, "the draft body") == ("", "   ")


def test_a_report_with_no_quotes_has_nothing_unverified():
    assert verify_quotes(JudgeReport(scores={"structure": 4}), "anything") == ()


def test_only_the_unverified_quotes_come_back():
    report = JudgeReport(
        scores={"structure": 4}, quotes=("two words", "never written", "here")
    )

    assert verify_quotes(report, "two words here") == ("never written",)


def test_a_judge_quoting_the_draft_it_was_shown_verifies(profile, tmp_path):
    """End to end against the blinded body, which is what the judge saw: a
    quote is checked against the text in front of the judge, not the file."""
    quote = "A peer MUST close the connection on a malformed frame."
    control(profile, reply="raw", text=reply_text(quotes=[quote, "invented"]))

    report = judge_draft(
        DRAFT, judge_transport(str(STUB), profile, "m"), dimensions=DIMENSIONS
    )

    assert verify_quotes(report, blinded_body(DRAFT)) == ("invented",)
