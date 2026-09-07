"""Revision-map operations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ai_rfc.draft.gate import GateError, load_revisions

from ..paths import Context
from . import CoreError
from .claims import _atomic_write


def record_revision(
    ctx: Context,
    tag: str,
    cluster_id: str,
    normative_change: bool,
    note: str,
    kind: str = "cluster",
    checkpoint: str | None = None,
) -> dict[str, Any]:
    """Record one revision entry, pinned to its on-disk checkpoint.

    Args:
        ctx: The resolved context.
        tag: The revision tag (``draft-<name>-NN``).
        cluster_id: The cluster this revision reflects.
        normative_change: Whether the revision changes normative behaviour;
            an explicit ``False`` is the auditable no-change marker.
        note: One-line rationale.
        kind: ``cluster`` for a cluster round, ``consolidation`` for a
            consolidation of one.
        checkpoint: The consolidation's own checkpoint, workspace-relative
            (``consolidations/<NN>``); required when ``kind`` is
            ``consolidation`` and refused otherwise by the substrate loader.

    Returns:
        The entry as recorded (including the checkpoint sha read from disk).

    Raises:
        CoreError: If the checkpoint is missing, the tag already exists, a
            consolidation names no checkpoint or one outside the workspace, the
            cluster already has a cluster revision, or the resulting revision
            map does not validate.
        GateError: If the revision map already on disk does not validate, which
            is a defect in the file rather than in what was asked for.
    """
    if kind == "consolidation":
        if not checkpoint:
            raise CoreError(f"{tag}: a consolidation must name its checkpoint")
        named = Path(checkpoint)
        # Checked before the path is joined or reported: the refusal below
        # renders it relative to the workspace, which raises on a path that
        # escapes it, and the guardrail's failure path must not itself fail.
        if named.is_absolute() or ".." in named.parts:
            raise CoreError(
                "checkpoint must be a workspace-relative path such as "
                "consolidations/NN"
            )
        pinned = ctx.workspace / checkpoint / "checkpoint.json"
    else:
        pinned = ctx.workspace / "checkpoints" / cluster_id / "checkpoint.json"
    if not pinned.exists():
        raise CoreError(
            f"no checkpoint at {pinned.parent.relative_to(ctx.workspace)}; write "
            f"the checkpoint before recording the revision that pins it"
        )
    sha = json.loads(pinned.read_text())["manifest_sha256"]

    document = yaml.safe_load(ctx.revisions.read_text())
    if not isinstance(document, dict) or "revisions" not in document:
        raise CoreError(f"{ctx.revisions} is not a revision map")
    if tag in document["revisions"]:
        raise CoreError(f"revision {tag} is already recorded")
    if kind == "cluster":
        for existing in load_revisions(ctx.revisions):
            if existing.kind == "cluster" and existing.cluster_id == cluster_id:
                raise CoreError(
                    f"{cluster_id} already has a cluster revision ({existing.tag}); "
                    f"a consolidation needs kind='consolidation'"
                )
    entry: dict[str, Any] = {
        "cluster_id": cluster_id,
        "checkpoint_manifest_sha256": sha,
        "normative_change": normative_change,
        "note": note,
        "kind": kind,
    }
    if checkpoint is not None:
        entry["checkpoint"] = checkpoint
    document["revisions"][tag] = entry

    with tempfile.TemporaryDirectory() as scratch:
        candidate = Path(scratch) / "revisions.yaml"
        candidate.write_text(yaml.safe_dump(document, sort_keys=True))
        try:
            load_revisions(candidate)
        except GateError as error:
            raise CoreError(str(error)) from error
        _atomic_write(ctx.revisions, candidate.read_text())
    return {"tag": tag, **entry}
