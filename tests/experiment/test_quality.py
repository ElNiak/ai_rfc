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
import yaml

from ai_rfc.draft.checkpoint import MANIFEST_FILE
from ai_rfc.draft.gate import draft_text
from ai_rfc.draft.lint import lint
from ai_rfc.experiment import quality
from ai_rfc.experiment.quality import (
    _flatten,
    build_not_requested,
    build_tally,
    compare_lints,
    final_build,
    reduce_lint,
    revision_lints,
)
from ai_rfc.experiment.report import render_report
from ai_rfc.experiment.workspace import copy_workspace
from ai_rfc.schema import load
from ai_rfc.server.testing import git as _vcs

from .conftest import REPO_ROOT, append_untagged_revision
from .test_fake_claude import _cluster_steps, _launch, _one_round
from .test_report import _aggregate, _quality_tables

FIRST_TAG = "draft-test-fixture-01"
SECOND_TAG = "draft-test-fixture-02"
#: A third revision the fixture never tags; see `append_untagged_revision`.
UNTAGGED = "draft-test-fixture-03"
#: The cluster that third revision names. No checkpoint directory carries it,
#: so the row's manifest is missing as well as its draft. That second damage is
#: constructed, not what a kill leaves; `append_untagged_revision` says why.
UNCHECKPOINTED_CLUSTER = "c0009-never-ran"
#: The row keys that are provenance rather than measurement. They stay real on
#: a row with no draft; everything else in it must be None.
PROVENANCE = {
    "tag",
    "number",
    "cluster_id",
    "kind",
    "manifest_status",
    "manifest_error",
    "draft_status",
    "draft_error",
}
#: Two defects a manifest is not needed to see: a fenced figure with no
#: citation in the three lines after it closes, and a structure block that
#: opens and never closes. Appended to a real draft, they make the text-only
#: half of the projection non-empty whatever the checkpoint holds.
TEXT_ONLY_DEFECTS = (
    "\n"
    "~~~\n"
    "a diagram nobody cited\n"
    "~~~\n"
    "\n"
    "{::comment}\n"
    "ai_rfc:struct:never-closed begin\n"
    "{:/comment}\n"
)
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
    rows = revision_lints(two_tag_workspace)["revisions"]
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
    rows = revision_lints(two_tag_workspace)["revisions"]
    assert [row["tag"] for row in rows] == [FIRST_TAG, SECOND_TAG]
    assert [row["kind"] for row in rows] == ["cluster", "cluster"]
    assert all(row["cluster_id"] for row in rows)
    assert rows[1]["citations"]["uncited"] == []


def test_the_reduction_survives_a_json_round_trip(two_tag_workspace):
    """D-33: a tuple deserialises as a list and a Path does not serialise.

    ``test_metrics.py`` asserts the stored analysis equals the value in hand,
    so any tuple that reached this payload would fail there rather than here.
    """
    rows = revision_lints(two_tag_workspace)["revisions"]
    assert json.loads(json.dumps(rows)) == rows


