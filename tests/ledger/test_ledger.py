"""One reader of per-cluster progress, agreed with by the surfaces it replaces."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ai_rfc.draft.checkpoint import write_checkpoint
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
    sha: str = "0" * 64,
) -> None:
    document = yaml.safe_load((root / "revisions.yaml").read_text()) or {
        "revisions": {}
    }
    body = {
        "cluster_id": cluster_id,
        "checkpoint_manifest_sha256": sha,
        "normative_change": True,
        "note": "n",
    }
    if kind:
        body["kind"] = kind
    document.setdefault("revisions", {})[tag] = body
    (root / "revisions.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    if tag_it:
        git(root / "draft", "tag", "-a", tag, "-m", tag, date=DATE)


def _finish(root: Path, cluster_id: str, tag: str) -> None:
    """Finish one cluster the way the shipped code does.

    Through :func:`write_checkpoint` rather than the stub above, because
    ``completeness.build`` reads the checkpoint's frozen manifest and a stub
    record has none.
    """
    directory = write_checkpoint(
        root / "manifest.yaml", root / "timeline", cluster_id, root / "checkpoints"
    )
    sha = json.loads((directory / "checkpoint.json").read_text())["manifest_sha256"]
    _revision(root, tag, cluster_id, sha=sha)


def _preseed(root: Path, cluster_id: str, ordinal: int) -> None:
    """Freeze one cluster the way the harness seeds a baseline's work.

    The shape ``experiment.workspace.preseed`` writes, reproduced rather than
    imported: the workspace manifest checkpointed against the cluster, plus the
    marker naming it as work done before this run began.
    """
    directory = write_checkpoint(
        root / "manifest.yaml", root / "timeline", cluster_id, root / "checkpoints"
    )
    (directory / "harness.json").write_text(
        json.dumps(
            {"pre_seeded": True, "reason": "outside window", "ordinal": ordinal},
            sort_keys=True,
        )
        + "\n"
    )


def _cite_every_claim(root: Path) -> None:
    """Commit a draft revision that cites both of the fixture's claims.

    ``completeness`` reports every frozen claim no revision cites, and the
    fixture's prose cites only the first of the manifest's two. A case about the
    window must not be graded on that unrelated gap, so the prose cites both
    before any checkpoint is tagged.
    """
    (root / "draft" / "draft-test-spec.md").write_text(
        "# Spec\n\nThing one MUST hold. `ai_rfc:t:1.1`\n\n"
        "Thing two SHOULD hold. `ai_rfc:t:2.1`\n"
    )
    git(root / "draft", "add", "draft-test-spec.md")
    git(root / "draft", "commit", "-m", "cite every claim", date=DATE)


def _completeness(root: Path):
    """The completeness report ``verify`` freezes, built off ``root``."""
    from ai_rfc.draft import completeness

    return completeness.build(
        root / "timeline",
        root / "checkpoints",
        root / "manifest.yaml",
        root / "revisions.yaml",
        root / "draft",
    )


def _checkpoint_stage(root: Path):
    """The pipeline's ``checkpoint`` stage, read off ``root``."""
    from ai_rfc.pipeline.state import state
    from ai_rfc.pipeline.workspace import Workspace

    return {entry.stage.name: entry for entry in state(Workspace(root))}["checkpoint"]


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


def test_a_workspace_with_no_draft_repository_is_tagless_not_an_error(ws):
    """Not every workspace has started a draft, and that is not a fault."""
    first, _ = _ids(ws)
    _checkpoint(ws, first)
    _revision(ws, "draft-test-spec-01", first, tag_it=False)
    shutil.rmtree(ws / "draft")

    state = clusters(ws)[0]

    assert not state.tag_exists and not state.done
    assert (
        state.partial_reason
        == "checkpoint present, revision entry recorded, tag missing"
    )


