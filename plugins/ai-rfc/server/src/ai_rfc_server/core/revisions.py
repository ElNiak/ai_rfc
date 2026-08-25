"""Revision-map operations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ..paths import Context
from . import CoreError
from .claims import _atomic_write


def record_revision(
    ctx: Context,
    tag: str,
    cluster_id: str,
    normative_change: bool,
    note: str,
) -> dict[str, Any]:
    """Record one revision entry, pinned to its on-disk checkpoint.

    Args:
        ctx: The resolved context.
        tag: The revision tag (``draft-<name>-NN``).
        cluster_id: The cluster this revision reflects.
        normative_change: Whether the revision changes normative behaviour;
            an explicit ``False`` is the auditable no-change marker.
        note: One-line rationale.

    Returns:
        The entry as recorded (including the checkpoint sha read from disk).

    Raises:
        CoreError: If the checkpoint is missing or the tag already exists.
        GateError: If the resulting revision map does not validate.
    """
    checkpoint = ctx.workspace / "checkpoints" / cluster_id / "checkpoint.json"
    if not checkpoint.exists():
        raise CoreError(
            f"no checkpoint for {cluster_id}; write the checkpoint before "
            f"recording the revision that pins it"
        )
    sha = json.loads(checkpoint.read_text())["manifest_sha256"]

    document = yaml.safe_load(ctx.revisions.read_text())
    if not isinstance(document, dict) or "revisions" not in document:
        raise CoreError(f"{ctx.revisions} is not a revision map")
    if tag in document["revisions"]:
        raise CoreError(f"revision {tag} is already recorded")
    entry = {
        "cluster_id": cluster_id,
        "checkpoint_manifest_sha256": sha,
        "normative_change": normative_change,
        "note": note,
    }
    document["revisions"][tag] = entry

    from panther.plugins.services.testers.a_rfc.draft.gate import load_revisions

    with tempfile.TemporaryDirectory() as scratch:
        candidate = Path(scratch) / "revisions.yaml"
        candidate.write_text(yaml.safe_dump(document, sort_keys=True))
        load_revisions(candidate)
        _atomic_write(ctx.revisions, candidate.read_text())
    return {"tag": tag, **entry}