def test_the_reduction_keeps_the_report_field_names(two_tag_workspace):
    """A caller reads ``row["citations"]["uncited"]``, so the nesting is contract."""
    rows = revision_lints(two_tag_workspace)["revisions"]
    _, text = draft_text(two_tag_workspace / "draft", FIRST_TAG)
    frozen = load(
        two_tag_workspace / "checkpoints" / rows[0]["cluster_id"] / "manifest.yaml"
    )
    reduced = reduce_lint(lint(text, manifest=frozen))
    assert set(reduced) == {
        "manifest_error",
        "sections",
        "abstract",
        "references",
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
    clean = revision_lints(two_tag_workspace)["revisions"]
    _make_frozen_manifest_unreadable(two_tag_workspace, clean[0]["cluster_id"])
    damaged = revision_lints(two_tag_workspace)["revisions"]

    assert damaged[0]["manifest_status"] == "unloadable"
    assert damaged[1]["manifest_status"] == "read"
    assert damaged[0]["manifest_error"] is not None
    assert damaged[1]["manifest_error"] is None
    # Every metric the manifest fed carries its own "unmeasured", so a consumer
    # needs no list of which metrics those are.
    assert damaged[0]["citations"]["cited_fraction"] is None
    assert damaged[0]["citations"]["uncited"] is None
    # `malformed` is measured from the text alone, so it stays an empty list
    # rather than joining its nulled neighbours: [] here means "looked for and
    # not found", which is exactly what None next to it does not mean.
    assert damaged[0]["structures"] == {
        "malformed": [],
        "defined": None,
        "rendered": None,
    }
    assert damaged[0]["finding_count"] is None
    assert damaged[1]["citations"]["cited_fraction"] == 1.0
    assert isinstance(damaged[1]["finding_count"], int)
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
    clean = revision_lints(two_tag_workspace)["revisions"]
    shutil.rmtree(two_tag_workspace / "checkpoints" / clean[0]["cluster_id"])
    rows = revision_lints(two_tag_workspace)["revisions"]

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
    rows = revision_lints(two_tag_workspace)["revisions"]
    frozen = two_tag_workspace / "checkpoints" / rows[0]["cluster_id"] / MANIFEST_FILE
    frozen.unlink()
    frozen.mkdir()
    with pytest.raises(OSError):
        revision_lints(two_tag_workspace)


def test_a_tag_the_draft_repository_never_got_is_reported_not_raised(
    two_tag_workspace,
):
    """R23: a run killed between appending the entry and tagging is evidence.

    Raising would not stop at the revision: ``analyze_campaign`` builds its
    runs in a dict comprehension, so one such run would abort the aggregate
    for every run in the campaign — the failure class the ``salvage_stream``
    comment in ``metrics.py`` already rules on, and the reason a GEPA
    evaluation of a candidate that died in that window would lose its score
    outright rather than be scored.

    Both sides are asserted in the one workspace. A workspace where every
    revision were absent would prove as little as one where none is: it could
    not tell this apart from an instrument that reports every row unmeasured.
    """
    append_untagged_revision(two_tag_workspace, UNTAGGED, UNCHECKPOINTED_CLUSTER)
    rows = revision_lints(two_tag_workspace)["revisions"]
    measured, absent = rows[1], rows[2]

    assert [row["draft_status"] for row in rows] == ["read", "read", "unreadable"]
    assert UNTAGGED in absent["draft_error"]
    assert measured["draft_error"] is None
    # The row is damaged twice over, so it carries both reasons: nulling
    # `manifest_error` with the metrics would leave `manifest_status` asserting
    # "missing" with nothing to say why.
    assert absent["manifest_status"] == "missing"
    assert absent["manifest_error"] is not None
    assert UNCHECKPOINTED_CLUSTER in absent["manifest_error"]

    # The key sets are compared rather than a list of metric names kept here,
    # so a metric `reduce_lint` grows later cannot end up measured on one row
    # and absent from the other.
    flat_absent, flat_measured = _flatten(absent), _flatten(measured)
    assert set(flat_absent) == set(flat_measured)
    assert all(flat_absent[name] is None for name in set(flat_absent) - PROVENANCE)
    # None and not zero. Linting the empty string would score each of these,
    # and an absent revision would then read as an empty draft.
    assert absent["citations"]["tokens"] is None
    assert absent["narration_count"] is None
    assert absent["abstract"]["word_count"] is None
    assert absent["references"] == {
        "normative": None,
        "informative": None,
        "inline": None,
    }
    # The sibling beside it in the same workspace still measures.
    assert measured["citations"]["cited_fraction"] == 1.0
    assert measured["narration_count"] is not None


def test_git_that_cannot_be_invoked_is_raised_and_not_reported(
    two_tag_workspace, monkeypatch
):
    """The swallow is for the evidence, not for a broken instrument.

    ``_draft_at`` catches ``GateError`` alone, so git that cannot be run at
    all still fails loudly instead of reporting every revision unmeasured. A
    caught ``Exception`` would produce a row of nulls per revision, and
    ``draft_status`` could not separate that from a run that tagged nothing:
    it reads ``unreadable`` either way. Only ``draft_error`` carries the
    distinction — ``No such file or directory: 'git'`` rather than ``could not
    list its tree: fatal: ...`` — so a reader grouping by the status alone
    would lose it. An empty ``PATH`` is that failure deterministically, and
    reading the revision map runs no git, so the loop is reached first.
    """
    monkeypatch.setenv("PATH", "")
    with pytest.raises(OSError):
        revision_lints(two_tag_workspace)


def test_an_unreadable_map_and_a_map_recording_none_do_not_collapse(
    two_tag_workspace,
):
    """R27: an empty ``revisions`` means "none recorded" or "could not tell".

    A workspace no run has tagged in legitimately reads ``revisions: {}``, so
    the empty list cannot carry the difference by itself — the same
    "unmeasured is not zero" rule the rows already enforce, one level up. The
    two sides are built in the one workspace and the list is held *fixed*, so
    only the status can tell them apart.
    """
    measured = revision_lints(two_tag_workspace)
    assert measured["revisions_status"] == "read"
    assert measured["revisions_error"] is None
    assert len(measured["revisions"]) == 2

    path = two_tag_workspace / "revisions.yaml"
    path.write_text("revisions: {}\n")
    none_recorded = revision_lints(two_tag_workspace)
    path.write_text("this is not a revision map\n")
    unreadable = revision_lints(two_tag_workspace)
    path.unlink()
    absent = revision_lints(two_tag_workspace)

    # Three states, one list. Only the status separates them.
    assert none_recorded["revisions"] == unreadable["revisions"] == []
    assert absent["revisions"] == []
    assert none_recorded["revisions_status"] == "read"
    assert unreadable["revisions_status"] == "unreadable"
    assert absent["revisions_status"] == "missing"
    assert none_recorded["revisions_error"] is None
    assert "revisions.yaml" in unreadable["revisions_error"]
    assert "revisions.yaml" in absent["revisions_error"]


def test_a_revision_map_an_arm_deleted_is_reported_not_raised(two_tag_workspace):
    """R29: absence is incomplete evidence, not a mis-addressed instrument.

    The path is the right one and the file is not at it, which is the same
    shape as a tag the draft repository does not hold. Two facts put it on the
    evidence side rather than the instrument side: ``ledger._entries``, another
    reader of this same file, has always returned ``{}`` on absence; and
    ``revisions.yaml`` is in ``audit.STATE_FILES`` *because arms hand-edit it*,
    and an arm has shell access, so an arm deleting it is reachable.
    """
    (two_tag_workspace / "revisions.yaml").unlink()
    payload = revision_lints(two_tag_workspace)

    assert payload["revisions"] == []
    assert payload["revisions_status"] == "missing"
    assert "revisions.yaml" in payload["revisions_error"]


def test_a_revision_map_that_is_not_valid_yaml_is_reported(two_tag_workspace):
    """R30: the parser refusing the bytes is damaged evidence, like a bad shape.

    **This state does not reach here through ``analyze_run`` today**, and that
    was measured rather than assumed: ``cluster_artifacts`` runs at
    ``metrics.py:324`` and reaches ``ledger._entries``, which turns the same
    ``yaml.YAMLError`` into a ``LedgerParseError`` (``ledger.py:135-137``)
    before ``quality`` is built at ``:350``. So a campaign aborts in the ledger
    first — C10's arm, ruled open. This test calls ``revision_lints``
    directly, which runs no ledger, which is the only reason the arm is
    reachable at all.

    Guarded anyway because ``_revision_map`` would otherwise be the only one of
    this file's three readers without the guard — ``latest_tag`` catches
    ``yaml.YAMLError`` at ``gate.py:160`` — so whenever C10's ledger arm is
    made tolerant, this would silently become the new campaign-wide abort site.

    The assertion compares against the parser's *own* message rather than
    quoting its wording, so a PyYAML rewording does not make this a false red.
    """
    text = "revisions:\n  draft-test-fixture-01: {cluster_id: c1\n"
    (two_tag_workspace / "revisions.yaml").write_text(text)
    with pytest.raises(yaml.YAMLError) as refused:
        yaml.safe_load(text)
    payload = revision_lints(two_tag_workspace)

    assert payload["revisions"] == []
    assert payload["revisions_status"] == "unreadable"
    assert str(refused.value) in payload["revisions_error"]
    # The parser says `in "<unicode string>"`, never the file, so the path is
    # added rather than left for a reader to guess at.
    assert "revisions.yaml" in payload["revisions_error"]


def test_a_revision_map_that_exists_and_will_not_open_is_raised(two_tag_workspace):
    """R17's propagate arm, which R29 must not widen away.

    Absence is reported; a path that *is* there and cannot be read is a broken
    instrument and still fails loudly. A directory where the map belongs is
    that refusal deterministically and without touching a mode — the idiom
    ``test_a_frozen_manifest_that_exists_and_will_not_open_is_raised`` already
    uses. It is also why ``FileNotFoundError`` is caught rather than
    ``exists()`` tested: ``exists()`` answers False for a permission failure
    too, which would fold this case back into ``missing``.
    """
    path = two_tag_workspace / "revisions.yaml"
    path.unlink()
    path.mkdir()
    with pytest.raises(OSError):
        revision_lints(two_tag_workspace)


def test_finding_count_is_unmeasured_when_no_manifest_fed_it(two_tag_workspace):
    """R20: most of what ``findings`` counts cannot be looked for without a manifest.

    Five of its classes — an unknown citation, an unrendered, stale or unknown
    structure, and an unbound data-model claim — are structurally empty when
    ``lint`` was handed none. What survives is a count of the checks that still
    ran, and a plain int does not say so, so it is reported as unmeasured with
    the rest.

    The earlier version of this test compared two reports that both had no
    manifest, and so could not see any of this: the same checks were missing
    from both sides of the comparison.
    """
    rows = revision_lints(two_tag_workspace)["revisions"]
    _, text = draft_text(two_tag_workspace / "draft", FIRST_TAG)
    frozen = load(
        two_tag_workspace / "checkpoints" / rows[0]["cluster_id"] / MANIFEST_FILE
    )
    # A citation to a claim no manifest declares: a finding only a manifest can
    # make, in prose that is byte-identical either way.
    hostile = text + "\nThe field is `ai_rfc:t:9.9` wide.\n"
    measured = lint(hostile, manifest=frozen)
    unmeasured = lint(hostile, manifest_error="the checkpoint will not load")

    # The vanishing itself: the check is not failed, it is not made.
    assert any("t:9.9" in finding for finding in measured.findings)
    assert not any("t:9.9" in finding for finding in unmeasured.findings)

    assert isinstance(reduce_lint(measured)["finding_count"], int)
    assert reduce_lint(unmeasured)["finding_count"] is None
    # The signals the comparison table is built on are reported on their own,
    # so nulling the count does not cost the row its prose measurements. These
    # two are members of that set, not the whole of it.
    assert (
        reduce_lint(unmeasured)["narration_count"]
        == reduce_lint(measured)["narration_count"]
    )
    assert reduce_lint(unmeasured)["abstract"] == reduce_lint(measured)["abstract"]


def test_a_row_with_no_manifest_still_reports_what_the_text_alone_shows(
    two_tag_workspace,
):
    """R21: four findings need no manifest, so their fields must not need one.

    They were the part of ``finding_count`` that still meant something on an
    unmeasured row, and nulling the count took them with it. Only a lint with
    no manifest at all can see that — which is the third time in this task that
    a test sharing one condition across both its sides could not see the
    condition.
    """
    _, text = draft_text(two_tag_workspace / "draft", FIRST_TAG)
    report = lint(text + TEXT_ONLY_DEFECTS, manifest_error="the checkpoint is gone")
    reduced = reduce_lint(report)

    # Both defects survive, and they sit in the same blocks whose manifest-fed
    # neighbours are None. That pairing is the contract: one block, two
    # populations, and the row says which is which.
    assert any("never-closed" in entry for entry in reduced["structures"]["malformed"])
    assert reduced["structures"]["malformed"] == list(
        report.extra["structures"]["malformed"]
    )
    assert reduced["structures"]["defined"] is None
    assert reduced["blocks"]["figures_without_caption_citation"] == list(
        report.blocks["figures_without_caption_citation"]
    )
    assert reduced["blocks"]["figures_without_caption_citation"]
    assert reduced["citations"]["uncited"] is None
    # The whole `references` field, not a choice of its keys.
    assert reduced["references"] == report.references
    assert reduced["abstract"]["is_stub"] is report.abstract["is_stub"]
    assert reduced["finding_count"] is None


def test_the_table_will_not_show_an_unmeasured_metric_as_a_number(two_tag_workspace):
    """R16 downstream: two unreadable revisions must not render as "no change"."""
    clean = revision_lints(two_tag_workspace)["revisions"]
    _make_frozen_manifest_unreadable(two_tag_workspace, clean[0]["cluster_id"])
    damaged = revision_lints(two_tag_workspace)["revisions"]
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
    """No toolchain is a status of its own, not an unexplained null.

    It is the case Task 3 left folded together with "no build was asked for":
    ``build`` is null in both, and before the status a reader had no way to
    tell a campaign that could not build from an analysis that chose not to.
    """
    assert final_build(tmp_path, None, tmp_path / "out") == {
        "build": None,
        "build_status": "no toolchain",
        "build_error": None,
    }
    assert build_not_requested() == {
        "build": None,
        "build_status": "not requested",
        "build_error": None,
    }


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
        "build": {
            "exit_code": 0,
            "findings": [],
            "idnits": {},
            "broken_references": [],
            "diagnostic_counts": {},
        },
        "build_status": "built",
        "build_error": None,
    }
    # The caller's `out` is honoured: the build report landed under it. That
    # the run directory stayed untouched is not something this read observes.
    stored = json.loads((out / "build" / "build-report.json").read_text())
    assert stored["ref"] == SECOND_TAG


