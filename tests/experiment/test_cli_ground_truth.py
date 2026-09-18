"""The ``ground-truth`` verb: one draft scored against the pinned dataset.

Nothing here reaches a model, and one test asserts that rather than assuming
it: the axis exists because a judge's opinion is not evidence, so a verb that
quietly asked one would be measuring the thing it was built to be independent
of.

The drafts are literals written here, never text derived from the dataset. A
draft built by joining the dataset's own statements would shrink with any
mutation of the dataset and go on scoring 1.0, which is the shape of an
expectation that pins nothing.
"""

import json
import subprocess
from pathlib import Path

import pytest

from ai_rfc.experiment import cli
from ai_rfc.experiment.ground_truth import (
    EXCLUDED_DECIMAL,
    EXCLUDED_NOT_A_NUMBER,
    NEARBY_CHARS,
)

pytestmark = pytest.mark.unit

#: A draft stating two error codes correctly. Short deliberately: what these
#: tests check is the verb's plumbing, and the predicate itself is measured
#: over the whole dataset in ``test_ground_truth.py``.
DRAFT = """\
The HTTP/3 error code H3_NO_ERROR is 0x100.

The HTTP/3 error code H3_INTERNAL_ERROR is 0x102.
"""


def _score(tmp_path: Path, text: str) -> tuple[int, Path]:
    """Run the verb over one draft.

    Args:
        tmp_path: The test's own directory; the draft and the output land in it.
        text: The draft body.

    Returns:
        The exit code, and the path the report should have been written to.
    """
    draft = tmp_path / "draft.md"
    draft.write_text(text)
    out = tmp_path / "out"
    code = cli.main(["ground-truth", str(draft), "--out", str(out)])
    return code, out / "ground-truth.json"


def test_the_ground_truth_verb_is_dispatched_not_silently_zero(tmp_path):
    """D-3: ``run``'s if/elif chain has no ``else``.

    A verb wired into ``configure`` but never given an ``elif`` parses its
    arguments, falls through every branch and returns 0 having done nothing.
    The exit code alone cannot catch that, so the assertion is on the file.
    """
    code, report = _score(tmp_path, DRAFT)
    assert report.exists(), "the verb parsed but never dispatched"
    assert code == 0


def test_the_report_names_its_denominator_and_the_entries_it_left_out(tmp_path):
    """A recall of 0.9 is unreadable without the count it was taken over."""
    _, report = _score(tmp_path, DRAFT)
    payload = json.loads(report.read_text())
    assert payload["scored"] == 35
    assert payload["excluded"] == [
        "h3-const-alpn",
        "h3-const-reserved-settings",
        "h3-stream-control",
        "h3-stream-push",
        "h3-stream-qpack-encoder",
        "h3-stream-qpack-decoder",
    ]
    assert payload["matched"] == ["h3-error-no-error", "h3-error-internal-error"]
    assert payload["recall"] == pytest.approx(2 / 35)
    assert payload["claim_accuracy"] == pytest.approx(1.0)


def test_the_report_says_why_each_excluded_entry_could_not_be_scored(tmp_path):
    """Six ids and one sentence would not say which entry the sentence is about.

    The two reasons are different facts about the dataset: ``["h3"]`` is a
    value no matcher over prose could ever score, while a stream type's ``2``
    is one this matcher cannot score because a character window cannot tell a
    constant from a count. A reader deciding whether the denominator is fair
    needs to know which of those each entry is, and a lump count cannot say.
    """
    _, report = _score(tmp_path, DRAFT)
    because = json.loads(report.read_text())["excluded_because"]
    assert set(because) == set(json.loads(report.read_text())["excluded"])
    assert because["h3-const-alpn"] == EXCLUDED_NOT_A_NUMBER
    assert because["h3-stream-qpack-encoder"] == EXCLUDED_DECIMAL


