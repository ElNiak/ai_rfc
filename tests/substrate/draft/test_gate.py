from pathlib import Path

import pytest
import yaml

from ai_rfc.draft.gate import GateError, draft_text, load_revisions, run_gate

from .conftest import _append_to_draft, _record_consolidation, _retag_draft_with, git

pytestmark = pytest.mark.unit


def _gate(workspace: dict[str, Path]) -> tuple[str, ...]:
    return run_gate(
        workspace["repo"],
        workspace["timeline"],
        workspace["checkpoints"],
        workspace["questions"],
        workspace["revisions"],
    )


def _patch_revisions(workspace: dict[str, Path], tag: str, **changes) -> None:
    document = yaml.safe_load(workspace["revisions"].read_text())
    document["revisions"][tag].update(changes)
    workspace["revisions"].write_text(yaml.safe_dump(document, sort_keys=True))


def test_clean_workspace_gates_clean(draft_workspace):
    assert _gate(draft_workspace) == ()


def test_register_tag_missing_from_repo_is_found(draft_workspace):
    git(draft_workspace["repo"], "tag", "-d", "draft-test-spec-01")
    findings = _gate(draft_workspace)
    assert any("draft-test-spec-01" in finding for finding in findings)


def test_repo_revision_tag_missing_from_register_is_found(draft_workspace):
    git(draft_workspace["repo"], "tag", "draft-test-spec-02")
    findings = _gate(draft_workspace)
    assert any("draft-test-spec-02" in finding for finding in findings)


def test_non_increasing_cluster_ordinals_are_found(draft_workspace):
    document = yaml.safe_load(draft_workspace["revisions"].read_text())
    first = document["revisions"]["draft-test-spec-00"]
    _patch_revisions(
        draft_workspace,
        "draft-test-spec-01",
        cluster_id=first["cluster_id"],
        checkpoint_manifest_sha256=first["checkpoint_manifest_sha256"],
    )
    findings = _gate(draft_workspace)
    assert any("ordinal" in finding for finding in findings)


def test_unknown_cluster_id_is_found(draft_workspace):
    _patch_revisions(
        draft_workspace, "draft-test-spec-01", cluster_id="c9999-pr-000000000000"
    )
    findings = _gate(draft_workspace)
    assert any("c9999" in finding for finding in findings)


def test_edited_checkpoint_manifest_is_found(draft_workspace):
    document = yaml.safe_load(draft_workspace["revisions"].read_text())
    cluster_id = document["revisions"]["draft-test-spec-01"]["cluster_id"]
    stored = draft_workspace["checkpoints"] / cluster_id / "manifest.yaml"
    stored.write_bytes(stored.read_bytes() + b"# drift\n")
    findings = _gate(draft_workspace)
    assert any("edited" in finding or "immutable" in finding for finding in findings)


def test_citation_of_unknown_claim_is_found(draft_workspace):
    repo = draft_workspace["repo"]
    git(repo, "tag", "-d", "draft-test-spec-01")
    draft_file = repo / "draft-test-spec.md"
    draft_file.write_text(draft_file.read_text() + "\nGhost. `ai_rfc:spec:9.9`\n")
    git(repo, "add", "draft-test-spec.md")
    git(repo, "commit", "-m", "revision 01 with a ghost citation")
    git(repo, "tag", "draft-test-spec-01")
    findings = _gate(draft_workspace)
    assert any("spec:9.9" in finding for finding in findings)


def test_no_change_marker_with_changed_citations_is_found(draft_workspace):
    _patch_revisions(draft_workspace, "draft-test-spec-01", normative_change=False)
    findings = _gate(draft_workspace)
    assert any("normative" in finding for finding in findings)


def _recheckpoint(
    draft_workspace: dict[str, Path],
    timeline_dir: Path,
    tag: str,
    manifest_text: str,
    name: str,
) -> None:
    """Replace one revision's checkpoint with ``manifest_text`` and re-pin it."""
    from ai_rfc.draft.checkpoint import write_checkpoint

    from .conftest import _checkpoint_sha

    document = yaml.safe_load(draft_workspace["revisions"].read_text())
    cluster_id = document["revisions"][tag]["cluster_id"]
    manifest = timeline_dir.parent / name
    manifest.write_text(manifest_text)
    stale = draft_workspace["checkpoints"] / cluster_id
    (stale / "manifest.yaml").unlink()
    (stale / "checkpoint.json").unlink()
    stale.rmdir()
    checkpoint_dir = write_checkpoint(
        manifest, timeline_dir, cluster_id, draft_workspace["checkpoints"]
    )
    _patch_revisions(
        draft_workspace, tag, checkpoint_manifest_sha256=_checkpoint_sha(checkpoint_dir)
    )


