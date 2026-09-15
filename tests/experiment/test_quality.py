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
import shutil
from pathlib import Path

import pytest

from ai_rfc.draft.checkpoint import MANIFEST_FILE
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
    # The discriminator. `uncited == []` alone is satisfied just as well by "no
    # manifest was read at all", because `lint` initialises it empty and only
    # fills it when handed a manifest. `cited_fraction` is None in that case
    # and a real float here, so it is what tells the two causes apart — and
    # `manifest_status` names the cause outright.
    assert rows[0]["citations"]["cited_fraction"] == 1.0
    assert rows[0]["manifest_error"] is None
    assert [row["manifest_status"] for row in rows] == ["read", "read"]


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
        "manifest_error",
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


def _make_frozen_manifest_unreadable(workspace: Path, cluster_id: str) -> None:
    """Corrupt one revision's frozen manifest, as a stale pilot workspace has.

    The aioquic pilot's C1 checkpoint carries a ``level`` the current enum will
    not load, and a frozen workspace is evidence that is never re-gated — so an
    unreadable frozen manifest is a real state, not a hypothetical one.
    """
    (workspace / "checkpoints" / cluster_id / MANIFEST_FILE).write_text("rfc: [\n")


def test_an_unreadable_frozen_manifest_is_reported_not_silently_zeroed(
    two_tag_workspace,
):
    """R16: "not measured" must never be recorded as "measured, and clean".

    Without the reason in the payload, a revision whose manifest failed to load
    is byte-identical to one that cited everything: ``uncited`` is empty and
    ``cited_fraction`` is None either way as far as the row shows.
    """
    clean = revision_lints(two_tag_workspace)
    _make_frozen_manifest_unreadable(two_tag_workspace, clean[0]["cluster_id"])
    damaged = revision_lints(two_tag_workspace)

    assert damaged[0]["manifest_status"] == "unloadable"
    assert damaged[1]["manifest_status"] == "read"
    assert damaged[0]["manifest_error"] is not None
    assert damaged[1]["manifest_error"] is None
    # Every metric the manifest fed carries its own "unmeasured", so a consumer
    # needs no list of which metrics those are.
    assert damaged[0]["citations"]["cited_fraction"] is None
    assert damaged[0]["citations"]["uncited"] is None
    assert damaged[0]["structures"] == {"defined": None, "rendered": None}
    assert damaged[1]["citations"]["cited_fraction"] == 1.0
    # The text-only metrics are still measured: losing the manifest costs the
    # comparison, not the whole revision.
    assert damaged[0]["citations"]["tokens"] == clean[0]["citations"]["tokens"]


def test_a_checkpoint_that_never_landed_is_a_different_report_from_a_broken_one(
    two_tag_workspace,
):
    """A run killed mid-round leaves a tag with no checkpoint; that is evidence.

    It is reported and the analysis continues, so the revisions either side of
    it still measure. The class is recorded, not just a message, because
    "the checkpoint never landed" and "the checkpoint is there and will not
    load" are different facts about the run.
    """
    clean = revision_lints(two_tag_workspace)
    shutil.rmtree(two_tag_workspace / "checkpoints" / clean[0]["cluster_id"])
    rows = revision_lints(two_tag_workspace)

    assert [row["manifest_status"] for row in rows] == ["missing", "read"]
    assert clean[0]["cluster_id"] in rows[0]["manifest_error"]
    assert rows[0]["citations"]["cited_fraction"] is None
    assert rows[1]["citations"]["cited_fraction"] == 1.0


def test_a_frozen_manifest_that_exists_and_will_not_open_is_raised(two_tag_workspace):
    """A broken instrument fails loudly instead of emitting zeros.

    The swallow exists for stale evidence, not for a filesystem that refuses a
    path this function has just computed. A directory where the manifest
    belongs is that refusal, deterministically and without touching a mode.
    """
    rows = revision_lints(two_tag_workspace)
    frozen = two_tag_workspace / "checkpoints" / rows[0]["cluster_id"] / MANIFEST_FILE
    frozen.unlink()
    frozen.mkdir()
    with pytest.raises(OSError):
        revision_lints(two_tag_workspace)


def test_an_unreadable_manifest_is_not_counted_as_a_prose_regression(two_tag_workspace):
    """The instrument's own failure must not read as one more thing to fix.

    ``LintReport.findings`` prepends a line whenever ``manifest_error`` is set,
    so a ``finding_count`` taken straight off it moves by one for a reason that
    is nothing to do with the draft.
    """
    _, text = draft_text(two_tag_workspace / "draft", FIRST_TAG)
    # The +1 this pins is exactly what the reduction has to leave out.
    assert len(lint(text, manifest_error="x").findings) == len(lint(text).findings) + 1
    assert (
        reduce_lint(lint(text, manifest_error="x"))["finding_count"]
        == reduce_lint(lint(text))["finding_count"]
    )


def test_the_table_will_not_show_an_unmeasured_metric_as_a_number(two_tag_workspace):
    """R16 downstream: two unreadable revisions must not render as "no change"."""
    clean = revision_lints(two_tag_workspace)
    _make_frozen_manifest_unreadable(two_tag_workspace, clean[0]["cluster_id"])
    damaged = revision_lints(two_tag_workspace)
    table = compare_lints(
        damaged[0], damaged[1], before_label=FIRST_TAG, after_label=SECOND_TAG
    )

    assert "| citations.uncited | — | 0 | — |" in table
    assert "| structures.defined | — | 0 | — |" in table
    # The reason is a row of its own, so the dashes are explained and not a
    # second unexplained absence.
    assert "| manifest_error | " in table
    # What did not need the manifest is still a number.
    assert "| citations.tokens | 1 | 2 | +1 |" in table


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
    # The caller's `out` is honoured: nothing was written into the run.
    stored = json.loads((out / "build" / "build-report.json").read_text())
    assert stored["ref"] == SECOND_TAG


def test_the_comparison_table_escapes_a_pipe_in_a_metric_name():
    table = compare_lints(
        {"sections|x": 1}, {"sections|x": 2}, before_label="b", after_label="a"
    )
    row = table.splitlines()[2]  # header, separator, then the first data row
    # Four columns = five structural rails; the escaped pipe must not count.
    assert row.count("|") - row.count("\\|") == 5


def test_a_pipe_in_a_label_cannot_widen_the_separator():
    """The rule row is sized from the header, so an escaped pipe must not count.

    ``cell`` escapes a pipe rather than removing it, so a header carrying one
    holds five structural rails and one escape. A separator that counted every
    pipe would lay five columns of rule under a four-column header — which is
    why this table used to be sized from a constant rail rather than from the
    header it sits under.
    """
    table = compare_lints({"m": 1}, {"m": 2}, before_label="be|fore", after_label="a")
    header, rule = table.splitlines()[:2]
    assert header.count("|") - header.count("\\|") == 5
    assert rule == "|---|---|---|---|"


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
