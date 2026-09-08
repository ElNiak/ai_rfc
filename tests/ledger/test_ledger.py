"""One reader of per-cluster progress, agreed with by the surfaces it replaces."""

import json
import os
from pathlib import Path

import pytest
import yaml

from ai_rfc.ledger import LedgerError, clusters, counts, next_cluster, window_of
from ai_rfc.server.testing import build_workspace, git

DATE = "2026-01-01T00:00:09+00:00"

#: The sealed MARK A1 run directory holds the workspace one level down.
MARK = Path(
    os.path.expanduser("~/ai-rfc-experiments/baselines/mark-a1-2026-09-03/workspace")
)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """The twin-builder workspace: two clusters, a draft repository, no tags."""
    return build_workspace(tmp_path / "ws")


def _ids(root: Path) -> list[str]:
    lines = (root / "timeline" / "clusters.jsonl").read_text().splitlines()
    return [json.loads(line)["id"] for line in lines if line.strip()]


def _checkpoint(
    root: Path, cluster_id: str, *, pre_seeded: bool = False, bare: bool = False
) -> None:
    directory = root / "checkpoints" / cluster_id
    directory.mkdir(parents=True)
    if bare:
        return
    (directory / "checkpoint.json").write_text(
        json.dumps(
            {"cluster_id": cluster_id, "manifest_sha256": "0" * 64, "ordinal": 1}
        )
    )
    if pre_seeded:
        (directory / "harness.json").write_text('{"pre_seeded": true}\n')


def _revision(
    root: Path,
    tag: str,
    cluster_id: str,
    *,
    kind: str | None = None,
    tag_it: bool = True,
) -> None:
    document = yaml.safe_load((root / "revisions.yaml").read_text()) or {
        "revisions": {}
    }
    body = {
        "cluster_id": cluster_id,
        "checkpoint_manifest_sha256": "0" * 64,
        "normative_change": True,
        "note": "n",
    }
    if kind:
        body["kind"] = kind
    document.setdefault("revisions", {})[tag] = body
    (root / "revisions.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    if tag_it:
        git(root / "draft", "tag", "-a", tag, "-m", tag, date=DATE)


def test_a_fresh_workspace_is_all_outstanding(ws):
    states = clusters(ws)
    assert [s.ordinal for s in states] == [1, 2]
    assert all(not s.done and s.partial_reason is None and s.in_window for s in states)
    assert counts(states) == {
        "total": 2,
        "in_window": 2,
        "done": 0,
        "partial": 0,
        "outstanding": 2,
        "pre_seeded": 0,
    }
    assert next_cluster(ws).ordinal == 1


def test_done_needs_checkpoint_entry_and_tag(ws):
    first, second = _ids(ws)
    _checkpoint(ws, first)
    assert clusters(ws)[0].partial_reason == "checkpoint present, no revision entry"
    _revision(ws, "draft-test-spec-01", first, tag_it=False)
    assert (
        clusters(ws)[0].partial_reason
        == "checkpoint present, revision entry recorded, tag missing"
    )
    git(ws / "draft", "tag", "-a", "draft-test-spec-01", "-m", "01", date=DATE)
    state = clusters(ws)[0]
    assert state.done and state.revision_tag == "draft-test-spec-01"
    assert state.tag_exists
    assert next_cluster(ws).id == second


def test_a_bare_directory_is_not_a_checkpoint(ws):
    first, _ = _ids(ws)
    _checkpoint(ws, first, bare=True)
    state = clusters(ws)[0]
    assert not state.checkpoint and not state.done and state.partial_reason is None


def test_pre_seeded_and_out_of_window_clusters_are_done_by_definition(ws):
    first, second = _ids(ws)
    _checkpoint(ws, first, pre_seeded=True)
    states = clusters(ws)
    assert states[0].done and states[0].pre_seeded
    states = clusters(ws, window=(2, 2))
    assert not states[0].in_window and states[0].done and states[1].in_window
    assert next_cluster(ws, window=(2, 2)).id == second
    assert counts(states)["in_window"] == 1


def test_window_is_read_from_init_json_then_pristine_json(ws):
    assert window_of(ws) is None
    (ws / "pristine.json").write_text(json.dumps({"window": [2, 2]}))
    assert window_of(ws) == (2, 2)
    (ws / "init.json").write_text(json.dumps({"window": [1, 1]}))
    assert window_of(ws) == (1, 1)
    assert next_cluster(ws).ordinal == 1 and clusters(ws)[1].in_window is False


def test_a_consolidation_entry_never_marks_a_cluster_done(ws):
    first, _ = _ids(ws)
    _checkpoint(ws, first)
    _revision(ws, "draft-test-spec-01", first, kind="consolidation")
    state = clusters(ws)[0]
    assert not state.done and state.revision_tag is None


def test_an_unreadable_timeline_is_an_error(tmp_path):
    with pytest.raises(LedgerError):
        clusters(tmp_path)


def test_the_five_old_readers_agree_with_the_ledger(ws, monkeypatch):
    """The surfaces this module replaces must report what it reports."""
    from ai_rfc.experiment.metrics import cluster_artifacts
    from ai_rfc.experiment.progress import window_progress
    from ai_rfc.pipeline.state import State, state
    from ai_rfc.pipeline.workspace import Workspace
    from ai_rfc.server.core import queries
    from ai_rfc.server.paths import resolve_context

    first, second = _ids(ws)
    _checkpoint(ws, first)
    _revision(ws, "draft-test-spec-01", first)
    (ws / "init.json").write_text(json.dumps({"window": [1, 2]}))
    monkeypatch.setenv("AI_RFC_WORKSPACE", str(ws))
    assert queries.cluster_next(resolve_context())["id"] == second
    assert next_cluster(ws).id == second
    row, artifacts, position, done, total = window_progress(ws)
    assert (row["id"], position, done, total) == (second, 2, 1, 2)
    assert cluster_artifacts(ws, {"id": first, "ordinal": 1, "kind": "pr"})["artifacts"]
    by_name = {entry.stage.name: entry for entry in state(Workspace(ws))}
    assert by_name["checkpoint"].state is State.PARTIAL
    assert "1 of 2" in by_name["checkpoint"].reason


@pytest.mark.skipif(not MARK.is_dir(), reason="the sealed MARK A1 copy is not here")
def test_the_finished_mark_run_reads_as_37_done_of_69_with_none_half_finished():
    """The run stopped between clusters, not inside one.

    All 37 checkpoint directories carry a record, an entry and a tag, so the
    ledger reports no partial cluster: ordinal 38 is the next outstanding one,
    which is not the same thing as a half-finished one.
    """
    states = clusters(MARK)
    summary = counts(states)
    assert summary["done"] == 37 and summary["total"] == 69
    assert [s.ordinal for s in states if s.partial_reason] == []
    assert next_cluster(MARK).ordinal == 38