def test_a_tag_the_build_cannot_resolve_is_reported_not_raised(
    two_tag_workspace, toolchain_record, tmp_path
):
    """R31: the fourth member of the class R23, R27 and R29 closed.

    The tag the run last recorded is deleted from the draft repository, so
    ``latest_tag`` still names it out of ``revisions.yaml`` and ``rev-parse``
    refuses it — the state a run killed between tagging and pushing leaves,
    and one an arm with shell access can produce outright.

    ``build`` stays null rather than becoming an empty report: nothing was
    measured, and a zeroed report would say the draft built clean.
    """
    _vcs(two_tag_workspace / "draft", "tag", "-d", SECOND_TAG)

    record = final_build(
        two_tag_workspace, str(toolchain_record), tmp_path / "analysis" / "draft-build"
    )

    assert record["build"] is None and record["build_status"] == "failed"
    assert record["build_error"].startswith(f"{SECOND_TAG}: not a commit in ")
    assert "Needed a single revision" in record["build_error"]


def test_a_build_whose_instrument_is_broken_is_raised_and_not_reported(
    two_tag_workspace, toolchain_record, tmp_path, monkeypatch
):
    """R17's split, on the arm R31 just widened: distinguish, do not widen.

    An :exc:`OSError` is not a finding about the run, whatever a
    :exc:`BuildError` is: a build that cannot invoke its own tools is a broken
    instrument, and reporting it as a run's quality would publish an aggregate
    over measurements nothing took. That is the half this test pins. A caught
    :exc:`BuildError` says the build refused to start and ``build_error`` says
    why; which of its arms are evidence about the run and which read as a
    damaged instrument is not sorted here, for the reason :func:`final_build`
    records.
    """

    def _cannot_run(*_args, **_kwargs):
        raise OSError("make: cannot execute")

    monkeypatch.setattr(quality, "build", _cannot_run)

    with pytest.raises(OSError):
        final_build(
            two_tag_workspace,
            str(toolchain_record),
            tmp_path / "analysis" / "draft-build",
        )