def test_a_normative_revision_repeating_the_previous_manifest_is_found(
    draft_workspace, timeline_dir: Path
):
    """A normative change that checkpointed nothing new is not a change.

    Without this the second revision can claim new normative content while
    freezing the manifest it inherited, so the revision history reads as
    progress that no evidence backs.
    """
    from .conftest import _manifest_text

    _recheckpoint(
        draft_workspace,
        timeline_dir,
        "draft-test-spec-01",
        _manifest_text(with_second_claim=False),
        "m2-unchanged.yaml",
    )
    findings = _gate(draft_workspace)
    assert (
        "draft-test-spec-01: recorded as a normative change, but its checkpoint "
        "manifest is identical to the previous revision's" in findings
    )


def test_a_first_normative_revision_over_an_empty_manifest_is_found(
    draft_workspace, timeline_dir: Path
):
    """The first revision must checkpoint at least one claim to be normative.

    The harness pre-seeds an empty manifest, so a run that reconstructs
    nothing and records a revision anyway would otherwise gate clean.
    """
    _recheckpoint(
        draft_workspace,
        timeline_dir,
        "draft-test-spec-00",
        "rfc: SPEC-1\ntitle: 'A reconstructed specification'\nrequirements: {}\n",
        "m1-empty.yaml",
    )
    findings = _gate(draft_workspace)
    assert (
        "draft-test-spec-00: recorded as a normative change, but its checkpoint "
        "manifest holds no claims" in findings
    )


def test_unregistered_question_id_is_found(draft_workspace, timeline_dir: Path):
    from ai_rfc.draft.checkpoint import write_checkpoint

    from .conftest import _checkpoint_sha, _manifest_text

    document = yaml.safe_load(draft_workspace["revisions"].read_text())
    cluster_id = document["revisions"]["draft-test-spec-01"]["cluster_id"]
    manifest = timeline_dir.parent / "m2-bad-question.yaml"
    manifest.write_text(_manifest_text(with_second_claim=True, question_id="q-404"))
    stale = draft_workspace["checkpoints"] / cluster_id
    (stale / "manifest.yaml").unlink()
    (stale / "checkpoint.json").unlink()
    stale.rmdir()
    checkpoint_dir = write_checkpoint(
        manifest, timeline_dir, cluster_id, draft_workspace["checkpoints"]
    )
    _patch_revisions(
        draft_workspace,
        "draft-test-spec-01",
        checkpoint_manifest_sha256=_checkpoint_sha(checkpoint_dir),
    )
    findings = _gate(draft_workspace)
    assert any("q-404" in finding for finding in findings)


def test_missing_revisions_file_is_a_gate_error(draft_workspace):
    draft_workspace["revisions"].unlink()
    with pytest.raises((GateError, OSError)):
        _gate(draft_workspace)


def test_malformed_register_entry_is_a_gate_error(draft_workspace):
    document = yaml.safe_load(draft_workspace["revisions"].read_text())
    del document["revisions"]["draft-test-spec-01"]["normative_change"]
    draft_workspace["revisions"].write_text(yaml.safe_dump(document, sort_keys=True))
    with pytest.raises(GateError) as excinfo:
        _gate(draft_workspace)
    assert "normative_change" in str(excinfo.value)


def test_draft_text_reads_the_single_draft_at_a_ref(draft_workspace):
    name, text = draft_text(draft_workspace["repo"], "draft-test-spec-00")
    assert name == "draft-test-spec.md"
    assert "`ai_rfc:spec:1.1`" in text and "`ai_rfc:spec:2.1`" not in text


def test_draft_text_refuses_a_ref_without_one_draft(draft_workspace, tmp_path):
    with pytest.raises(GateError) as excinfo:
        draft_text(draft_workspace["repo"], "no-such-ref")
    assert "no-such-ref" in str(excinfo.value)


def test_an_old_revisions_file_still_loads_as_cluster_rounds(tmp_path):
    path = tmp_path / "revisions.yaml"
    path.write_text(
        "revisions:\n"
        "  draft-test-01:\n"
        "    cluster_id: c1\n"
        "    checkpoint_manifest_sha256: " + "a" * 64 + "\n"
        "    normative_change: true\n"
        "    note: first\n"
    )
    entry = load_revisions(path)[0]
    assert entry.kind == "cluster"
    assert entry.checkpoint is None


def test_a_consolidation_entry_must_name_its_checkpoint(tmp_path):
    path = tmp_path / "revisions.yaml"
    path.write_text(
        "revisions:\n"
        "  draft-test-01:\n"
        "    cluster_id: c1\n"
        "    checkpoint_manifest_sha256: " + "a" * 64 + "\n"
        "    normative_change: false\n"
        "    note: consolidated\n"
        "    kind: consolidation\n"
    )
    with pytest.raises(GateError) as error:
        load_revisions(path)
    assert "checkpoint" in str(error.value)


