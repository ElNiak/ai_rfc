import hashlib
import json
from pathlib import Path

import pytest

from ai_rfc_server.testing import git
from experiment import ExperimentError
from experiment.workspace import (
    HARNESS_MARKER,
    Target,
    out_of_window,
    preseed,
    scaffold_draft,
)


@pytest.fixture
def template_repo(tmp_path: Path) -> tuple[str, str]:
    """A local stand-in for auto-i-d-template, carrying agent files to strip."""
    repo = tmp_path / "template"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("draft-*\n*.swp\n")
    (repo / "Makefile").write_text("all:\n\t@echo build\n")
    (repo / "CLAUDE.md").write_text("template agent notes\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "template", date="2026-01-01T00:00:09+00:00")
    return str(repo), git(repo, "rev-parse", "HEAD")


def _target(source: Path) -> Target:
    return Target(
        name="fixture",
        source=source,
        forge_snapshot=None,
        window=(2, 2),
        draft_name="draft-test-fixture",
        rfc_id="FIX-1",
        title="Fixture",
        abbrev="Fix",
    )


def test_out_of_window_keeps_order():
    assert out_of_window(range(1, 8), (2, 4)) == [1, 5, 6, 7]
    assert out_of_window([], (2, 4)) == []


def test_pristine_name_encodes_target_and_window():
    assert _target(Path("/x")).pristine_name == "fixture-w02-02"


def test_scaffold_strips_agent_files_and_seeds_the_draft(template_repo, tmp_path):
    template, commit = template_repo
    dest = tmp_path / "draft"
    head = scaffold_draft(
        dest, _target(tmp_path), template=template, template_commit=commit
    )
    body = (dest / "draft-test-fixture.md").read_text()
    assert (dest / "Makefile").exists()
    assert not (dest / "CLAUDE.md").exists() and not (dest / ".claude").exists()
    assert "draft-*" not in (dest / ".gitignore").read_text()
    assert "docname: draft-test-fixture-latest" in body
    assert 'title: "Fixture"' in body and "specification of fixture" in body
    assert "`a_rfc:" not in body
    assert git(dest, "log", "--oneline").count("\n") == 0
    assert git(dest, "config", "user.name") == "arfc-harness"
    assert head == git(dest, "rev-parse", "HEAD")


def test_scaffold_is_byte_deterministic(template_repo, tmp_path):
    template, commit = template_repo
    first = scaffold_draft(
        tmp_path / "a", _target(tmp_path), template=template, template_commit=commit
    )
    second = scaffold_draft(
        tmp_path / "b", _target(tmp_path), template=template, template_commit=commit
    )
    assert first == second


def test_scaffold_refuses_an_existing_destination(template_repo, tmp_path):
    template, commit = template_repo
    dest = tmp_path / "draft"
    dest.mkdir()
    with pytest.raises(ExperimentError) as excinfo:
        scaffold_draft(
            dest, _target(tmp_path), template=template, template_commit=commit
        )
    assert "scaffolded once" in str(excinfo.value)


def _clusters(workspace: Path) -> list[dict]:
    rows = (workspace / "timeline" / "clusters.jsonl").read_text().splitlines()
    return [json.loads(row) for row in rows]


def _cluster_ids(workspace: Path) -> list[str]:
    return [row["id"] for row in _clusters(workspace)]


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_preseed_makes_the_server_skip_the_cluster(fixture_workspace, panther_repo):
    ordinals = [row["ordinal"] for row in _clusters(fixture_workspace)]
    assert ordinals == [1, 2]
    seeded = preseed(fixture_workspace, panther_repo, out_of_window(ordinals, (2, 2)))
    assert seeded == [_cluster_ids(fixture_workspace)[0]]
    marker = json.loads(
        (fixture_workspace / "checkpoints" / seeded[0] / HARNESS_MARKER).read_text()
    )
    assert marker == {"ordinal": 1, "pre_seeded": True, "reason": "outside window"}

    from ai_rfc_server.core.queries import cluster_next, status
    from ai_rfc_server.paths import resolve_context

    ctx = resolve_context()
    assert cluster_next(ctx)["ordinal"] == 2
    composite = status(ctx)
    assert composite["clusters_total"] == 2
    assert composite["clusters_processed"] == 1


def test_preseed_rejects_an_unknown_ordinal(fixture_workspace, panther_repo):
    with pytest.raises(ExperimentError) as excinfo:
        preseed(fixture_workspace, panther_repo, [99])
    assert "no cluster with ordinal 99" in str(excinfo.value)


def test_preseed_leaves_substrate_artifacts_untouched(fixture_workspace, panther_repo):
    before = _tree_digest(fixture_workspace / "timeline")
    manifest_before = (fixture_workspace / "manifest.yaml").read_bytes()
    preseed(fixture_workspace, panther_repo, [1])
    assert _tree_digest(fixture_workspace / "timeline") == before
    assert (fixture_workspace / "manifest.yaml").read_bytes() == manifest_before
