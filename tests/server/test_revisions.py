"""Revision kinds through the server core."""

from __future__ import annotations

import pytest

from ai_rfc.server.core import CoreError, revisions
from ai_rfc.server.core.gates import write_checkpoint
from ai_rfc.server.core.queries import cluster_next

pytestmark = pytest.mark.unit


def _checkpointed_cluster(workspace):
    first = cluster_next(workspace)["id"]
    assert write_checkpoint(workspace, first)["exit_code"] == 0
    return first


def test_a_consolidation_revision_records_its_kind_and_checkpoint(workspace):
    first = _checkpointed_cluster(workspace)
    revisions.record_revision(workspace, "draft-test-spec-00", first, True, "first")
    written = write_checkpoint(
        workspace, first, consolidation=1, base=f"checkpoints/{first}"
    )
    assert written["exit_code"] == 0
    entry = revisions.record_revision(
        workspace,
        "draft-test-spec-01",
        first,
        False,
        "consolidated",
        kind="consolidation",
        checkpoint="consolidations/01",
    )
    assert entry["kind"] == "consolidation"
    assert entry["checkpoint"] == "consolidations/01"
    # Pinned to the consolidation's own checkpoint, not the cluster's.
    assert entry["checkpoint_manifest_sha256"] == written["manifest_sha256"]


def test_a_consolidation_must_name_a_checkpoint_that_exists(workspace):
    first = _checkpointed_cluster(workspace)
    revisions.record_revision(workspace, "draft-test-spec-00", first, True, "first")
    with pytest.raises(CoreError) as error:
        revisions.record_revision(
            workspace,
            "draft-test-spec-01",
            first,
            False,
            "consolidated",
            kind="consolidation",
            checkpoint="consolidations/01",
        )
    assert "consolidations/01" in str(error.value)


def test_a_cluster_may_not_be_recorded_twice(workspace):
    first = _checkpointed_cluster(workspace)
    revisions.record_revision(workspace, "draft-test-spec-00", first, True, "first")
    with pytest.raises(CoreError) as error:
        revisions.record_revision(workspace, "draft-test-spec-01", first, True, "again")
    assert "already has a cluster revision" in str(error.value)
