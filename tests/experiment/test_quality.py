"""The draft-quality instrument: a lint per revision, and one final build.

The workspace these read is one two fake sessions actually drove, not a
hand-written revision map. What is under test is that each revision is
measured against the manifest *its own* checkpoint froze, and only a workspace
whose later cluster mined a claim the earlier one never saw can tell that
apart from measuring every revision against the live manifest — so the fixture
below is built to make the two answers differ, and a negative control asserts
that they do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_rfc.draft.gate import draft_text
from ai_rfc.draft.lint import lint
from ai_rfc.experiment.quality import (
    compare_lints,
    final_build,
    reduce_lint,
    revision_lints,
)
from ai_rfc.experiment.workspace import copy_workspace
from ai_rfc.schema import load

from .conftest import REPO_ROOT
from .test_fake_claude import _cluster_steps, _launch, _one_round

FIRST_TAG = "draft-test-fixture-01"
SECOND_TAG = "draft-test-fixture-02"
#: The claim the second cluster mines, which the first cluster's checkpoint
#: has never heard of. It is the whole difference between the two manifests.
LATER_CLAIM = "t:4.1"


@pytest.fixture
def two_tag_workspace(wide_pristine, tmp_path, scenario_workspace) -> Path:
    """A workspace two sessions drove: one cluster, one checkpoint, one tag each.

    ``_launch``'s third argument is any git repository and is never read; the
    constant is passed rather than the ``panther_repo`` fixture so that
    fixture's own docstring, which enumerates its requesters, stays true.

    Args:
        wide_pristine: A prepared workspace whose window holds both clusters.
        tmp_path: The test's own directory.
        scenario_workspace: Names the scenario and says where it must live.

    Returns:
        The driven workspace, holding two revisions and two frozen checkpoints.
    """
    profile = tmp_path / "profile"
    workspace = scenario_workspace(
        profile,
        "quality-two-tags",
        {
            "arm": "A",
            "steps": [
                *_one_round(_cluster_steps(1, FIRST_TAG, "t:3.1"), 1),
                *_one_round(_cluster_steps(2, SECOND_TAG, LATER_CLAIM), 2),
            ],
        },
    )
    copy_workspace(wide_pristine, workspace)
    # A session is one cluster's work and reads which round is outstanding off
    # the workspace, so two launches are the two clusters, in order.
    _launch(profile, workspace, REPO_ROOT)
    _launch(profile, workspace, REPO_ROOT)
    return workspace


def test_each_revision_is_linted_against_its_own_frozen_manifest(two_tag_workspace):
    """R3: the live manifest would mark every early structure unrendered."""
    rows = revision_lints(two_tag_workspace)
    assert [row["number"] for row in rows] == [1, 2]
    # Revision 01 cited every claim ITS OWN checkpoint knew. Linted against the
    # final manifest it would report the second cluster's claims as uncited.
    assert rows[0]["citations"]["uncited"] == []


def test_the_live_manifest_calls_the_first_revision_incomplete(two_tag_workspace):
    """The negative control: without it, the assertion above proves nothing.

    If both revisions cited the same claims the frozen and the live manifest
    would agree, and a ``revision_lints`` that ignored the checkpoints
    entirely would still pass.
    """
    _, text = draft_text(two_tag_workspace / "draft", FIRST_TAG)
    live = load(two_tag_workspace / "manifest.yaml")
    assert lint(text, manifest=live).citations["uncited"] == [LATER_CLAIM]


def test_every_revision_names_the_checkpoint_its_own_entry_pins(two_tag_workspace):
    """The rows carry the revision map's own provenance, not an ordinal."""
    rows = revision_lints(two_tag_workspace)
    assert [row["tag"] for row in rows] == [FIRST_TAG, SECOND_TAG]
    assert [row["kind"] for row in rows] == ["cluster", "cluster"]
    assert all(row["cluster_id"] for row in rows)
    assert rows[1]["citations"]["uncited"] == []


def test_the_reduction_survives_a_json_round_trip(two_tag_workspace):
    """D-33: a tuple deserialises as a list and a Path does not serialise.

    ``test_metrics.py`` asserts the stored analysis equals the value in hand,
    so any tuple that reached this payload would fail there rather than here.
    """
    rows = revision_lints(two_tag_workspace)
    assert json.loads(json.dumps(rows)) == rows


def test_the_reduction_keeps_the_report_field_names(two_tag_workspace):
    """A caller reads ``row["citations"]["uncited"]``, so the nesting is contract."""
    rows = revision_lints(two_tag_workspace)
    _, text = draft_text(two_tag_workspace / "draft", FIRST_TAG)
    frozen = load(
        two_tag_workspace / "checkpoints" / rows[0]["cluster_id"] / "manifest.yaml"
    )
    reduced = reduce_lint(lint(text, manifest=frozen))
    assert set(reduced) == {
        "sections",
        "abstract",
        "keywords",
        "blocks",
        "citations",
        "structures",
        "narration_count",
        "finding_count",
    }
    assert reduced["citations"]["tokens"] == 1
    assert reduced["citations"]["cited_fraction"] == 1.0
    assert reduced["abstract"]["word_count"] > 0


def test_the_build_is_skipped_without_a_toolchain(tmp_path):
    assert final_build(tmp_path, None, tmp_path / "out") is None


def test_the_final_build_builds_the_highest_numbered_tag(
    two_tag_workspace, toolchain_record, tmp_path
):
    """R4: the instrument reports the draft as the run last left it.

    ``toolchain_record``'s ``make`` is a no-op that exits zero, so this
    exercises the ref choice and the reduction, not the template.
    """
    out = tmp_path / "analysis" / "draft-build"
    record = final_build(two_tag_workspace, str(toolchain_record), out)
    assert record == {
        "exit_code": 0,
        "findings": [],
        "idnits": {},
        "broken_references": [],
        "diagnostic_counts": {},
    }
    stored = json.loads((out / "build" / "build-report.json").read_text())
    assert stored["ref"] == SECOND_TAG
    # The evidence directory is the caller's to choose, and it is not the run's.
    assert two_tag_workspace not in out.parents


def test_the_comparison_table_escapes_a_pipe_in_a_metric_name():
    table = compare_lints(
        {"sections|x": 1}, {"sections|x": 2}, before_label="b", after_label="a"
    )
    row = table.splitlines()[2]  # header, separator, then the first data row
    # Four columns = five structural rails; the escaped pipe must not count.
    assert row.count("|") - row.count("\\|") == 5


def test_the_comparison_table_flattens_and_subtracts(two_tag_workspace):
    """The reduction is nested, so the table names a metric by its full path."""
    rows = revision_lints(two_tag_workspace)
    table = compare_lints(
        rows[0], rows[1], before_label=FIRST_TAG, after_label=SECOND_TAG
    )
    header, separator = table.splitlines()[:2]
    assert header == f"| metric | {FIRST_TAG} | {SECOND_TAG} | delta |"
    assert separator == "|---|---|---|---|"
    assert "| citations.tokens | 1 | 2 | +1 |" in table
    # A list-valued metric is rendered and subtracted as its length: the table
    # compares magnitudes, and the items themselves are in the lint reports.
    assert "| citations.uncited | 0 | 0 | 0 |" in table


def test_a_metric_only_one_side_carries_has_no_delta():
    """A missing side is the em dash, never a zero that reads as "no change"."""
    table = compare_lints(
        {"only_before": 3}, {"only_after": 4}, before_label="b", after_label="a"
    )
    assert "| only_before | 3 | — | — |" in table
    assert "| only_after | — | 4 | — |" in table