def test_a_draft_repository_that_will_not_answer_is_an_error(ws, monkeypatch):
    """A failed ``git tag -l`` must not read as "no tags".

    Swallowing it made every cluster read ``tag_exists`` False, so a whole
    finished reconstruction reported as outstanding and ``next_cluster``
    offered the same cluster forever with nothing saying why.
    """
    first, _ = _ids(ws)
    _finish(ws, first, "draft-test-spec-01")
    assert clusters(ws)[0].done

    shutil.rmtree(ws / "draft" / ".git")
    (ws / "draft" / ".git").write_text("gitdir: nowhere\n")
    with pytest.raises(LedgerError) as broken:
        clusters(ws)
    assert "git tag -l exited" in str(broken.value)

    def _no_git(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(subprocess, "run", _no_git)
    with pytest.raises(LedgerError) as missing:
        clusters(ws)
    assert "could not run git" in str(missing.value)


def test_the_old_readers_agree_with_the_ledger(ws, monkeypatch):
    """The surfaces this module replaces must report what it reports.

    They answer two different questions, and each question now has one home.
    Four ask "is this cluster finished?" — ``cluster_next``, ``status``,
    ``cluster_artifacts`` and ``window_progress`` — and are checked against
    ``counts(...)["done"]``. Two ask "has this cluster been frozen?" —
    ``completeness.build`` and the pipeline's ``checkpoint`` stage — and are
    checked against ``state.checkpoint``. Both are read once, here.

    Asserted at two states rather than one, because surfaces that happen to
    coincide on a half-finished workspace can still disagree about a finished
    one: that was the old bug's shape, where a checkpoint alone advanced the
    MCP cursor while the harness still called the cluster outstanding.
    """
    from ai_rfc.draft import completeness
    from ai_rfc.experiment.metrics import cluster_artifacts
    from ai_rfc.experiment.progress import window_progress
    from ai_rfc.pipeline.state import State, state
    from ai_rfc.pipeline.workspace import Workspace
    from ai_rfc.server.core import queries
    from ai_rfc.server.paths import resolve_context

    def _completeness():
        return completeness.build(
            ws / "timeline",
            ws / "checkpoints",
            ws / "manifest.yaml",
            ws / "revisions.yaml",
            ws / "draft",
        )

    def _stage():
        return {entry.stage.name: entry for entry in state(Workspace(ws))}["checkpoint"]

    first, second = _ids(ws)
    _finish(ws, first, "draft-test-spec-01")
    (ws / "init.json").write_text(json.dumps({"window": [1, 2]}))
    monkeypatch.setenv("AI_RFC_CONFIG", str(ws / "recon.yaml"))

    rows = clusters(ws)
    summary = counts(rows)
    assert summary["done"] == 1 and summary["outstanding"] == 1
    outstanding = [row.id for row in rows if not row.done]
    unfrozen = [row.id for row in rows if not row.checkpoint]

    # Finished: the four surfaces that answer it.
    assert queries.cluster_next(resolve_context())["id"] == second == outstanding[0]
    assert next_cluster(ws).id == second
    composite = queries.status(resolve_context())
    assert composite["clusters_processed"] == summary["done"]
    assert composite["ledger"] == summary
    row, _, position, done, total = window_progress(ws)
    assert (row["id"], position, done, total) == (second, 2, summary["done"], 2)
    assert cluster_artifacts(ws, {"id": first, "ordinal": 1, "kind": "pr"})["artifacts"]

    # Frozen: the two surfaces that answer it.
    assert _completeness().unprocessed_clusters == tuple(unfrozen) == (second,)
    assert _stage().state is State.PARTIAL
    assert "1 of 2" in _stage().reason

    # The same six answers, after the window is finished.
    _finish(ws, second, "draft-test-spec-02")
    summary = counts(clusters(ws))
    assert summary["done"] == 2 and summary["outstanding"] == 0

    assert queries.cluster_next(resolve_context()) is None
    assert next_cluster(ws) is None
    composite = queries.status(resolve_context())
    assert composite["clusters_processed"] == 2 and composite["ledger"] == summary
    assert window_progress(ws) == (None, None, 0, 2, 2)
    assert cluster_artifacts(ws, {"id": second, "ordinal": 2, "kind": "pr"})[
        "artifacts"
    ]
    assert _completeness().unprocessed_clusters == ()
    assert _stage().state is State.DONE
    assert "2 of 2" in _stage().reason


def test_a_narrowed_window_that_finished_its_cluster_is_done_everywhere(ws, capsys):
    """A window of one, finished, must read as finished on every surface.

    The fixture the case above uses writes ``{"window": [1, 2]}`` over two
    clusters, so the window covers everything and a reader that ignores it
    coincides with one that honours it. Narrowed to the first cluster alone,
    the two part: a reader counting every timeline row calls a finished
    reconstruction "1 of 2" and names the second cluster unprocessed, and
    ``verify --strict`` then reports a completeness finding for work this run
    was never asked to do.

    The exit code of ``verify`` itself is not the assertion: this fixture's
    draft is not an Internet-Draft skeleton, so ``lint`` reports findings here
    whatever the window does. What the window decides is the ``completeness``
    line, and the ids the frozen report carries.
    """
    from ai_rfc import cli
    from ai_rfc.pipeline.state import State

    first, second = _ids(ws)
    _cite_every_claim(ws)
    _finish(ws, first, "draft-test-spec-01")
    (ws / "init.json").write_text(json.dumps({"window": [1, 1]}))

    states = clusters(ws)
    assert [s.in_window for s in states] == [True, False]
    assert counts(states) == {
        "total": 2,
        "in_window": 1,
        "done": 1,
        "partial": 0,
        "outstanding": 0,
        "pre_seeded": 0,
    }

    stage = _checkpoint_stage(ws)
    assert stage.state is State.DONE and "1 of 1" in stage.reason
    report = _completeness(ws)
    assert report.unprocessed_clusters == ()
    assert report.totals["clusters_total"] == 1
    assert report.totals["clusters_processed"] == 1
    assert report.totals["processed_fraction"] == 1.0
    assert second not in report.unprocessed_clusters

    capsys.readouterr()
    # Not ``== 0``: ``lint`` reports this fixture's draft whatever the window
    # does. ``!= 1`` is the aggregation guard — a check that degrades into an
    # error is reported as 1 (``lifecycle/verify/cli.py:108-109``), and the
    # ``completeness`` line below would still read ok while it happened.
    assert cli.main(["verify", "--config", str(ws / "recon.yaml"), "--strict"]) != 1
    assert "completeness: ok" in capsys.readouterr().err
    frozen = json.loads((ws / "out" / "completeness.json").read_text())
    assert frozen["unprocessed_clusters"] == []
    assert frozen["totals"]["clusters_total"] == 1


def test_a_pre_seeded_cluster_is_not_counted_as_this_runs_work(ws, capsys):
    """A baseline's cluster is nobody's outstanding work, on every surface.

    The window is the whole timeline here; what takes the second cluster out of
    this run's figures is the harness's own marker beside its checkpoint. A
    reader that counts checkpoint records alone credits the run with work the
    baseline did, and one that counts timeline rows alone measures it against a
    denominator that includes them.
    """
    from ai_rfc import cli
    from ai_rfc.pipeline.state import State

    first, second = _ids(ws)
    _cite_every_claim(ws)
    _finish(ws, first, "draft-test-spec-01")
    _preseed(ws, second, ordinal=2)

    states = clusters(ws)
    assert [s.pre_seeded for s in states] == [False, True]
    assert counts(states)["in_window"] == 1 and counts(states)["pre_seeded"] == 1

    stage = _checkpoint_stage(ws)
    assert stage.state is State.DONE and "1 of 1" in stage.reason
    report = _completeness(ws)
    assert report.unprocessed_clusters == ()
    assert report.totals["clusters_total"] == 1
    assert report.totals["clusters_processed"] == 1
    assert report.totals["processed_fraction"] == 1.0
    assert second not in report.silent_clusters

    capsys.readouterr()
    # The same aggregation guard as the case above, for the same reason.
    assert cli.main(["verify", "--config", str(ws / "recon.yaml"), "--strict"]) != 1
    assert "completeness: ok" in capsys.readouterr().err
    frozen = json.loads((ws / "out" / "completeness.json").read_text())
    assert frozen["unprocessed_clusters"] == []
    assert frozen["totals"]["clusters_total"] == 1


def test_a_window_that_is_entirely_pre_seeded_has_nothing_to_checkpoint(ws):
    """An empty scope is finished, not pending: there is nothing to offer.

    Every cluster here is a baseline's, so this run was asked to produce none
    of them. Counting timeline rows made the stage read "no cluster
    checkpointed of 0" and offer an operator a stage that cannot be run, and
    gave ``completeness`` a denominator it was never measured against.
    """
    from ai_rfc.pipeline.state import State

    first, second = _ids(ws)
    _preseed(ws, first, ordinal=1)
    _preseed(ws, second, ordinal=2)

    summary = counts(clusters(ws))
    assert summary["in_window"] == 0 and summary["pre_seeded"] == 2
    assert next_cluster(ws) is None

    stage = _checkpoint_stage(ws)
    assert stage.state is State.DONE
    assert stage.reason == "no cluster in this run's window to checkpoint"
    report = _completeness(ws)
    assert report.unprocessed_clusters == () and report.silent_clusters == ()
    assert report.totals["clusters_total"] == 0
    assert report.totals["clusters_processed"] == 0
    assert report.totals["processed_fraction"] == 0.0


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


def test_the_status_tool_counts_this_runs_work_on_both_of_its_own_figures(
    ws, monkeypatch
):
    """``ai_rfc_status`` is the surface an agent reads mid-campaign.

    It printed ``clusters_total`` as the length of the whole timeline and
    ``clusters_processed`` as every done cluster anywhere in it — two figures
    built beside ``"ledger": counts(states)`` and disagreeing with it. An
    agent reading "1 of 2" for a finished window is told there is work left
    that was never this run's, which is the same defect Task 5 closed for
    ``status`` and ``completeness``, one package over.

    Both halves of "this run's work" are exercised: the second cluster is
    outside the window *and* carries the pre-seed marker, so a reader honouring
    only one of the two still answers 2.
    """
    from ai_rfc.server.core import queries
    from ai_rfc.server.paths import resolve_context

    first, second = _ids(ws)
    _cite_every_claim(ws)
    _finish(ws, first, "draft-test-spec-01")
    _preseed(ws, second, ordinal=2)
    (ws / "init.json").write_text(json.dumps({"window": [1, 1]}))
    monkeypatch.setenv("AI_RFC_CONFIG", str(ws / "recon.yaml"))

    summary = counts(clusters(ws))
    assert summary["total"] == 2 and summary["in_window"] == 1

    composite = queries.status(resolve_context())

    assert composite["ledger"] == summary
    assert composite["clusters_total"] == summary["in_window"] == 1
    assert composite["clusters_processed"] == summary["done"] == 1