def test_a_cluster_entry_may_not_name_a_consolidation_checkpoint(tmp_path):
    path = tmp_path / "revisions.yaml"
    path.write_text(
        "revisions:\n"
        "  draft-test-01:\n"
        "    cluster_id: c1\n"
        "    checkpoint_manifest_sha256: " + "a" * 64 + "\n"
        "    normative_change: true\n"
        "    note: first\n"
        "    checkpoint: consolidations/01\n"
    )
    with pytest.raises(GateError) as error:
        load_revisions(path)
    assert "only a consolidation" in str(error.value)


def test_an_unknown_kind_is_refused(tmp_path):
    path = tmp_path / "revisions.yaml"
    path.write_text(
        "revisions:\n"
        "  draft-test-01:\n"
        "    cluster_id: c1\n"
        "    checkpoint_manifest_sha256: " + "a" * 64 + "\n"
        "    normative_change: true\n"
        "    note: first\n"
        "    kind: editorial\n"
    )
    with pytest.raises(GateError) as error:
        load_revisions(path)
    assert "editorial" in str(error.value)


def test_a_block_naming_no_frozen_structure_is_a_finding(structured_workspace):
    # Check 9.
    ws = structured_workspace
    _append_to_draft(
        ws,
        "{::comment}\nai_rfc:struct:ghost begin\n{:/comment}\nx\n"
        "{::comment}\nai_rfc:struct:ghost end\n{:/comment}\n",
    )
    findings = run_gate(
        ws["repo"], ws["timeline"], ws["checkpoints"], ws["questions"], ws["revisions"]
    )
    assert any("ghost" in f and "not a structure" in f for f in findings)


def test_a_one_byte_edit_to_a_rendered_block_is_a_finding(structured_workspace):
    # Check 10 — the property the whole design rests on.
    ws = structured_workspace
    _retag_draft_with(ws, lambda text: text.replace("uint8", "uint9"))
    findings = run_gate(
        ws["repo"], ws["timeline"], ws["checkpoints"], ws["questions"], ws["revisions"]
    )
    assert any("does not match the frozen" in f for f in findings)


def test_an_untouched_structured_draft_gates_clean(structured_workspace):
    ws = structured_workspace
    assert (
        run_gate(
            ws["repo"],
            ws["timeline"],
            ws["checkpoints"],
            ws["questions"],
            ws["revisions"],
        )
        == ()
    )


def test_a_malformed_delimiter_is_a_finding(structured_workspace):
    # Check 11.
    ws = structured_workspace
    _retag_draft_with(
        ws, lambda text: text + "{::comment}\nai_rfc:struct:orphan end\n{:/comment}\n"
    )
    findings = run_gate(
        ws["repo"], ws["timeline"], ws["checkpoints"], ws["questions"], ws["revisions"]
    )
    assert any("closed but never opened" in f for f in findings)


def test_a_faithful_consolidation_gates_clean(consolidated_workspace):
    # The consolidation reuses its predecessor's cluster id (so the ordinal
    # cannot increase) and its pasted legend cites claims the previous revision
    # already cited; neither existing pass may report it (D52).
    ws = consolidated_workspace
    assert (
        run_gate(
            ws["repo"],
            ws["timeline"],
            ws["checkpoints"],
            ws["questions"],
            ws["revisions"],
            consolidations_dir=ws["consolidations"],
        )
        == ()
    )


def test_a_consolidation_that_drops_a_citation_is_a_finding(consolidated_workspace):
    # D52: a consolidation recorded normative_change: false keeps every citation.
    # Only the consolidation's own tag moves; `_retag_draft_with` would move
    # revision 01 too and hide the drop.
    ws = consolidated_workspace
    draft_file = ws["repo"] / "draft-test-spec.md"
    draft_file.write_text(
        draft_file.read_text().replace("`ai_rfc:spec:2.1`", "nothing")
    )
    git(ws["repo"], "add", "draft-test-spec.md")
    git(ws["repo"], "commit", "-m", "drop a citation")
    git(ws["repo"], "tag", "-f", "draft-test-spec-02")
    findings = run_gate(
        ws["repo"],
        ws["timeline"],
        ws["checkpoints"],
        ws["questions"],
        ws["revisions"],
        consolidations_dir=ws["consolidations"],
    )
    assert any("drops" in f and "spec:2.1" in f for f in findings)