def test_the_report_carries_every_id_the_two_ratios_were_taken_over(tmp_path):
    """The lists a low score is read through, on a draft that gets one wrong.

    ``recall`` and ``claim_accuracy`` are two numbers over the same entries,
    and the only way to see *which* entries is these lists. Emptying
    ``mismatched`` would turn a wrong claim into a silent one, and emptying
    ``missed`` would leave the denominator unaccounted for. The identity
    asserted last is what makes that unfakeable: the scored entries are
    partitioned by ``matched``, ``mismatched`` and ``missed``, so a list that
    is emptied cannot be emptied alone.
    """
    _, report = _score(tmp_path, "H3_NO_ERROR is 0x1000. H3_INTERNAL_ERROR is 0x102.")
    payload = json.loads(report.read_text())
    assert payload["mismatched"] == ["h3-error-no-error"]
    assert payload["matched"] == ["h3-error-internal-error"]
    assert "h3-error-no-error" not in payload["missed"]
    assert payload["scored"] == len(payload["matched"]) + len(
        payload["mismatched"]
    ) + len(payload["missed"])


def test_an_unattempted_draft_records_a_null_accuracy_never_a_zero(tmp_path):
    """The central distinction, at the one place a reader will meet it.

    A draft that stated no checkable fact has an *unmeasured* claim accuracy.
    Written as 0.0 it would read as "every claim it made was wrong", and as
    1.0 as "every claim it made was right"; both are claims about claims that
    were never made. ``recall`` is a measured zero on the same draft -- 39
    entries were looked for and none found -- so the two must not be written
    the same way.
    """
    _, report = _score(tmp_path, "This draft states nothing checkable.\n")
    payload = json.loads(report.read_text())
    assert payload["claim_accuracy"] is None
    assert payload["recall"] == 0.0
    assert payload["scored"] == 35


def test_the_terminal_says_not_measured_rather_than_printing_a_zero(tmp_path, capsys):
    """The JSON keeps ``null``; the terminal has to keep it in words.

    An operator reads the four lines this verb prints far more often than the
    file it writes, and ``claim accuracy: 0.0`` there would say "every claim
    the draft made was wrong" about a draft that made none. The null in the
    JSON is no help to a reader who never opens it, so the distinction has to
    survive into the prose.
    """
    _score(tmp_path, "This draft states nothing checkable.\n")
    out = capsys.readouterr().out
    assert "claim accuracy: not measured" in out
    assert "claim accuracy: 0.0" not in out
    # The measured zero on the same draft is still printed as a number, which
    # is what makes the line above a distinction and not a blanket silence.
    assert "recall: 0 of 35 scored" in out


def test_the_terminal_does_not_call_every_excluded_entry_a_non_number(tmp_path, capsys):
    """One reason was true of all of them, and then a second reason arrived.

    Four of the six are excluded because their value is a bare decimal
    numeral, which *is* a number. A line calling all six "entries whose value
    is not a number" would be false of two thirds of them, so the counts are
    printed per reason.
    """
    _score(tmp_path, DRAFT)
    out = capsys.readouterr().out
    assert "excluded: 6 entries nothing could score" in out
    assert f"2 because {EXCLUDED_NOT_A_NUMBER}" in out
    assert f"4 because {EXCLUDED_DECIMAL}" in out


def test_the_report_records_the_dataset_the_draft_was_scored_against(tmp_path):
    """A score is about a draft *and* a pin; the pin travels with it."""
    _, report = _score(tmp_path, DRAFT)
    payload = json.loads(report.read_text())
    assert payload["dataset"]["commit"] == "6d36838d008c2202c337142fa07e8bf80e96bac8"
    assert payload["dataset"]["repository"]
    # The window is part of what the numbers mean: two reports taken at
    # different widths are not comparable, and nothing else would say so.
    assert payload["nearby_chars"] == NEARBY_CHARS


