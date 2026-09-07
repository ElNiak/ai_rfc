"""Revision kinds and consolidation checkpoints, through the core and the verb."""

from __future__ import annotations

import pytest

from ai_rfc.server import cli
from ai_rfc.server.core import CoreError, revisions, structures
from ai_rfc.server.core.gates import write_checkpoint
from ai_rfc.server.core.queries import cluster_next

pytestmark = pytest.mark.unit

FIELDS = {
    "kind": "record",
    "title": "Message header",
    "section": "4",
    "fields": [{"name": "version", "type": "uint8", "claim": "t:1.1"}],
}


def _checkpointed_cluster(workspace):
    first = cluster_next(workspace)["id"]
    result = write_checkpoint(workspace, first)
    assert result["exit_code"] == 0
    return first, result


def test_a_consolidation_revision_records_its_kind_and_checkpoint(workspace):
    first, cluster = _checkpointed_cluster(workspace)
    revisions.record_revision(workspace, "draft-test-spec-00", first, True, "first")
    # Editing the structures is the one change a consolidation may make. Without
    # it both records digest the same manifest, and the pin asserted below would
    # hold just as well against code that pinned the cluster's record instead.
    structures.upsert_structure(workspace, "header", FIELDS)
    written = write_checkpoint(
        workspace, first, consolidation=1, base=f"checkpoints/{first}"
    )
    assert written["exit_code"] == 0
    assert written["manifest_sha256"] != cluster["manifest_sha256"]
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
    first, _ = _checkpointed_cluster(workspace)
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


def test_a_consolidation_naming_no_checkpoint_is_refused(workspace):
    first, _ = _checkpointed_cluster(workspace)
    with pytest.raises(CoreError) as error:
        revisions.record_revision(
            workspace, "draft-test-spec-00", first, False, "x", kind="consolidation"
        )
    assert "must name its checkpoint" in str(error.value)


@pytest.mark.parametrize("escape", ["/tmp/nowhere", "../outside"])
def test_a_checkpoint_leaving_the_workspace_is_refused(workspace, escape):
    first, _ = _checkpointed_cluster(workspace)
    with pytest.raises(CoreError) as error:
        revisions.record_revision(
            workspace,
            "draft-test-spec-00",
            first,
            False,
            "x",
            kind="consolidation",
            checkpoint=escape,
        )
    assert "workspace-relative" in str(error.value)


def test_a_cluster_may_not_be_recorded_twice(workspace):
    first, _ = _checkpointed_cluster(workspace)
    revisions.record_revision(workspace, "draft-test-spec-00", first, True, "first")
    with pytest.raises(CoreError) as error:
        revisions.record_revision(workspace, "draft-test-spec-01", first, True, "again")
    assert "already has a cluster revision" in str(error.value)


def test_a_base_without_a_consolidation_writes_nothing(workspace):
    first = cluster_next(workspace)["id"]
    with pytest.raises(CoreError) as error:
        write_checkpoint(workspace, first, base=f"checkpoints/{first}")
    assert "consolidation" in str(error.value)
    # The refusal precedes the shell-out, so the write-once guard is not spent.
    assert not (workspace.workspace / "checkpoints" / first).exists()


def test_the_verb_refuses_a_base_without_a_consolidation(workspace, capsys):
    first = cluster_next(workspace)["id"]
    assert cli.main(["checkpoint", first, "--base", f"checkpoints/{first}"]) == 1
    assert "needs consolidation" in capsys.readouterr().err
