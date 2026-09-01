import hashlib
import json
from pathlib import Path

import pytest

from ai_rfc_server.testing import git
from experiment import ExperimentError
from .conftest import fixture_target
from experiment.workspace import (
    DIGEST_FILE,
    HARNESS_MARKER,
    RECORD_FILE,
    TARGETS,
    copy_workspace,
    out_of_window,
    prepare,
    preseed,
    reseal,
    scaffold_draft,
    verify_digest,
    write_digest,
)


def test_out_of_window_keeps_order():
    assert out_of_window(range(1, 8), (2, 4)) == [1, 5, 6, 7]
    assert out_of_window([], (2, 4)) == []


def test_pristine_name_encodes_target_and_window():
    assert fixture_target(Path("/x")).pristine_name == "fixture-w02-02"


def test_scaffold_strips_agent_files_and_seeds_the_draft(template_repo, tmp_path):
    template, commit = template_repo
    dest = tmp_path / "draft"
    head = scaffold_draft(
        dest, fixture_target(tmp_path), template=template, template_commit=commit
    )
    body = (dest / "draft-test-fixture.md").read_text()
    assert (dest / "Makefile").exists()
    assert not (dest / "CLAUDE.md").exists() and not (dest / ".claude").exists()
    assert "draft-*" not in (dest / ".gitignore").read_text()
    assert "docname: draft-test-fixture-latest" in body
    assert 'title: "Fixture"' in body and "specification of fixture" in body
    assert "`ai_rfc:" not in body
    assert git(dest, "log", "--oneline").count("\n") == 0
    assert git(dest, "config", "user.name") == "ai-rfc-harness"
    assert head == git(dest, "rev-parse", "HEAD")


def test_scaffold_is_byte_deterministic(template_repo, tmp_path):
    template, commit = template_repo
    first = scaffold_draft(
        tmp_path / "a",
        fixture_target(tmp_path),
        template=template,
        template_commit=commit,
    )
    second = scaffold_draft(
        tmp_path / "b",
        fixture_target(tmp_path),
        template=template,
        template_commit=commit,
    )
    assert first == second


