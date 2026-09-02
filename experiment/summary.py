"""What one cluster produced, read back off the artifacts it left.

Built from inside the per-cluster loop, which may already be hours into a
sweep, so every section here is isolated: a section that cannot be computed
leaves its field null and names itself in ``errors`` rather than raising. The
one thing this module must never do is end a run.

It reads the model's own account — the agent-authored ``note`` in
``revisions.yaml`` — which is why it does not live in :mod:`metrics`, whose
contract is to recompute every outcome and trust nothing the model said.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .config import Campaign
from .metrics import _extend_sys_path

REVISIONS_FILE = "revisions.yaml"
SUMMARIES_DIR = "summaries"
#: git's empty tree, the base ``views.emit`` diffs the first span against.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _draft(workspace: Path) -> Path:
    """The nested prose-draft repository."""
    return workspace / "draft"


def revision_of(workspace: Path, cluster_id: str) -> dict[str, Any] | None:
    """The revision entry a cluster produced, or None when it made none.

    Joined by ``cluster_id``, never by ``checkpoint_manifest_sha256``: two
    revisions share that digest whenever the manifest did not change between
    them, so it does not identify a revision.

    Args:
        workspace: The run's workspace.
        cluster_id: The cluster whose revision is wanted.

    Returns:
        The entry with its ``tag`` added, or None.
    """
    try:
        document = yaml.safe_load((workspace / REVISIONS_FILE).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    for tag, entry in (document.get("revisions") or {}).items():
        if isinstance(entry, dict) and entry.get("cluster_id") == cluster_id:
            return {**entry, "tag": str(tag)}
    return None


def _previous_tag(tag: str) -> str | None:
    """The tag one revision earlier, or None at the first revision.

    Tags are a revision sequence (``draft-<slug>-NN``), not cluster ordinals,
    so the citation delta steps back by number rather than by cluster.
    """
    stem, _, number = tag.rpartition("-")
    if not number.isdigit():
        return None
    previous = int(number) - 1
    return f"{stem}-{previous:02d}" if previous >= 1 else None


def citation_delta(workspace: Path, tag: str) -> dict[str, Any]:
    """Which claim ids the prose began and stopped citing at ``tag``.

    Reads the substrate's citation extractor, so the caller must already have
    put the substrate on the import path — :func:`build_summary` does this once
    before calling any section.

    Args:
        workspace: The run's workspace.
        tag: The revision tag this cluster produced.

    Returns:
        ``{tag, prev_tag, added, removed, error}``; ``error`` names why the
        comparison is incomplete, and is None when it is not.
    """
    from panther.plugins.services.testers.ai_rfc.draft.gate import cited_ids

    previous = _previous_tag(tag)
    now, finding = cited_ids(_draft(workspace), tag)
    before: set[str] = set()
    if previous is not None and finding is None:
        before, earlier = cited_ids(_draft(workspace), previous)
        finding = finding or earlier
    return {
        "tag": tag,
        "prev_tag": previous,
        "added": sorted(now - before),
        "removed": sorted(before - now),
        "error": finding,
    }


def diffstat(workspace: Path, tag: str) -> dict[str, int] | None:
    """How much prose the cluster changed, between ``tag`` and the one before.

    Args:
        workspace: The run's workspace.
        tag: The revision tag this cluster produced.

    Returns:
        ``{files, insertions, deletions}``, or None when the diff is
        unavailable. At the first revision the base is the tag's own parent —
        the scaffold commit the draft was seeded from — falling back to the
        empty tree when that revision is itself the root commit.
    """
    previous = _previous_tag(tag)
    bases = [previous] if previous is not None else [f"{tag}^", EMPTY_TREE]
    for base in bases:
        result = subprocess.run(
            ["git", "-C", str(_draft(workspace)), "diff", "--numstat", base, tag],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            break
    else:
        return None
    if result.returncode != 0:
        return None
    files = insertions = deletions = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        files += 1
        insertions += int(parts[0])
        deletions += int(parts[1])
    return {"files": files, "insertions": insertions, "deletions": deletions}


def held_claim_ids(
    campaign: Campaign, workspace: Path, cluster_id: str
) -> tuple[frozenset[str], str | None]:
    """The claim ids a cluster's checkpoint holds.

    Args:
        campaign: The frozen campaign, for the substrate import path.
        workspace: The run's workspace.
        cluster_id: The cluster whose checkpoint is wanted.

    Returns:
        ``(ids, error)``; an empty set and a reason when unreadable.
    """
    _extend_sys_path(campaign)
    from panther.plugins.services.testers.ai_rfc.draft.completeness import claim_ids_of

    try:
        return claim_ids_of(workspace / "checkpoints" / cluster_id), None
    except Exception as error:  # noqa: BLE001 - a display line may not end a run
        return frozenset(), f"held_claim_ids: {error}"


def seed_seen(campaign: Campaign, workspace: Path) -> tuple[frozenset[str], str | None]:
    """Every claim id already held when the loop starts.

    Rebuilt on resume so ``new`` keeps meaning "first held at this cluster"
    across an interrupted run. Pre-seeded baseline checkpoints are included,
    which is correct: a claim a baseline already held was not introduced here.

    Args:
        campaign: The frozen campaign, for the substrate import path.
        workspace: The run's workspace.

    Returns:
        ``(ids, error)``; an empty set and a reason when the rebuild failed.
        ``checkpoint_records`` refuses the whole root if one record is damaged,
        so this degrades rather than propagating.
    """
    _extend_sys_path(campaign)
    from panther.plugins.services.testers.ai_rfc.draft.completeness import (
        checkpoint_records,
        claim_ids_of,
    )

    root = workspace / "checkpoints"
    try:
        held: frozenset[str] = frozenset()
        for name, _record in checkpoint_records(root):
            held = held | claim_ids_of(root / name)
        return held, None
    except Exception as error:  # noqa: BLE001 - a display line may not end a run
        return frozenset(), f"seed_seen: {error}"


def write_summary(run_dir: Path, cluster_id: str, record: dict[str, Any]) -> Path:
    """Write one record, replacing any previous one atomically.

    A kill mid-write must not leave half a JSON behind, so the record lands on
    a temporary name first.

    Args:
        run_dir: The run directory.
        cluster_id: Names the file.
        record: The record to write.

    Returns:
        The path written.
    """
    directory = run_dir / SUMMARIES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{cluster_id}.json"
    temporary = directory / f"{cluster_id}.json.tmp"
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return path
