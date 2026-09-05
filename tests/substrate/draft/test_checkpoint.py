import hashlib
import json
from pathlib import Path

import pytest

from ai_rfc.draft.checkpoint import (
    CheckpointError,
    verify_checkpoint,
    write_checkpoint,
    write_consolidation_checkpoint,
)
from ai_rfc.timeline.store import read_clusters

from .conftest import _manifest_text

pytestmark = pytest.mark.unit


def _pr_cluster_id(timeline_dir: Path) -> str:
    return [
        cluster["id"]
        for cluster in read_clusters(timeline_dir)
        if cluster["kind"] == "pr"
    ][0]


def test_checkpoint_records_ordinal_prev_and_digests(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path
):
    out = tmp_path / "checkpoints"
    cluster_id = _pr_cluster_id(timeline_dir)
    checkpoint_dir = write_checkpoint(manifest_path, timeline_dir, cluster_id, out)
    assert checkpoint_dir == out / cluster_id
    record = json.loads((checkpoint_dir / "checkpoint.json").read_text())
    assert record["cluster_id"] == cluster_id
    assert record["ordinal"] == 2
    assert record["prev_cluster_id"].startswith("c0001-epoch-")
    stored = (checkpoint_dir / "manifest.yaml").read_bytes()
    assert record["manifest_sha256"] == hashlib.sha256(stored).hexdigest()
    timeline_bytes = (timeline_dir / "timeline.json").read_bytes()
    assert record["timeline_sha256"] == hashlib.sha256(timeline_bytes).hexdigest()


def test_checkpoint_adjudication_summary(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path
):
    checkpoint_dir = write_checkpoint(
        manifest_path, timeline_dir, _pr_cluster_id(timeline_dir), tmp_path / "c"
    )
    record = json.loads((checkpoint_dir / "checkpoint.json").read_text())
    adjudication = record["adjudication"]
    assert adjudication["count_by_stored"] == {"gap": 2, "inferred": 0, "confirmed": 0}
    assert adjudication["count_by_supported"] == {
        "gap": 1,
        "inferred": 0,
        "confirmed": 1,
    }
    assert adjudication["promotable_count"] == 1
    assert adjudication["violation_count"] == 0


def test_unknown_cluster_is_refused(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path
):
    with pytest.raises(CheckpointError) as excinfo:
        write_checkpoint(
            manifest_path, timeline_dir, "c9999-pr-000000000000", tmp_path / "c"
        )
    assert "c9999" in str(excinfo.value)


def test_existing_checkpoint_is_never_overwritten(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path
):
    cluster_id = _pr_cluster_id(timeline_dir)
    write_checkpoint(manifest_path, timeline_dir, cluster_id, tmp_path / "c")
    with pytest.raises(CheckpointError):
        write_checkpoint(manifest_path, timeline_dir, cluster_id, tmp_path / "c")


def test_verify_detects_a_stale_manifest_copy(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path
):
    checkpoint_dir = write_checkpoint(
        manifest_path, timeline_dir, _pr_cluster_id(timeline_dir), tmp_path / "c"
    )
    assert verify_checkpoint(checkpoint_dir) is None
    stored = checkpoint_dir / "manifest.yaml"
    stored.write_bytes(stored.read_bytes() + b"# drift\n")
    reason = verify_checkpoint(checkpoint_dir)
    assert reason is not None
    assert "manifest.yaml" in reason


def test_two_checkpoints_of_same_manifest_are_byte_identical(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path
):
    cluster_id = _pr_cluster_id(timeline_dir)
    first = write_checkpoint(manifest_path, timeline_dir, cluster_id, tmp_path / "one")
    second = write_checkpoint(manifest_path, timeline_dir, cluster_id, tmp_path / "two")
    for name in ("manifest.yaml", "checkpoint.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_an_unreadable_timeline_leaves_no_checkpoint_behind(
    timeline_dir, manifest_path, tmp_path
):
    """A half-written checkpoint is worse than none.

    The write-once guard refuses the retry forever and `pipeline status` reads
    the bare directory as unfrozen, so the operator is routed back into the
    stage that will refuse them, with no documented recovery.
    """
    out = tmp_path / "fresh-checkpoints"
    cluster_id = json.loads(
        (timeline_dir / "clusters.jsonl").read_text().splitlines()[0]
    )["id"]
    (timeline_dir / "timeline.json").unlink()

    with pytest.raises(OSError):
        write_checkpoint(manifest_path, timeline_dir, cluster_id, out)

    assert not (out / cluster_id).exists()


