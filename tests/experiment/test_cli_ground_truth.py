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
from ai_rfc.experiment.ground_truth import NEARBY_CHARS

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
    assert payload["scored"] == 39
    assert payload["excluded"] == ["h3-const-alpn", "h3-const-reserved-settings"]
    assert payload["matched"] == ["h3-error-no-error", "h3-error-internal-error"]
    assert payload["recall"] == pytest.approx(2 / 39)
    assert payload["claim_accuracy"] == pytest.approx(1.0)


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
    assert payload["scored"] == 39


def test_the_report_records_the_dataset_the_draft_was_scored_against(tmp_path):
    """A score is about a draft *and* a pin; the pin travels with it."""
    _, report = _score(tmp_path, DRAFT)
    payload = json.loads(report.read_text())
    assert payload["dataset"]["commit"] == "6d36838d008c2202c337142fa07e8bf80e96bac8"
    assert payload["dataset"]["repository"]
    # The window is part of what the numbers mean: two reports taken at
    # different widths are not comparable, and nothing else would say so.
    assert payload["nearby_chars"] == NEARBY_CHARS


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
