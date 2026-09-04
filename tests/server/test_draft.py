import pytest
import yaml

from ai_rfc.server.core import CoreError
from ai_rfc.server.core.draft import commit_draft, tag_revision
from ai_rfc.server.core.gates import citation_gate, write_checkpoint
from ai_rfc.server.core.queries import cluster_next
from ai_rfc.server.core.revisions import record_revision
from ai_rfc.server.testing import git


def _draft(workspace):
    return workspace.workspace / "draft"


def _recorded(workspace, tag="draft-test-spec-00"):
    first = cluster_next(workspace)
    assert write_checkpoint(workspace, first["id"])["exit_code"] == 0
    record_revision(workspace, tag, first["id"], True, "initial")
    return first["id"]


def test_commit_draft_refuses_a_clean_tree(workspace):
    with pytest.raises(CoreError) as excinfo:
        commit_draft(workspace, "noop")
    assert "nothing to commit" in str(excinfo.value)


def test_commit_draft_commits_every_change(workspace):
    prose = _draft(workspace) / "draft-test-spec.md"
    prose.write_text(prose.read_text() + "\nMore prose.\n")
    result = commit_draft(workspace, "more prose")
    assert result["files"] == ["draft-test-spec.md"]
    assert git(_draft(workspace), "rev-parse", "HEAD") == result["commit"]
    assert git(_draft(workspace), "status", "--porcelain") == ""


def test_commit_draft_needs_a_message(workspace):
    with pytest.raises(CoreError):
        commit_draft(workspace, "   ")


def test_tag_revision_requires_a_recorded_entry(workspace):
    with pytest.raises(CoreError) as excinfo:
        tag_revision(workspace, "draft-test-spec-00", "rev 00")
    assert "record the revision first" in str(excinfo.value)


def test_tag_revision_rejects_a_malformed_tag(workspace):
    with pytest.raises(CoreError):
        tag_revision(workspace, "v1", "rev")


def test_tag_revision_refuses_a_dirty_tree(workspace):
    _recorded(workspace)
    prose = _draft(workspace) / "draft-test-spec.md"
    prose.write_text(prose.read_text() + "\nUncommitted.\n")
    with pytest.raises(CoreError) as excinfo:
        tag_revision(workspace, "draft-test-spec-00", "rev 00")
    assert "uncommitted" in str(excinfo.value)
    assert git(_draft(workspace), "tag", "-l") == ""


def test_tag_revision_creates_the_tag_when_both_gates_pass(workspace):
    _recorded(workspace)
    result = tag_revision(workspace, "draft-test-spec-00", "revision 00")
    assert result["exit_code"] == 0 and result["rolled_back"] is False
    assert git(_draft(workspace), "tag", "-l") == "draft-test-spec-00"
    assert (
        git(_draft(workspace), "rev-list", "-n", "1", "draft-test-spec-00")
        == result["commit"]
    )
    assert citation_gate(workspace, strict=True)["exit_code"] == 0
    with pytest.raises(CoreError):
        tag_revision(workspace, "draft-test-spec-00", "again")


def test_tag_revision_stops_on_manifest_gate_findings(workspace):
    _recorded(workspace)
    document = yaml.safe_load(workspace.manifest.read_text())
    document["requirements"]["t:1.1"]["status"] = "confirmed"
    workspace.manifest.write_text(yaml.safe_dump(document, sort_keys=True))
    result = tag_revision(workspace, "draft-test-spec-00", "revision 00")
    assert result["exit_code"] == 3 and result["stage"] == "manifest_gate"
    assert git(_draft(workspace), "tag", "-l") == ""


def test_tag_revision_rolls_back_on_citation_findings(workspace):
    prose = _draft(workspace) / "draft-test-spec.md"
    prose.write_text(prose.read_text() + "\nGhost. `ai_rfc:t:9.9`\n")
    commit_draft(workspace, "cite a ghost")
    _recorded(workspace)
    result = tag_revision(workspace, "draft-test-spec-00", "revision 00")
    assert result["exit_code"] == 3 and result["stage"] == "citation_gate"
    assert result["rolled_back"] is True
    assert any("t:9.9" in finding for finding in result["findings"])
    assert git(_draft(workspace), "tag", "-l") == ""


def test_tag_revision_refuses_when_the_build_has_findings(
    workspace, monkeypatch, tmp_path
):
    from ai_rfc.server.core import draft as draft_core
    from ai_rfc.server.paths import resolve_context

    _recorded(workspace)
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
    monkeypatch.setattr(
        draft_core,
        "draft_build",
        lambda ctx, ref="HEAD": {
            "exit_code": 0,
            "stderr": [],
            "findings": ["broken reference RFC9999 (not in the refcache)"],
            "commit": None,
            "outputs": {},
        },
    )
    result = tag_revision(resolve_context(), "draft-test-spec-00", "msg")
    assert result["stage"] == "draft_build" and result["exit_code"] == 3
    assert result["findings"] == ["broken reference RFC9999 (not in the refcache)"]
    assert git(_draft(workspace), "tag", "-l") == ""
