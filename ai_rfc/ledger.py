"""One reader of per-cluster progress, for every surface that reports it.

Five places used to compute "which clusters are done" and reached three
different answers: a bare checkpoint directory counted for the MCP status, a
checkpoint plus its revision entry plus its tag for the harness, and checkpoint
records for completeness. A cluster is done here only when the checkpoint
record, the ``kind: cluster`` revision entry and the annotated tag all exist;
what is on disk decides, never a counter in memory.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CHECKPOINT_FILE = "checkpoint.json"
PRESEED_MARKER = "harness.json"
CLUSTERS_FILE = "timeline/clusters.jsonl"
INIT_RECORD = "init.json"
PRISTINE_RECORD = "pristine.json"


class LedgerError(RuntimeError):
    """Raised when the workspace's progress cannot be read as written."""


def partial_reason(
    checkpoint: bool, revision_tag: str | None, tag_exists: bool
) -> str | None:
    """Name a half-finished cluster's state, or None when untouched or done.

    The three strings live here so every surface that describes a half-finished
    cluster says the same thing about it.

    Args:
        checkpoint: Whether the checkpoint record exists.
        revision_tag: The tag of the cluster's revision entry, or None.
        tag_exists: Whether that tag exists in the draft repository.

    Returns:
        A short description of what is already on disk, or None.
    """
    if not checkpoint:
        return None
    if not revision_tag:
        return "checkpoint present, no revision entry"
    if not tag_exists:
        return "checkpoint present, revision entry recorded, tag missing"
    return None


@dataclass(frozen=True)
class ClusterState:
    """What the workspace holds for one cluster."""

    id: str
    ordinal: int
    kind: str | None
    in_window: bool
    pre_seeded: bool
    checkpoint: bool
    revision_tag: str | None
    normative_change: bool | None
    tag_exists: bool

    @property
    def done(self) -> bool:
        """Finished, by the strict definition, or not this run's work at all."""
        if not self.in_window or self.pre_seeded:
            return True
        return self.checkpoint and self.revision_tag is not None and self.tag_exists

    @property
    def partial_reason(self) -> str | None:
        """Name a half-finished cluster's state, or None when untouched or done."""
        if not self.in_window or self.pre_seeded:
            return None
        return partial_reason(self.checkpoint, self.revision_tag, self.tag_exists)


def _rows(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / CLUSTERS_FILE
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise LedgerError(f"{path}: {error}") from None
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as error:
            raise LedgerError(f"{path}: not JSON Lines: {error}") from None
    for row in rows:
        missing = {"id", "ordinal"} - set(row)
        if missing:
            raise LedgerError(f"{path}: a cluster row is missing {sorted(missing)}")
    return sorted(rows, key=lambda row: row["ordinal"])


def _entries(workspace: Path) -> dict[str, tuple[str, bool | None]]:
    """cluster id -> (tag, normative_change) of its first ``kind: cluster`` entry."""
    path = workspace / "revisions.yaml"
    if not path.exists():
        return {}
    try:
        document = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as error:
        raise LedgerError(f"{path}: {error}") from None
    found: dict[str, tuple[str, bool | None]] = {}
    for tag, body in (document.get("revisions") or {}).items():
        if not isinstance(body, dict) or "cluster_id" not in body:
            continue
        if body.get("kind", "cluster") != "cluster":
            continue
        cluster_id = str(body["cluster_id"])
        if cluster_id not in found:
            normative = body.get("normative_change")
            found[cluster_id] = (
                str(tag),
                None if normative is None else bool(normative),
            )
    return found


def _tags(draft: Path) -> set[str]:
    """Every tag in the draft repository, or none when there is no repository.

    A repository that exists but will not answer is an error rather than an
    empty set: swallowing it makes every cluster read ``tag_exists`` False, so
    the whole reconstruction reports as outstanding and ``next_cluster`` offers
    the same cluster forever with nothing saying why.

    Args:
        draft: The nested prose-draft repository.

    Returns:
        The tag names, empty when ``draft`` is not a git repository at all.

    Raises:
        LedgerError: If the repository exists but its tags cannot be listed.
    """
    if not (draft / ".git").exists():
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", str(draft), "tag", "-l"], capture_output=True, text=True
        )
    except OSError as error:
        raise LedgerError(f"{draft}: could not run git: {error}") from None
    if result.returncode:
        raise LedgerError(
            f"{draft}: git tag -l exited {result.returncode}: "
            f"{result.stderr.strip() or '(no stderr)'}"
        )
    return {tag for tag in result.stdout.splitlines() if tag}