def test_scaffold_refuses_an_existing_destination(template_repo, tmp_path):
    template, commit = template_repo
    dest = tmp_path / "draft"
    dest.mkdir()
    with pytest.raises(ExperimentError) as excinfo:
        scaffold_draft(
            dest, fixture_target(tmp_path), template=template, template_commit=commit
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


@pytest.fixture
def sealed(fixture_workspace: Path) -> Path:
    """A fixture workspace sealed the way a pristine one is: record + digest."""
    record = {
        "clone_head": git(fixture_workspace / "clone", "rev-parse", "HEAD"),
        "draft_head": git(fixture_workspace / "draft", "rev-parse", "HEAD"),
    }
    (fixture_workspace / RECORD_FILE).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    write_digest(fixture_workspace)
    return fixture_workspace


def test_digest_covers_content_but_not_git_or_itself(sealed):
    covered = [
        line.partition("  ")[2]
        for line in (sealed / DIGEST_FILE).read_text().splitlines()
    ]
    assert "manifest.yaml" in covered
    assert "draft/draft-test-spec.md" in covered
    assert not any(part == ".git" for path in covered for part in path.split("/"))
    assert DIGEST_FILE not in covered and RECORD_FILE not in covered
    assert verify_digest(sealed) == []


def test_verify_reports_every_kind_of_drift_in_path_order(sealed):
    (sealed / "manifest.yaml").write_text("rfc: X\ntitle: t\nrequirements: {}\n")
    (sealed / "extra.txt").write_text("x\n")
    (sealed / "questions.yaml").unlink()
    assert verify_digest(sealed) == [
        "unexpected: extra.txt",
        "modified: manifest.yaml",
        "missing: questions.yaml",
    ]


def test_verify_reports_a_missing_manifest(fixture_workspace):
    assert verify_digest(fixture_workspace) == ["pristine.sha256 is missing"]


def test_copy_verifies_and_never_reuses_a_destination(sealed, tmp_path):
    copy = copy_workspace(sealed, tmp_path / "run" / "workspace")
    assert verify_digest(copy) == []
    assert git(copy / "draft", "rev-parse", "HEAD") == git(
        sealed / "draft", "rev-parse", "HEAD"
    )
    with pytest.raises(ExperimentError) as excinfo:
        copy_workspace(sealed, tmp_path / "run" / "workspace")
    assert "never reuses" in str(excinfo.value)


def test_copy_refuses_a_tampered_pristine(sealed, tmp_path):
    (sealed / "manifest.yaml").write_text("rfc: X\ntitle: t\nrequirements: {}\n")
    with pytest.raises(ExperimentError) as excinfo:
        copy_workspace(sealed, tmp_path / "run" / "workspace")
    assert "does not verify" in str(excinfo.value)


def _work(workspace: Path) -> str:
    """Advance a workspace the way a session does: prose committed, manifest grown."""
    (workspace / "manifest.yaml").write_text("rfc: X\ntitle: t\nrequirements: {}\n")
    (workspace / "draft" / "draft-test-spec.md").write_text("# advanced\n")
    git(workspace / "draft", "add", "-A")
    git(workspace / "draft", "commit", "-q", "-m", "prose for a cluster")
    return git(workspace / "draft", "rev-parse", "HEAD")


def test_reseal_seals_a_copy_and_leaves_the_used_workspace_alone(sealed, tmp_path):
    _work(sealed)
    before = (sealed / DIGEST_FILE).read_text()
    drift_before = verify_digest(sealed)
    assert drift_before, "a worked workspace must have drifted from its seal"

    baseline = reseal(sealed, tmp_path / "root" / "pristine" / "cont1")

    assert verify_digest(baseline) == []
    # The source keeps its drift: reseal continues a run, it does not repair one.
    assert verify_digest(sealed) == drift_before
    assert (sealed / DIGEST_FILE).read_text() == before


def test_reseal_records_the_draft_head_the_run_advanced_to(sealed, tmp_path):
    was = json.loads((sealed / RECORD_FILE).read_text())["draft_head"]
    now = _work(sealed)

    baseline = reseal(sealed, tmp_path / "root" / "pristine" / "cont1")

    record = json.loads((baseline / RECORD_FILE).read_text())
    assert record["draft_head"] == now != was
    assert record["resealed_from"] == str(sealed)


def test_a_resealed_baseline_is_one_a_campaign_can_copy(sealed, tmp_path):
    now = _work(sealed)
    baseline = reseal(sealed, tmp_path / "root" / "pristine" / "cont1")

    copy = copy_workspace(baseline, tmp_path / "run2" / "workspace")

    assert verify_digest(copy) == []
    assert git(copy / "draft", "rev-parse", "HEAD") == now


def test_reseal_refuses_a_workspace_that_was_never_prepared(
    fixture_workspace, tmp_path
):
    with pytest.raises(ExperimentError) as excinfo:
        reseal(fixture_workspace, tmp_path / "root" / "pristine" / "cont1")
    assert "not a prepared pristine workspace" in str(excinfo.value)


def test_reseal_refuses_an_existing_destination(sealed, tmp_path):
    _work(sealed)
    dest = tmp_path / "root" / "pristine" / "cont1"
    reseal(sealed, dest)
    with pytest.raises(ExperimentError) as excinfo:
        reseal(sealed, dest)
    assert "prepared once" in str(excinfo.value)


def test_reseal_refuses_a_clone_whose_head_moved(sealed, tmp_path):
    _work(sealed)
    (sealed / "clone" / "intruder.txt").write_text("x\n")
    git(sealed / "clone", "add", "-A")
    git(sealed / "clone", "commit", "-q", "-m", "the clone must never move")

    with pytest.raises(ExperimentError) as excinfo:
        reseal(sealed, tmp_path / "root" / "pristine" / "cont1")
    assert "clone HEAD" in str(excinfo.value)


def _prepare(fixture_workspace, panther_repo, template_repo, tmp_path):
    template, commit = template_repo
    return prepare(
        fixture_target(fixture_workspace),
        root=tmp_path / "root",
        panther_repo=panther_repo,
        template=template,
        template_commit=commit,
    )


def test_prepare_builds_the_pristine_tree(
    fixture_workspace, panther_repo, template_repo, tmp_path
):
    pristine = _prepare(fixture_workspace, panther_repo, template_repo, tmp_path)
    assert pristine == tmp_path / "root" / "pristine" / "fixture-w02-02"
    ids = _cluster_ids(pristine)
    assert sorted(p.name for p in (pristine / "clusters").iterdir()) == sorted(ids)
    assert (pristine / "manifest.yaml").read_text() == (
        "rfc: FIX-1\ntitle: Fixture\nrequirements: {}\n"
    )
    assert (pristine / "questions.yaml").read_text() == "questions: {}\n"
    assert (pristine / "revisions.yaml").read_text() == "revisions: {}\n"
    assert (pristine / "interviews").is_dir()
    assert (pristine / "draft" / "draft-test-fixture.md").exists()
    record = json.loads((pristine / RECORD_FILE).read_text())
    assert record["template_commit"] == template_repo[1]
    assert record["pre_seeded"] == [ids[0]]
    assert record["cluster_count"] == 2
    assert record["window"] == [2, 2]
    assert record["clone_head"] == git(pristine / "clone", "rev-parse", "HEAD")
    assert record["draft_head"] == git(pristine / "draft", "rev-parse", "HEAD")
    assert (pristine / "checkpoints" / ids[0] / HARNESS_MARKER).exists()
    assert (pristine / "checkpoints" / ids[0] / "checkpoint.json").exists()
    assert not (pristine / "checkpoints" / ids[1]).exists()
    assert verify_digest(pristine) == []


def test_prepared_window_is_the_only_unprocessed_range(
    fixture_workspace, panther_repo, template_repo, tmp_path, monkeypatch
):
    pristine = _prepare(fixture_workspace, panther_repo, template_repo, tmp_path)
    monkeypatch.setenv("AI_RFC_WORKSPACE", str(pristine))

    from ai_rfc_server.core.queries import cluster_next, status
    from ai_rfc_server.paths import resolve_context

    ctx = resolve_context()
    assert cluster_next(ctx)["ordinal"] == 2
    composite = status(ctx)
    assert composite["clusters_total"] == 2
    assert composite["clusters_processed"] == 1


def test_prepare_refuses_to_overwrite_or_to_run_without_the_substrate(
    fixture_workspace, panther_repo, template_repo, tmp_path
):
    _prepare(fixture_workspace, panther_repo, template_repo, tmp_path)
    with pytest.raises(ExperimentError) as overwrite:
        _prepare(fixture_workspace, panther_repo, template_repo, tmp_path)
    assert "prepared once" in str(overwrite.value)

    empty = tmp_path / "empty-source"
    empty.mkdir()
    template, commit = template_repo
    with pytest.raises(ExperimentError) as missing:
        prepare(
            fixture_target(empty),
            root=tmp_path / "other-root",
            panther_repo=panther_repo,
            template=template,
            template_commit=commit,
        )
    assert str(empty / "clone") in str(missing.value)


def test_cli_workspace_prepare_reports_the_tree(
    fixture_workspace, panther_repo, template_repo, tmp_path, monkeypatch, capsys
):
    from experiment.cli import main

    template, commit = template_repo
    monkeypatch.setitem(TARGETS, "fixture", fixture_target(fixture_workspace))
    code = main(
        [
            "workspace",
            "prepare",
            "fixture",
            "--root",
            str(tmp_path / "root"),
            "--panther-repo",
            str(panther_repo),
            "--template",
            template,
            "--template-commit",
            commit,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert f"pristine: {tmp_path / 'root' / 'pristine' / 'fixture-w02-02'}" in out
    assert "clusters: 2  pre-seeded: 1  window: [2, 2]" in out
