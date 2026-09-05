import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from ai_rfc.draft.checkpoint import write_checkpoint
from ai_rfc.timeline.build import build_timeline
from ai_rfc.timeline.corpus import find_tip, read_commits
from ai_rfc.timeline.store import read_clusters, write_timeline


def _record(sha: str, parents: list[str]) -> str:
    return json.dumps(
        {
            "sha": sha,
            "parents": parents,
            "author_name": "a",
            "author_email": "a@a",
            "authored_at": "2026-01-01T00:00:00+00:00",
            "committed_at": "2026-01-01T00:00:00+00:00",
            "subject": f"s {sha}",
            "body": "",
            "is_merge": len(parents) > 1,
            "file_count": 1,
            "files_recorded": 1,
            "files_truncated": False,
        },
        sort_keys=True,
    )


@pytest.fixture
def timeline_dir(tmp_path: Path) -> Path:
    """A two-cluster timeline (epoch then pr) built through the shipped code."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    records = [
        _record("aa", []),
        _record("ff", ["aa"]),
        _record("dd", ["aa"]),
        _record("mm", ["dd", "ff"]),
    ]
    (corpus / "commits.jsonl").write_text("\n".join(records) + "\n")
    (corpus / "files.jsonl").write_text("")
    commits = read_commits(corpus)
    out = tmp_path / "timeline"
    write_timeline(build_timeline(commits), find_tip(commits), corpus, out)
    return out


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _manifest_text(with_second_claim: bool, question_id: str = "q-001") -> str:
    text = (
        "rfc: SPEC-1\n"
        "title: 'A reconstructed specification'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: 'The system does the thing.'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: behaviour\n"
        "    anchors:\n"
        "      - evidence_class: code\n"
        "        locator: src/a.py\n"
        f"        commit: '{'0' * 40}'\n"
    )
    if with_second_claim:
        text += (
            "  'spec:2.1':\n"
            "    text: 'The system also does this.'\n"
            "    section: '2.1'\n"
            "    level: SHOULD\n"
            "    layer: behaviour\n"
            f"    question-id: {question_id}\n"
        )
    return text


def _checkpoint_sha(checkpoint_dir: Path) -> str:
    record = json.loads((checkpoint_dir / "checkpoint.json").read_text())
    return record["manifest_sha256"]


def _build_draft_workspace(
    tmp_path: Path, timeline_dir: Path, extra: str = ""
) -> dict[str, Any]:
    """Build a gate-clean workspace: draft repo, checkpoints, questions, revisions.

    Args:
        tmp_path: The root everything is laid out under.
        timeline_dir: A built two-cluster timeline.
        extra: YAML appended to the second cluster's manifest, so a caller can
            add a ``structures:`` block without restating the whole body.

    Returns:
        The workspace paths, plus the first and last checkpoint directories and
        their cluster ids, so a consolidation can be built on either.
    """
    clusters = read_clusters(timeline_dir)
    epoch_id, pr_id = clusters[0]["id"], clusters[1]["id"]

    first_manifest = tmp_path / "m1.yaml"
    first_manifest.write_text(_manifest_text(with_second_claim=False))
    second_manifest = tmp_path / "m2.yaml"
    second_manifest.write_text(_manifest_text(with_second_claim=True) + extra)
    checkpoints = tmp_path / "checkpoints"
    first_checkpoint = write_checkpoint(
        first_manifest, timeline_dir, epoch_id, checkpoints
    )
    second_checkpoint = write_checkpoint(
        second_manifest, timeline_dir, pr_id, checkpoints
    )

    repo = tmp_path / "draft"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    draft_file = repo / "draft-test-spec.md"
    draft_file.write_text("# Spec\n\nThe system does the thing. `ai_rfc:spec:1.1`\n")
    git(repo, "add", "draft-test-spec.md")
    git(repo, "commit", "-m", "revision 00")
    git(repo, "tag", "draft-test-spec-00")
    draft_file.write_text(
        draft_file.read_text() + "\nIt also does this. `ai_rfc:spec:2.1`\n"
    )
    git(repo, "add", "draft-test-spec.md")
    git(repo, "commit", "-m", "revision 01")
    git(repo, "tag", "draft-test-spec-01")

    questions = tmp_path / "questions.yaml"
    questions.write_text(
        "questions:\n"
        "  q-001:\n"
        "    question: 'Is the second behaviour deliberate?'\n"
        "    claim_ids: ['spec:2.1']\n"
        "    status: open\n"
        "    asked_at: '2026-08-25'\n"
    )

    revisions = tmp_path / "revisions.yaml"
    revisions.write_text(
        "revisions:\n"
        "  draft-test-spec-00:\n"
        f"    cluster_id: {epoch_id}\n"
        f"    checkpoint_manifest_sha256: {_checkpoint_sha(first_checkpoint)}\n"
        "    normative_change: true\n"
        "    note: 'initial reconstruction'\n"
        "  draft-test-spec-01:\n"
        f"    cluster_id: {pr_id}\n"
        f"    checkpoint_manifest_sha256: {_checkpoint_sha(second_checkpoint)}\n"
        "    normative_change: true\n"
        "    note: 'adds the second behaviour'\n"
    )

    return {
        "repo": repo,
        "timeline": timeline_dir,
        "checkpoints": checkpoints,
        "questions": questions,
        "revisions": revisions,
        "first_checkpoint": first_checkpoint,
        "first_cluster": epoch_id,
        "last_checkpoint": second_checkpoint,
        "last_cluster": pr_id,
    }


@pytest.fixture
def draft_workspace(tmp_path: Path, timeline_dir: Path) -> dict[str, Any]:
    """A gate-clean workspace: draft repo, checkpoints, questions, revisions."""
    return _build_draft_workspace(tmp_path, timeline_dir)


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "rfc: SPEC-1\n"
        "title: 'A reconstructed specification'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: 'The system does the thing.'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: behaviour\n"
        "    anchors:\n"
        "      - evidence_class: code\n"
        "        locator: src/a.py\n"
        f"        commit: '{'0' * 40}'\n"
        "      - evidence_class: paper\n"
        "        locator: 10.1000/xyz\n"
        "  'spec:2.1':\n"
        "    text: 'The system also does this.'\n"
        "    section: '2.1'\n"
        "    level: SHOULD\n"
        "    layer: behaviour\n"
        "    question-id: q-001\n"
    )
    return path


@pytest.fixture
def sparse_workspace(tmp_path: Path, timeline_dir: Path) -> dict[str, Path]:
    """A workspace of two clusters with only the first checkpointed.

    Laid out so ``tmp_path`` itself is a valid workspace root: the completeness
    verb derives every input from it.
    """
    epoch_id = read_clusters(timeline_dir)[0]["id"]

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(_manifest_text(with_second_claim=False))
    checkpoints = tmp_path / "checkpoints"
    checkpoint = write_checkpoint(manifest, timeline_dir, epoch_id, checkpoints)

    repo = tmp_path / "draft"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "draft-test-spec.md").write_text("# Spec\n\nNo citations yet.\n")
    git(repo, "add", "draft-test-spec.md")
    git(repo, "commit", "-m", "revision 00")
    git(repo, "tag", "draft-test-spec-00")

    revisions = tmp_path / "revisions.yaml"
    revisions.write_text(
        "revisions:\n"
        "  draft-test-spec-00:\n"
        f"    cluster_id: {epoch_id}\n"
        f"    checkpoint_manifest_sha256: {_checkpoint_sha(checkpoint)}\n"
        "    normative_change: true\n"
        "    note: 'initial reconstruction'\n"
    )
    return {
        "root": tmp_path,
        "repo": repo,
        "timeline": timeline_dir,
        "checkpoints": checkpoints,
        "manifest": manifest,
        "revisions": revisions,
    }


#: One record structure, appended to the second cluster's manifest so the last
#: checkpoint freezes a rendering the draft can paste.
STRUCTURED_BLOCK = (
    "structures:\n"
    "  header:\n"
    "    kind: record\n"
    "    title: Message header\n"
    "    section: '4'\n"
    "    fields:\n"
    "      - name: version\n"
    "        type: uint8\n"
    "        claim: spec:1.1\n"
)


def _retag_draft_with(
    workspace: dict[str, Any], transform: Callable[[str], str]
) -> Path:
    """Rewrite the draft, commit, and move the last tag onto the new commit."""
    repo = workspace["repo"]
    draft_file = repo / "draft-test-spec.md"
    draft_file.write_text(transform(draft_file.read_text()))
    git(repo, "add", "draft-test-spec.md")
    git(repo, "commit", "-m", "edit")
    git(repo, "tag", "-f", "draft-test-spec-01")
    return repo


def _append_to_draft(workspace: dict[str, Any], text: str) -> Path:
    return _retag_draft_with(workspace, lambda body: body + "\n" + text)


def _record_consolidation(
    workspace: dict[str, Any], ordinal: int, checkpoint: str
) -> str:
    """Append one consolidation revision and tag the current HEAD."""
    directory = workspace["consolidations"] / Path(checkpoint).name
    sha = json.loads((directory / "checkpoint.json").read_text())["manifest_sha256"]
    tag = f"draft-test-spec-{ordinal:02d}"
    workspace["revisions"].write_text(
        workspace["revisions"].read_text() + f"  {tag}:\n"
        f"    cluster_id: {workspace['last_cluster']}\n"
        f"    checkpoint_manifest_sha256: {sha}\n"
        "    normative_change: false\n"
        "    note: 'consolidated'\n"
        "    kind: consolidation\n"
        f"    checkpoint: {checkpoint}\n"
    )
    git(workspace["repo"], "tag", tag)
    return tag


@pytest.fixture
def structured_workspace(tmp_path: Path, timeline_dir: Path) -> dict[str, Any]:
    """A workspace whose last checkpoint freezes one structure block, pasted."""
    from ai_rfc.draft.structures import render_all
    from ai_rfc.schema import load as load_manifest

    workspace = _build_draft_workspace(tmp_path, timeline_dir, extra=STRUCTURED_BLOCK)
    manifest = load_manifest(workspace["last_checkpoint"] / "manifest.yaml")
    _retag_draft_with(workspace, lambda body: body + "\n" + render_all(manifest))
    return workspace


@pytest.fixture
def consolidated_workspace(
    structured_workspace: dict[str, Any], tmp_path: Path
) -> dict[str, Any]:
    """``structured_workspace`` plus one consolidation revision after it."""
    from ai_rfc.draft.checkpoint import write_consolidation_checkpoint

    workspace = structured_workspace
    consolidations = tmp_path / "consolidations"
    base = workspace["last_checkpoint"]
    write_consolidation_checkpoint(
        base / "manifest.yaml", 1, base, workspace["last_cluster"], consolidations
    )
    workspace["consolidations"] = consolidations
    _record_consolidation(workspace, ordinal=2, checkpoint="consolidations/01")
    return workspace