def test_the_build_tally_counts_every_status_and_not_the_successes():
    """The summary line must not read as "all well" for a campaign that built none.

    ``0 built, 0 failed`` is what a tally of successes alone would print for a
    campaign that froze no toolchain — the summary-level form of exactly what
    R31 took out of the exit code. The statuses go in on the constants, so a
    renamed value fails here rather than silently changing what the door
    prints; the expected string is written out, because it is the wording.

    A run archived before the build instrument existed carries no ``quality``
    and belongs under no status, so it is counted under none rather than under
    an invented one.
    """
    assert build_tally({}) == "nothing reported"
    assert build_tally({"A1": {}, "B1": {"quality": {}}}) == "nothing reported"
    assert (
        build_tally(
            {
                "A1": {"quality": {"build_status": quality.BUILD_FAILED}},
                "B1": {"quality": {"build_status": quality.BUILD_NO_TOOLCHAIN}},
                "C1": {"quality": {"build_status": quality.BUILD_BUILT}},
                "D1": {"quality": {"build_status": quality.BUILD_BUILT}},
                "E1": {"quality": {"build_status": quality.BUILD_NOT_REQUESTED}},
            }
        )
        == "2 built, 1 failed, 1 no toolchain, 1 not requested"
    )


def test_the_renderer_reads_the_rows_revision_lints_really_writes(two_tag_workspace):
    """The renderer against the producer, on a shape the fixtures hand-write.

    ``test_report.py`` builds its damaged rows by hand, so a key renamed in
    :func:`reduce_lint` or :func:`revision_lints` would leave every test there
    green against a shape that no longer occurs. This drives the real producer
    over a real workspace and renders what comes back, which is why it lives
    beside the fixture that builds the workspace rather than beside the
    renderer's other tests.

    The damaged shape is the unreadable draft: a tag the revision map
    registers and the draft repository does not hold. A measured revision is
    in the same render, so the dashes are this row's condition and not what
    the table does to every row.
    """
    append_untagged_revision(two_tag_workspace, UNTAGGED, UNCHECKPOINTED_CLUSTER)
    aggregate = _aggregate()
    aggregate["runs"]["A1"]["quality"] = {
        **revision_lints(two_tag_workspace),
        **build_not_requested(),
    }

    runs, revisions = _quality_tables(render_report(aggregate))

    assert runs[2] == "| A1 | 3 | read | — | not requested | — | — | — | — |"
    # Header, separator and one row per revision: the unreadable row is one
    # row, and the error git wrote into it did not break out of its cell.
    assert len(revisions) == 5
    measured, damaged = revisions[2], revisions[-1]
    assert measured.startswith(f"| A1 | {FIRST_TAG} | 1 | cluster | read | read | 1")
    assert damaged.startswith(
        f"| A1 | {UNTAGGED} | 3 | cluster | missing | unreadable | — | — | — | — |"
    )
    assert f"{UNTAGGED}: " in damaged and damaged.count("|") == 13


