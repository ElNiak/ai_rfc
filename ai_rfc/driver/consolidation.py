"""When a consolidation round is due, derived entirely from artifacts.

Nothing here records state (D50): the answer is a function of the workspace's
``revisions.yaml``, so a resumed sweep schedules exactly as one that never
stopped. Revisions are read through the substrate's own loader, so the driver
and the gate cannot disagree about what a revision is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ai_rfc.draft.gate import GateError, load_revisions


@dataclass(frozen=True)
class Due:
    """A consolidation round that should run now."""

    ordinal: int
    base_cluster: str
    since: int
    reason: str


def consolidations_recorded(workspace: Path) -> int:
    """How many consolidation revisions the workspace records.

    Read through the gate's own loader so the driver and the gate cannot
    disagree about what a revision is. It is what says a round *worked*:
    :func:`consolidation_due` answers None both for a map with nothing
    outstanding and for one its loader refuses, so a round credited by that
    silence would be credited by its own damage.

    Args:
        workspace: The run's workspace.

    Returns:
        The number of entries carrying ``kind: consolidation``, and 0 when the
        map is missing or will not load. The count is evidence a round left
        behind, so anything standing between the caller and that evidence is an
        absence of proof and never a reason to raise inside a sweep.
    """
    try:
        entries = load_revisions(workspace / "revisions.yaml")
    except Exception:  # noqa: BLE001 - no proof of a revision is not one
        return 0
    return sum(1 for entry in entries if entry.kind == "consolidation")


def consolidation_due(
    workspace: Path, every: int, *, at_end: bool = False
) -> Due | None:
    """Decide whether a consolidation round is due.

    Args:
        workspace: The reconstruction workspace.
        every: Cluster rounds between consolidations; 0 disables mid-sweep ones.
        at_end: True when the sweep has no clusters left, which consolidates any
            unconsolidated remainder regardless of ``every``.

    Returns:
        The round to run, or None when none is due — including when every
        cluster round so far is already consolidated.
    """
    revisions = workspace / "revisions.yaml"
    if not revisions.is_file():
        return None
    try:
        entries = load_revisions(revisions)
    except (GateError, OSError, yaml.YAMLError, TypeError):
        # A malformed revisions file is the gate's finding to report, not a
        # reason to schedule an editorial pass over it. load_revisions
        # documents GateError for a malformed document but raises two others
        # before its own validation runs: yaml.safe_load's error when the
        # document will not scan, and TypeError when it sorts the revision
        # mapping's keys and one of them implicit-typed to a non-string.
        return None

    consolidations = 0
    since = 0
    base_cluster = ""
    for entry in entries:
        if entry.kind == "consolidation":
            consolidations += 1
            since = 0
        else:
            since += 1
            base_cluster = entry.cluster_id

    if since == 0 or not base_cluster:
        return None
    if at_end:
        return Due(consolidations + 1, base_cluster, since, "sweep end")
    if every and since >= every:
        rounds = "round" if since == 1 else "rounds"
        return Due(consolidations + 1, base_cluster, since, f"{since} cluster {rounds}")
    return None
