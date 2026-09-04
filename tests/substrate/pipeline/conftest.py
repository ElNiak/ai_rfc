import os
import subprocess
from pathlib import Path

import pytest

from ai_rfc.draft.checkpoint import write_checkpoint
from ai_rfc.pipeline import cli
from ai_rfc.pipeline.state import _cluster_ids, next_stage
from ai_rfc.pipeline.workspace import Workspace

#: `drafted_workspace`'s commit date, fixed like every other fixture's.
_DRAFT_COMMIT_DATE = "2026-01-01T00:00:09+00:00"


def _run(repo: Path, *args: str, when: str | None = None) -> None:
    env = None
    if when is not None:
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace root holding only a pinned clone, as stage 0 leaves it.

    The clone is a real repository with one merge, so the timeline stage has
    both a PR cluster and an epoch cluster to find — the same shape the views
    fixture builds, because the pipeline's job is to reproduce by chaining what
    those stages already do when driven by hand.
    """
    root = tmp_path / "ws"
    clone = root / "clone"
    clone.mkdir(parents=True)
    _run(clone, "init", "-b", "main")
    _run(clone, "config", "user.email", "t@t")
    _run(clone, "config", "user.name", "t")
    (clone / "a.txt").write_text("one\n")
    _run(clone, "add", "a.txt")
    _run(clone, "commit", "-m", "root")
    _run(clone, "checkout", "-b", "feat")
    (clone / "b.txt").write_text("two\n")
    _run(clone, "add", "b.txt")
    _run(clone, "commit", "-m", "feat work")
    _run(clone, "checkout", "main")
    (clone / "c.txt").write_text("three\n")
    _run(clone, "add", "c.txt")
    _run(clone, "commit", "-m", "direct push")
    _run(clone, "merge", "--no-ff", "feat", "-m", "Merge branch 'feat'")
    return root


@pytest.fixture
def mined_workspace(workspace: Path) -> Path:
    """A workspace carrying a manifest whose stored status its evidence denies.

    `check` is re-derivable and so never `DONE`; the point of this fixture is a
    workspace where the walk stops at an agent boundary while a manifest sits on
    disk overstating a claim. If `check` does not run, nothing notices.
    """
    assert cli.main(["run", str(workspace)]) == 0
    (workspace / "manifest.yaml").write_text(
        "rfc: T\ntitle: 'Fixture'\nrequirements:\n"
        "  't:1':\n"
        "    text: 'A claim.'\n"
        "    section: '1'\n"
        "    level: MUST\n"
        "    layer: transport\n"
        "    status: confirmed\n"
        "    anchors:\n"
        "      - evidence_class: adr\n"
        "        locator: 'doc:fixture'\n"
    )
    return workspace


@pytest.fixture
def finished_workspace(mined_workspace: Path) -> Path:
    """A workspace where every stage reads DONE or RECOMPUTED.

    `next_stage` returns `None` here — the "nothing outstanding" case `_run`
    used to trust as nothing left to do, before the re-derivable checks could
    run. Built by checkpointing every cluster `mined_workspace` already has
    evidence for and giving it a draft repository, an empty question register
    and a `revisions.yaml`, the same artifacts `draft/conftest.py`'s
    `draft_workspace` writes, so `state()` reports `checkpoint` and `prose`
    DONE as well as `mining`.
    """
    ws = Workspace(root=mined_workspace)
    for cluster_id in _cluster_ids(ws):
        write_checkpoint(ws.manifest, ws.timeline, cluster_id, ws.checkpoints)

    ws.draft.mkdir(parents=True)
    _run(ws.draft, "init", "-b", "main")
    ws.questions.write_text("questions: {}\n")
    ws.revisions.write_text("revisions: {}\n")

    assert next_stage(ws) is None
    return mined_workspace


@pytest.fixture
def drafted_workspace(finished_workspace: Path) -> Workspace:
    """`finished_workspace` with a committed draft file.

    `finished_workspace` only `git init`s the draft repository, so `draft lint`
    and `draft build` — which read the draft at a ref — have nothing to read.
    """
    ws = Workspace(root=finished_workspace)
    _run(ws.draft, "config", "user.email", "t@t")
    _run(ws.draft, "config", "user.name", "t")
    (ws.draft / "draft-test-spec.md").write_text(
        "# Spec\n\nThe system does the thing. `ai_rfc:spec:1.1`\n"
    )
    _run(ws.draft, "add", "draft-test-spec.md")
    _run(ws.draft, "commit", "-m", "revision 00", when=_DRAFT_COMMIT_DATE)
    return ws