def test_empty_manifest_checkpoints_with_zero_counts(
    timeline_dir: Path, tmp_path: Path
):
    import yaml

    manifest = tmp_path / "empty.yaml"
    manifest.write_text("rfc: SPEC-0\ntitle: 'Nothing yet'\nrequirements: {}\n")
    checkpoint_dir = write_checkpoint(
        manifest, timeline_dir, _pr_cluster_id(timeline_dir), tmp_path / "c"
    )
    record = json.loads((checkpoint_dir / "checkpoint.json").read_text())
    zero = {"gap": 0, "inferred": 0, "confirmed": 0}
    assert record["adjudication"] == {
        "count_by_stored": zero,
        "count_by_supported": zero,
        "promotable_count": 0,
        "violation_count": 0,
    }
    stored = yaml.safe_load((checkpoint_dir / "manifest.yaml").read_text())
    assert stored == {"rfc": "SPEC-0", "title": "Nothing yet", "requirements": {}}
    assert verify_checkpoint(checkpoint_dir) is None


STRUCTURED = (
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


def _structured_manifest(tmp_path):
    path = tmp_path / "structured.yaml"
    path.write_text(_manifest_text(with_second_claim=True) + STRUCTURED)
    return path


def test_a_structured_checkpoint_freezes_the_rendering_and_its_digest(
    tmp_path, timeline_dir
):
    from ai_rfc.draft.structures import STRUCTURES_FILE

    out = tmp_path / "checkpoints"
    directory = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, _pr_cluster_id(timeline_dir), out
    )
    frozen = (directory / STRUCTURES_FILE).read_bytes()
    assert b"ai_rfc:struct:header begin" in frozen
    record = json.loads((directory / "checkpoint.json").read_text())
    assert record["structures_sha256"] == hashlib.sha256(frozen).hexdigest()


def test_a_structure_free_checkpoint_writes_no_structures_file(
    tmp_path, timeline_dir, manifest_path
):
    from ai_rfc.draft.structures import STRUCTURES_FILE

    directory = write_checkpoint(
        manifest_path, timeline_dir, _pr_cluster_id(timeline_dir), tmp_path / "cp"
    )
    assert not (directory / STRUCTURES_FILE).exists()
    assert "structures_sha256" not in json.loads(
        (directory / "checkpoint.json").read_text()
    )


def test_a_tampered_structures_file_is_caught(tmp_path, timeline_dir):
    from ai_rfc.draft.structures import STRUCTURES_FILE

    directory = write_checkpoint(
        _structured_manifest(tmp_path),
        timeline_dir,
        _pr_cluster_id(timeline_dir),
        tmp_path / "cp",
    )
    assert verify_checkpoint(directory) is None
    target = directory / STRUCTURES_FILE
    target.write_bytes(target.read_bytes().replace(b"uint8", b"uint9"))
    problem = verify_checkpoint(directory)
    assert problem is not None and STRUCTURES_FILE in problem


def test_a_consolidation_checkpoint_lands_under_its_own_root(tmp_path, timeline_dir):
    cluster_id = _pr_cluster_id(timeline_dir)
    out = tmp_path / "checkpoints"
    base = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, cluster_id, out
    )
    consolidations = tmp_path / "consolidations"
    directory = write_consolidation_checkpoint(
        _structured_manifest(tmp_path), 1, base, cluster_id, consolidations
    )
    assert directory == consolidations / "01"
    record = json.loads((directory / "checkpoint.json").read_text())
    assert record["kind"] == "consolidation"
    assert record["cluster_id"] == cluster_id
    assert record["base_checkpoint"] == cluster_id
    # The cluster root keeps enumerating cluster checkpoints only.
    assert sorted(p.name for p in out.iterdir()) == [cluster_id]


def test_a_consolidation_that_changes_a_requirement_is_refused(tmp_path, timeline_dir):
    cluster_id = _pr_cluster_id(timeline_dir)
    out = tmp_path / "checkpoints"
    base = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, cluster_id, out
    )
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        _manifest_text(with_second_claim=True).replace("level: MUST", "level: MAY")
        + STRUCTURED
    )
    with pytest.raises(CheckpointError) as error:
        write_consolidation_checkpoint(
            changed, 1, base, cluster_id, tmp_path / "consolidations"
        )
    assert "requirements" in str(error.value)


def test_a_consolidation_checkpoint_verifies_like_any_other(tmp_path, timeline_dir):
    cluster_id = _pr_cluster_id(timeline_dir)
    out = tmp_path / "checkpoints"
    base = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, cluster_id, out
    )
    directory = write_consolidation_checkpoint(
        _structured_manifest(tmp_path), 7, base, cluster_id, tmp_path / "consolidations"
    )
    assert directory.name == "07"
    assert verify_checkpoint(directory) is None