def _draft_with_reach(reach: int, *, before: bool = False) -> str:
    """A draft whose value lies ``reach`` characters from its symbol.

    Built for the normalised text the matcher searches, where runs of
    whitespace have collapsed to one space: ``H3_NO_ERROR`` is 11 characters
    and ``0x100`` is five, separated by one space, ``reach - 7`` filler
    characters and another space. The filler is a single unbroken run of ``x``
    because a run of spaces would collapse and a word with a digit in it would
    be a second token.

    Args:
        reach: Characters the window has to span, from the near end of the
            symbol to the far end of the value.
        before: Put the value ahead of the symbol rather than after it. The
            window reaches both ways and the two halves are separate
            expressions in the source, so a narrowing of one is invisible to a
            draft that only exercises the other.

    Returns:
        The draft body.
    """
    filler = "x" * (reach - 7)
    if before:
        return f"0x100 {filler} H3_NO_ERROR\n"
    return f"H3_NO_ERROR {filler} 0x100\n"


def test_the_window_the_report_names_is_the_window_the_matcher_read(tmp_path):
    """Read the width out of the report, then hold the matcher to it.

    Asserting ``payload["nearby_chars"] == NEARBY_CHARS`` compares the report
    against the constant it was written from, so it holds however far the
    matcher has drifted from either. These drafts are built off the width the
    *report* claims -- one whose value ends on the last character the window
    covers, one a single character past it, one clear of it -- so a matcher
    reading any other width fails on one side or the other.

    Note what the one-character-past draft is *not* asserted to be. The window
    is applied by slicing the text, so a value straddling its edge is cut in
    half and the front half is a token in its own right: ``0x100`` one
    character out yields ``0x10``, and the entry is reported as an attempt
    carrying the wrong value rather than as a miss. That is the matcher as it
    stands, recorded here rather than assumed away; the third draft is what
    shows the entry can leave the numerator and the attempts together.
    """
    _, report = _score(tmp_path, DRAFT)
    width = json.loads(report.read_text())["nearby_chars"]

    _, inside = _score(tmp_path, _draft_with_reach(width))
    assert json.loads(inside.read_text())["matched"] == ["h3-error-no-error"]

    _, edge = _score(tmp_path, _draft_with_reach(width + 1))
    assert json.loads(edge.read_text())["matched"] == []

    _, outside = _score(tmp_path, _draft_with_reach(width + 8))
    assert json.loads(outside.read_text())["attempted"] == []
    assert "h3-error-no-error" in json.loads(outside.read_text())["missed"]

    # And the same boundary with the value written first, because a draft says
    # "0x100 (H3_NO_ERROR)" as readily as the other order and the two halves of
    # the window are separate expressions: narrowing only the backward one
    # leaves every forward probe above green.
    _, behind = _score(tmp_path, _draft_with_reach(width, before=True))
    assert json.loads(behind.read_text())["matched"] == ["h3-error-no-error"]

    _, far_behind = _score(tmp_path, _draft_with_reach(width + 1, before=True))
    assert json.loads(far_behind.read_text())["attempted"] == []


def test_a_draft_that_is_not_utf8_is_one_line_and_exit_1_not_a_traceback(tmp_path):
    """``UnicodeDecodeError`` is a ``ValueError``, which ``run`` does not catch.

    Every other bad input to this package leaves one ``error:`` line and exit
    1. A draft saved in a byte encoding would have left a traceback instead,
    because the exception it raises is outside the tuple ``run`` handles, and
    an operator cannot tell a traceback from a crash of the tool.
    """
    draft = tmp_path / "draft.md"
    draft.write_bytes(b"H3_NO_ERROR is 0x100.\n\xff\xfe not text\n")
    out = tmp_path / "out"
    code = cli.main(["ground-truth", str(draft), "--out", str(out)])
    assert code == 1
    assert not (out / "ground-truth.json").exists()


def test_the_verb_consults_nothing(tmp_path, monkeypatch):
    """No model call anywhere: that independence is the whole axis.

    Asserted by breaking the two ways this package launches anything rather
    than by reading the branch, because an import added later would not show
    up in a reading of the code this test was written against.
    """

    def refuse(*args, **kwargs):
        raise AssertionError("the ground-truth verb launched a subprocess")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    code, report = _score(tmp_path, DRAFT)
    assert code == 0
    assert report.exists()