def test_a_consolidation_whose_requirements_moved_is_a_finding(
    structured_workspace, tmp_path
):
    # Check 8. The consolidation is written legitimately from the FIRST
    # cluster's checkpoint (its requirements equal that base, so the writer
    # accepts it), but the revision it follows is the second cluster's, whose
    # requirements differ. Nothing is tampered, so verify_checkpoint stays quiet
    # and the gate reaches check 8.
    from ai_rfc.draft.checkpoint import write_consolidation_checkpoint

    ws = structured_workspace
    consolidations = tmp_path / "consolidations"
    first = ws["first_checkpoint"]
    write_consolidation_checkpoint(
        first / "manifest.yaml", 1, first, ws["first_cluster"], consolidations
    )
    ws["consolidations"] = consolidations
    _record_consolidation(ws, ordinal=2, checkpoint="consolidations/01")
    findings = run_gate(
        ws["repo"],
        ws["timeline"],
        ws["checkpoints"],
        ws["questions"],
        ws["revisions"],
        consolidations_dir=consolidations,
    )
    assert any("requirements differ" in f for f in findings)


def test_the_first_revision_may_not_be_a_consolidation(consolidated_workspace):
    # The fixture writes no `kind:` on the first entry (it defaults to cluster),
    # so make it one by appending the two keys to that entry's block.
    ws = consolidated_workspace
    ws["revisions"].write_text(
        ws["revisions"]
        .read_text()
        .replace(
            "    note: 'initial reconstruction'\n",
            "    note: 'initial reconstruction'\n"
            "    kind: consolidation\n"
            "    checkpoint: consolidations/01\n",
        )
    )
    findings = run_gate(
        ws["repo"],
        ws["timeline"],
        ws["checkpoints"],
        ws["questions"],
        ws["revisions"],
        consolidations_dir=ws["consolidations"],
    )
    assert any("cannot be a consolidation" in f for f in findings)


def test_a_consolidation_that_adds_a_citation_gates_clean(
    structured_workspace, tmp_path
):
    # D52 read the other way round, and the only test that pins the exemption
    # as a superset: the previous revision does not cite spec:2.1 and the
    # consolidation does, so an equality comparison would report it.
    from ai_rfc.draft.checkpoint import write_consolidation_checkpoint

    ws = structured_workspace
    _retag_draft_with(
        ws,
        lambda text: text.replace(
            "It also does this. `ai_rfc:spec:2.1`", "It also does this."
        ),
    )
    consolidations = tmp_path / "consolidations"
    base = ws["last_checkpoint"]
    write_consolidation_checkpoint(
        base / "manifest.yaml", 1, base, ws["last_cluster"], consolidations
    )
    ws["consolidations"] = consolidations
    draft_file = ws["repo"] / "draft-test-spec.md"
    draft_file.write_text(
        draft_file.read_text().replace(
            "It also does this.", "It also does this. `ai_rfc:spec:2.1`"
        )
    )
    git(ws["repo"], "add", "draft-test-spec.md")
    git(ws["repo"], "commit", "-m", "the consolidation cites the second claim")
    _record_consolidation(ws, ordinal=2, checkpoint="consolidations/01")
    assert (
        run_gate(
            ws["repo"],
            ws["timeline"],
            ws["checkpoints"],
            ws["questions"],
            ws["revisions"],
            consolidations_dir=consolidations,
        )
        == ()
    )


def test_a_consolidation_naming_another_cluster_is_a_finding(consolidated_workspace):
    # Nothing else in the gate reads a consolidation entry's own cluster_id —
    # the checkpoint resolves by `checkpoint:` and the ordinal pass skips it —
    # so without this check the entry may name any cluster in the timeline.
    ws = consolidated_workspace
    _patch_revisions(ws, "draft-test-spec-02", cluster_id=ws["first_cluster"])
    findings = run_gate(
        ws["repo"],
        ws["timeline"],
        ws["checkpoints"],
        ws["questions"],
        ws["revisions"],
        consolidations_dir=ws["consolidations"],
    )
    assert any(
        "a consolidation entry names cluster" in f and ws["first_cluster"] in f
        for f in findings
    )


def test_a_missing_consolidation_checkpoint_names_the_consolidations_root(
    consolidated_workspace,
):
    # The cluster-round wording names `checkpoints_dir` and the entry's cluster,
    # neither of which is where a consolidation's checkpoint belongs.
    ws = consolidated_workspace
    directory = ws["consolidations"] / "01"
    for path in sorted(directory.iterdir()):
        path.unlink()
    directory.rmdir()
    findings = run_gate(
        ws["repo"],
        ws["timeline"],
        ws["checkpoints"],
        ws["questions"],
        ws["revisions"],
        consolidations_dir=ws["consolidations"],
    )
    assert f"draft-test-spec-02: no checkpoint at {directory}" in findings