def test_a_consolidation_checkpoint_is_write_once(tmp_path, timeline_dir):
    cluster_id = _pr_cluster_id(timeline_dir)
    out = tmp_path / "checkpoints"
    base = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, cluster_id, out
    )
    consolidations = tmp_path / "consolidations"
    write_consolidation_checkpoint(
        _structured_manifest(tmp_path), 1, base, cluster_id, consolidations
    )
    with pytest.raises(CheckpointError) as error:
        write_consolidation_checkpoint(
            _structured_manifest(tmp_path), 1, base, cluster_id, consolidations
        )
    assert "immutable" in str(error.value)


def test_a_failed_consolidation_leaves_no_directory(tmp_path, timeline_dir):
    consolidations = tmp_path / "consolidations"
    missing = tmp_path / "nope.yaml"
    with pytest.raises((CheckpointError, OSError)):
        write_consolidation_checkpoint(
            missing,
            1,
            tmp_path / "absent",
            _pr_cluster_id(timeline_dir),
            consolidations,
        )
    assert not (consolidations / "01").exists()


def test_a_consolidation_may_change_the_structures(tmp_path, timeline_dir):
    """The digest a consolidation is gated on covers requirements alone.

    Without that, a consolidation whose only change is a structure — the one
    edit D48 permits — is refused, and the gate rejects every legitimate use.
    """
    cluster_id = _pr_cluster_id(timeline_dir)
    out = tmp_path / "checkpoints"
    base = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, cluster_id, out
    )
    widened = tmp_path / "widened.yaml"
    widened.write_text(
        _manifest_text(with_second_claim=True) + STRUCTURED.replace("uint8", "uint16")
    )

    directory = write_consolidation_checkpoint(
        widened, 1, base, cluster_id, tmp_path / "consolidations"
    )

    consolidated = json.loads((directory / "checkpoint.json").read_text())
    frozen = json.loads((base / "checkpoint.json").read_text())
    assert consolidated["structures_sha256"] != frozen["structures_sha256"]
    assert consolidated["manifest_sha256"] != frozen["manifest_sha256"]
    assert verify_checkpoint(directory) is None


def test_an_unrecorded_structures_file_is_caught(tmp_path, timeline_dir, manifest_path):
    from ai_rfc.draft.structures import STRUCTURES_FILE

    directory = write_checkpoint(
        manifest_path, timeline_dir, _pr_cluster_id(timeline_dir), tmp_path / "cp"
    )
    (directory / STRUCTURES_FILE).write_text("smuggled in after the freeze\n")
    problem = verify_checkpoint(directory)
    assert problem is not None and "present but unrecorded" in problem


def test_a_deleted_structures_file_is_caught(tmp_path, timeline_dir):
    from ai_rfc.draft.structures import STRUCTURES_FILE

    directory = write_checkpoint(
        _structured_manifest(tmp_path),
        timeline_dir,
        _pr_cluster_id(timeline_dir),
        tmp_path / "cp",
    )
    (directory / STRUCTURES_FILE).unlink()
    problem = verify_checkpoint(directory)
    assert problem is not None and "recorded but missing" in problem


def test_a_failed_rendering_leaves_no_checkpoint_behind(
    tmp_path, timeline_dir, monkeypatch
):
    """The rendering happens before the `mkdir`, like every other input.

    A directory created and then abandoned is refused forever by the write-once
    guard, and `pipeline status` reads it as unfrozen.
    """

    def refuse(manifest):
        raise RuntimeError("rendering refused")

    monkeypatch.setattr("ai_rfc.draft.checkpoint.render_all", refuse)
    out = tmp_path / "checkpoints"
    cluster_id = _pr_cluster_id(timeline_dir)

    with pytest.raises(RuntimeError):
        write_checkpoint(_structured_manifest(tmp_path), timeline_dir, cluster_id, out)

    assert not (out / cluster_id).exists()


@pytest.mark.parametrize("ordinal", [0, 100])
def test_an_ordinal_outside_two_digits_is_refused(tmp_path, timeline_dir, ordinal):
    """``consolidations/<NN>`` is a two-digit scheme.

    ``f"{100:02d}"`` widens to three digits silently, and the directory then
    sorts before ``99``.
    """
    cluster_id = _pr_cluster_id(timeline_dir)
    base = write_checkpoint(
        _structured_manifest(tmp_path), timeline_dir, cluster_id, tmp_path / "cp"
    )
    consolidations = tmp_path / "consolidations"

    with pytest.raises(CheckpointError) as error:
        write_consolidation_checkpoint(
            _structured_manifest(tmp_path), ordinal, base, cluster_id, consolidations
        )

    assert str(ordinal) in str(error.value)
    assert not consolidations.exists()
