"""Freeze a manifest against one timeline cluster.

A checkpoint is the manifest as it stood when cluster ``i`` was processed:
a normalized byte-stable copy plus a record tying it to the cluster, the
timeline it came from, and an adjudication summary. Checkpoints are written
once and never overwritten — a re-run against the same cluster is a new
decision, not an update.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from ai_rfc.draft.structures import STRUCTURES_FILE, render_all

from ..models import STATUS_RANK, Manifest, Status
from ..promotion import adjudicate, violations
from ..schema import dump, load

CHECKPOINT_FILE = "checkpoint.json"
MANIFEST_FILE = "manifest.yaml"


class CheckpointError(RuntimeError):
    """Raised when a checkpoint cannot be written or no longer holds."""


def _digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def requirements_digest(manifest: Manifest) -> str:
    """Digest a manifest's requirements alone, ignoring its structures.

    D48 lets a consolidation change only ``structures:``. Existing checkpoints
    record no such digest, so it is computed from stored bytes here and reused
    by the gate — one function, so the writer and the checker cannot drift.

    Args:
        manifest: The manifest to digest.

    Returns:
        The hex sha256 of the manifest dumped without its structures.
    """
    return _digest_bytes(dump(replace(manifest, structures=())).encode())


def _cluster_row(timeline_dir: Path, cluster_id: str) -> tuple[dict, str | None]:
    rows = [
        json.loads(line)
        for line in (timeline_dir / "clusters.jsonl").read_text().splitlines()
    ]
    by_id = {row["id"]: row for row in rows}
    if cluster_id not in by_id:
        raise CheckpointError(f"no cluster {cluster_id} in {timeline_dir}")
    row = by_id[cluster_id]
    previous = [r["id"] for r in rows if r["ordinal"] == row["ordinal"] - 1]
    return row, previous[0] if previous else None


def write_checkpoint(
    manifest_path: Path, timeline_dir: Path, cluster_id: str, out: Path
) -> Path:
    """Checkpoint ``manifest_path`` against one cluster of a timeline.

    Args:
        manifest_path: The manifest to freeze; loaded and re-dumped so the
            stored copy is normalized, byte-stable and citable.
        timeline_dir: Directory written by the timeline stage.
        cluster_id: The cluster this manifest state belongs to.
        out: Root directory; the checkpoint lands in ``out/<cluster_id>/``.

    Returns:
        The checkpoint directory.

    Raises:
        CheckpointError: If the cluster is unknown or the checkpoint exists.
        SchemaError: If the manifest cannot be loaded as written.
        OSError: If an input cannot be read.
    """
    manifest = load(manifest_path)
    row, prev_cluster_id = _cluster_row(timeline_dir, cluster_id)

    # Every input is read before anything is created. A failure after `mkdir`
    # leaves a directory the write-once guard below then refuses forever, and
    # `pipeline status` reads it as unfrozen and routes the operator straight
    # back into the stage that will refuse them.
    timeline_sha256 = _digest_bytes((timeline_dir / "timeline.json").read_bytes())
    normalized = dump(manifest).encode()
    structures_text = render_all(manifest) if manifest.structures else ""
    structures_sha256 = (
        _digest_bytes(structures_text.encode()) if structures_text else None
    )

    checkpoint_dir = out / cluster_id
    if checkpoint_dir.exists():
        raise CheckpointError(
            f"{checkpoint_dir} already exists; a checkpoint is written once "
            f"and never overwritten"
        )
    checkpoint_dir.mkdir(parents=True)

    (checkpoint_dir / MANIFEST_FILE).write_bytes(normalized)
    if structures_text:
        (checkpoint_dir / STRUCTURES_FILE).write_bytes(structures_text.encode())

    supported_counts = {status.value: 0 for status in Status}
    promotable = 0
    for claim in manifest.claims:
        supported = adjudicate(claim)
        supported_counts[supported.value] += 1
        if STATUS_RANK[supported] > STATUS_RANK[claim.status]:
            promotable += 1

    record = {
        "adjudication": {
            "count_by_stored": manifest.count_by_status,
            "count_by_supported": supported_counts,
            "promotable_count": promotable,
            "violation_count": len(violations(manifest)),
        },
        "cluster_id": cluster_id,
        "manifest_sha256": _digest_bytes(normalized),
        "ordinal": row["ordinal"],
        "prev_cluster_id": prev_cluster_id,
        "timeline_sha256": timeline_sha256,
    }
    if structures_sha256 is not None:
        record["structures_sha256"] = structures_sha256
    (checkpoint_dir / CHECKPOINT_FILE).write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n"
    )
    return checkpoint_dir


def verify_checkpoint(checkpoint_dir: Path) -> str | None:
    """Check that a checkpoint's stored files still match their digests.

    Args:
        checkpoint_dir: A directory written by :func:`write_checkpoint` or
            :func:`write_consolidation_checkpoint`.

    Returns:
        None when every stored copy still matches; otherwise the reason one
        of them does not.

    Raises:
        OSError: If the checkpoint record cannot be read.
    """
    record = json.loads((checkpoint_dir / CHECKPOINT_FILE).read_text())
    stored = checkpoint_dir / MANIFEST_FILE
    if not stored.exists():
        return f"{stored} is missing"
    current = _digest_bytes(stored.read_bytes())
    if current != record["manifest_sha256"]:
        return (
            f"{checkpoint_dir.name}/manifest.yaml has been edited since the "
            f"checkpoint was written; a checkpoint is immutable"
        )

    expected = record.get("structures_sha256")
    frozen = checkpoint_dir / STRUCTURES_FILE
    if expected is None:
        if frozen.exists():
            return f"{checkpoint_dir.name}: {STRUCTURES_FILE} is present but unrecorded"
    else:
        if not frozen.is_file():
            return f"{checkpoint_dir.name}: {STRUCTURES_FILE} is recorded but missing"
        if _digest_bytes(frozen.read_bytes()) != expected:
            return (
                f"{checkpoint_dir.name}: {STRUCTURES_FILE} does not match the "
                f"digest recorded in {CHECKPOINT_FILE}"
            )
    return None


def write_consolidation_checkpoint(
    manifest_path: Path,
    ordinal: int,
    base_checkpoint: Path,
    cluster_id: str,
    out: Path,
) -> Path:
    """Freeze a consolidation's manifest under its own root.

    A consolidation belongs to no new cluster, so it cannot reuse
    :func:`write_checkpoint`, which resolves ``out / cluster_id`` and requires
    the id to appear in the timeline. It lands at ``out/<NN>`` instead, leaving
    the cluster checkpoint root to cluster rounds alone (D48).

    Args:
        manifest_path: The consolidation's manifest.
        ordinal: The consolidation's number; the directory is two digits.
        base_checkpoint: The cluster checkpoint this consolidation follows.
        cluster_id: The cluster the base checkpoint belongs to.
        out: The consolidations root.

    Returns:
        The directory written.

    Raises:
        CheckpointError: If the base is unreadable, or the manifest changes any
            requirement rather than only its structures.
    """
    manifest = load(manifest_path)
    base_manifest_path = base_checkpoint / MANIFEST_FILE
    if not base_manifest_path.is_file():
        raise CheckpointError(
            f"{base_checkpoint}: no {MANIFEST_FILE} to consolidate from"
        )
    base = load(base_manifest_path)
    if requirements_digest(manifest) != requirements_digest(base):
        raise CheckpointError(
            f"consolidation {ordinal:02d}: requirements differ from "
            f"{base_checkpoint.name}; a consolidation may change only structures"
        )
    manifest_text = dump(manifest)
    structures_text = render_all(manifest) if manifest.structures else ""
    record = {
        "base_checkpoint": base_checkpoint.name,
        "cluster_id": cluster_id,
        "kind": "consolidation",
        "manifest_sha256": _digest_bytes(manifest_text.encode()),
        "ordinal": ordinal,
    }
    if structures_text:
        record["structures_sha256"] = _digest_bytes(structures_text.encode())

    checkpoint_dir = out / f"{ordinal:02d}"
    if checkpoint_dir.exists():
        raise CheckpointError(
            f"{checkpoint_dir}: already written; a checkpoint is immutable"
        )
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / MANIFEST_FILE).write_bytes(manifest_text.encode())
    if structures_text:
        (checkpoint_dir / STRUCTURES_FILE).write_bytes(structures_text.encode())
    (checkpoint_dir / CHECKPOINT_FILE).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    return checkpoint_dir