def window_of(workspace: Path) -> tuple[int, int] | None:
    """The window ``init`` (production) or ``prepare`` (campaign) recorded, if any.

    Args:
        workspace: The workspace root.

    Returns:
        Inclusive ordinal bounds, or None when neither record names a window.

    Raises:
        LedgerError: If a record exists but cannot be read as JSON.
    """
    for name in (INIT_RECORD, PRISTINE_RECORD):
        path = workspace / name
        if path.exists():
            try:
                window = json.loads(path.read_text()).get("window")
            except (OSError, ValueError) as error:
                raise LedgerError(f"{path}: {error}") from None
            if window:
                return int(window[0]), int(window[1])
    return None


def clusters(
    workspace: Path, window: tuple[int, int] | None = None
) -> tuple[ClusterState, ...]:
    """Read every cluster's state off the workspace, in ordinal order.

    Args:
        workspace: The workspace root.
        window: Inclusive ordinal bounds of this reconstruction's work;
            defaults to what the workspace recorded, else every cluster.

    Returns:
        One state per timeline cluster.

    Raises:
        LedgerError: If the timeline, a record or the revision map is unreadable.
    """
    bounds = window or window_of(workspace)
    entries = _entries(workspace)
    tags = _tags(workspace / "draft")
    states = []
    for row in _rows(workspace):
        directory = workspace / "checkpoints" / row["id"]
        tag, normative = entries.get(row["id"], (None, None))
        ordinal = int(row["ordinal"])
        states.append(
            ClusterState(
                id=row["id"],
                ordinal=ordinal,
                kind=row.get("kind"),
                in_window=bounds is None or bounds[0] <= ordinal <= bounds[1],
                pre_seeded=(directory / PRESEED_MARKER).exists(),
                checkpoint=(directory / CHECKPOINT_FILE).is_file(),
                revision_tag=tag,
                normative_change=normative,
                tag_exists=tag in tags if tag else False,
            )
        )
    return tuple(states)


def next_cluster(
    workspace: Path, window: tuple[int, int] | None = None
) -> ClusterState | None:
    """The lowest-ordinal in-window cluster that is not done, or None.

    Args:
        workspace: The workspace root.
        window: Inclusive ordinal bounds; defaults to the recorded window.

    Returns:
        The cluster to work on next, or None when none is outstanding.

    Raises:
        LedgerError: If the workspace's progress cannot be read.
    """
    for state in clusters(workspace, window):
        if not state.done:
            return state
    return None


def counts(states: tuple[ClusterState, ...]) -> dict[str, int]:
    """Totals a status line prints.

    Pre-seeded clusters are excluded from the in-window figures: they are a
    baseline's work, so counting them would report progress this run did not
    make.

    Args:
        states: The states from :func:`clusters`.

    Returns:
        ``total``, ``in_window``, ``done``, ``partial``, ``outstanding`` and
        ``pre_seeded``.
    """
    in_window = [s for s in states if s.in_window and not s.pre_seeded]
    return {
        "total": len(states),
        "in_window": len(in_window),
        "done": sum(1 for s in in_window if s.done),
        "partial": sum(1 for s in in_window if s.partial_reason),
        "outstanding": sum(1 for s in in_window if not s.done),
        "pre_seeded": sum(1 for s in states if s.pre_seeded),
    }
