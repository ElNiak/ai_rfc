"""When a consolidation round is due."""

from __future__ import annotations

import pytest

from ai_rfc.experiment.consolidation import consolidation_due

pytestmark = pytest.mark.unit

HEAD = "revisions:\n"
SHA = "a" * 64


def _cluster(tag, cluster_id):
    return (
        f"  {tag}:\n"
        f"    cluster_id: {cluster_id}\n"
        f"    checkpoint_manifest_sha256: {SHA}\n"
        "    normative_change: true\n"
        "    note: 'x'\n"
    )


def _consolidation(tag, cluster_id, checkpoint):
    return (
        f"  {tag}:\n"
        f"    cluster_id: {cluster_id}\n"
        f"    checkpoint_manifest_sha256: {SHA}\n"
        "    normative_change: false\n"
        "    note: 'consolidated'\n"
        "    kind: consolidation\n"
        f"    checkpoint: {checkpoint}\n"
    )


def _workspace(tmp_path, body):
    (tmp_path / "revisions.yaml").write_text(HEAD + body)
    return tmp_path


def test_nothing_is_due_before_any_revision(tmp_path):
    assert consolidation_due(tmp_path, 2) is None


def test_nothing_is_due_below_the_threshold(tmp_path):
    ws = _workspace(tmp_path, _cluster("draft-t-01", "c1"))
    assert consolidation_due(ws, 2) is None


def test_one_is_due_at_the_threshold(tmp_path):
    ws = _workspace(
        tmp_path, _cluster("draft-t-01", "c1") + _cluster("draft-t-02", "c2")
    )
    due = consolidation_due(ws, 2)
    assert due is not None
    assert (due.ordinal, due.base_cluster, due.since) == (1, "c2", 2)


def test_the_counter_restarts_after_a_consolidation(tmp_path):
    ws = _workspace(
        tmp_path,
        _cluster("draft-t-01", "c1")
        + _cluster("draft-t-02", "c2")
        + _consolidation("draft-t-03", "c2", "consolidations/01")
        + _cluster("draft-t-04", "c3"),
    )
    assert consolidation_due(ws, 2) is None
    due = consolidation_due(ws, 2, at_end=True)
    assert (due.ordinal, due.base_cluster, due.since) == (2, "c3", 1)


def test_the_sweep_end_does_not_consolidate_twice(tmp_path):
    ws = _workspace(
        tmp_path,
        _cluster("draft-t-01", "c1")
        + _consolidation("draft-t-02", "c1", "consolidations/01"),
    )
    assert consolidation_due(ws, 2, at_end=True) is None


@pytest.mark.parametrize(
    "broken",
    [
        "revisions:\n  draft-t-01:\n\tcluster_id: c1\n",
        'revisions:\n  draft-t-01:\n    note: "unterminated\n',
    ],
    ids=["tab-indent", "unterminated-quote"],
)
def test_yaml_that_cannot_be_scanned_schedules_nothing(tmp_path, broken):
    (tmp_path / "revisions.yaml").write_text(broken)
    assert consolidation_due(tmp_path, 2) is None
    assert consolidation_due(tmp_path, 2, at_end=True) is None


def test_a_tag_key_of_another_type_schedules_nothing(tmp_path):
    ws = _workspace(
        tmp_path,
        _cluster("draft-t-01", "c1") + "  01:\n    cluster_id: c2\n",
    )
    assert consolidation_due(ws, 2) is None
    assert consolidation_due(ws, 2, at_end=True) is None


def test_the_reason_counts_a_single_round_in_the_singular(tmp_path):
    ws = _workspace(tmp_path, _cluster("draft-t-01", "c1"))
    due = consolidation_due(ws, 1)
    assert due is not None
    assert due.reason == "1 cluster round"


def test_zero_disables_mid_sweep_consolidation_but_not_the_final_one(tmp_path):
    ws = _workspace(
        tmp_path, _cluster("draft-t-01", "c1") + _cluster("draft-t-02", "c2")
    )
    assert consolidation_due(ws, 0) is None
    assert consolidation_due(ws, 0, at_end=True) is not None