def test_a_row_missing_only_its_manifest_keeps_its_text_metrics_in_the_render(
    two_tag_workspace,
):
    """The partial nulling, driven by a producer instead of hand-written.

    What the payload exists to carry is that a row missing only its frozen
    manifest still shows every metric the draft text alone measures. The shape
    that says so — ``manifest_status`` missing beside a ``draft_status`` of
    read — reaches the renderer in ``test_report.py`` only as a hand-written
    record, so a producer that stopped emitting it, or emitted it under other
    keys, would leave that test green. The producer-tied damaged row this file
    already renders is the *other* one, missing manifest and unreadable draft
    together, and it nulls everything: it cannot tell a partial nulling from a
    total one, which is the whole contract.

    Deleting the checkpoint directory is the damage
    ``test_a_checkpoint_that_never_landed_is_a_different_report_from_a_broken_one``
    measures at the record level — what a run killed mid-round leaves behind.
    The intact revision is in the same render, so the dashes belong to this
    row's condition and not to what the table does to every row.
    """
    cluster_id = revision_lints(two_tag_workspace)["revisions"][0]["cluster_id"]
    shutil.rmtree(two_tag_workspace / "checkpoints" / cluster_id)
    measured = revision_lints(two_tag_workspace)
    aggregate = _aggregate()
    aggregate["runs"]["A1"]["quality"] = {**measured, **build_not_requested()}

    _, revisions = _quality_tables(render_report(aggregate))

    assert len(revisions) == 4
    damaged, intact = revisions[2], revisions[3]
    # Read off the producer's own row: "real" here means its numbers reached
    # the table, not merely that the cells are not dashes. Measured: this
    # fixture's draft narrates nothing, so that cell reads 0 — which is still
    # not the em dash a nulled metric renders as, and `word_count` is the
    # non-zero half.
    row = measured["revisions"][0]
    narration, words = row["narration_count"], row["abstract"]["word_count"]
    assert isinstance(narration, int) and isinstance(words, int) and words
    assert damaged.startswith(
        f"| A1 | {FIRST_TAG} | 1 | cluster | missing | read | — | — "
        f"| {narration} | {words} |"
    )
    # Only the manifest is gone, so its reason is in the row and the last
    # column, the draft's, stays empty.
    assert cluster_id in damaged and damaged.endswith(" | — |")
    assert intact.startswith(f"| A1 | {SECOND_TAG} | 2 | cluster | read | read |")


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
    rows = revision_lints(two_tag_workspace)["revisions"]
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
