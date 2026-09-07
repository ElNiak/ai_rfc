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


def test_a_cluster_revision_naming_a_checkpoint_is_refused_as_a_core_error(workspace):
    # Only a consolidation may name a checkpoint. The substrate loader decides
    # that; the core owes the caller one exception class for it, the same one
    # the parallel stray `base` on a cluster checkpoint already raises.
    first, _ = _checkpointed_cluster(workspace)
    with pytest.raises(CoreError) as error:
        revisions.record_revision(
            workspace,
            "draft-test-spec-00",
            first,
            True,
            "stray",
            checkpoint="consolidations/01",
        )
    assert "only a consolidation may name a checkpoint" in str(error.value)


def test_a_corrupt_revision_map_on_disk_is_a_core_error(workspace):
    # The map on disk is read through the gate's loader only for a cluster
    # revision; a consolidation meets the same corruption in the candidate. Both
    # paths must report one class, or the exception depends on the argument
    # rather than on the defect.
    first, _ = _checkpointed_cluster(workspace)
    workspace.revisions.write_text(
        "revisions:\n"
        "  bad-tag:\n"
        "    cluster_id: c\n"
        "    checkpoint_manifest_sha256: abc\n"
        "    normative_change: true\n"
    )
    with pytest.raises(CoreError) as error:
        revisions.record_revision(workspace, "draft-test-spec-00", first, True, "x")
    assert "not a revision tag" in str(error.value)


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


@pytest.mark.parametrize("escape", ["/tmp/nowhere", "../outside"])
def test_a_base_leaving_the_workspace_is_refused(workspace, escape):
    first = cluster_next(workspace)["id"]
    with pytest.raises(CoreError) as error:
        write_checkpoint(workspace, first, consolidation=1, base=escape)
    assert "workspace-relative" in str(error.value)
    # The refusal precedes the shell-out, so the substrate never ran and no
    # consolidation root exists to hold a checkpoint pinned to a foreign base.
    assert not (workspace.workspace / "consolidations").exists()


def test_a_base_naming_another_workspaces_checkpoint_is_refused(
    workspace, make_workspace
):
    # The two literals above are refused by the substrate anyway, so only a
    # base that resolves somewhere real shows what the guard buys: without it
    # this exits 0 and pins a consolidation here to a foreign manifest.
    from ai_rfc.server.paths import resolve_context

    build, use = make_workspace
    foreign_root = build("foreign")
    use(foreign_root)
    foreign = resolve_context()
    other = cluster_next(foreign)["id"]
    assert write_checkpoint(foreign, other)["exit_code"] == 0

    use(workspace.workspace)
    first = cluster_next(workspace)["id"]
    with pytest.raises(CoreError) as error:
        write_checkpoint(
            workspace,
            first,
            consolidation=1,
            base=str(foreign_root / "checkpoints" / other),
        )
    assert "workspace-relative" in str(error.value)
    assert not (workspace.workspace / "consolidations").exists()


def test_the_verb_refuses_a_base_without_a_consolidation(workspace, capsys):
    first = cluster_next(workspace)["id"]
    assert cli.main(["checkpoint", first, "--base", f"checkpoints/{first}"]) == 1
    assert "needs consolidation" in capsys.readouterr().err
