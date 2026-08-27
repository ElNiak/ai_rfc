from pathlib import Path

import pytest

from ai_rfc_server.testing import git
from experiment import ExperimentError
from experiment.workspace import Target, out_of_window, scaffold_draft


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
