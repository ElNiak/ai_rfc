"""The cluster ids this workspace's timeline actually has.

Three joins in this package take a ``cluster_id`` from an agent- or
operator-written source and put it straight into a path under the workspace —
the revision map's checkpoint pin, the cluster view, and the checkpoint the
freeze writes. The guard they need is membership, and it is written here once
rather than three times.

**Two of the three call it; the third is defended by a different route, and
saying so is the point.** :func:`checked_cluster_id` is called by
``revisions.py:58`` and ``queries.py:111``. The checkpoint the freeze writes
(``gates.py:112``, ``record_dir = out / cluster_id``) never reaches it:
:func:`ai_rfc.draft.checkpoint.write_checkpoint` calls ``_cluster_row`` first,
which raises for a cluster the timeline does not have before anything is
created, and ``gates.py`` only reads ``record_dir`` once that returned 0. So
the join is guarded, by the writer rather than by this module — and a reader
treating this docstring as the map of where the guard is *applied* would
otherwise go looking for a call that is not there.

Membership rather than a filter over characters, for the reason
``experiment/per_cluster._checked_cluster_id`` records: a cluster id reaches
these functions out of YAML, whose implicit typing rewrites it before anything
sees it — ``01`` arrives as ``1``, an empty value as ``None``, a sequence as
its repr — so a value can carry no character a filter would catch and still
name nothing. Requiring it to be one of the timeline's own ids covers that, a
``../..`` climbing out of the workspace and an absolute path replacing the
join's root in a single test.

The known set is read through :func:`ai_rfc.draft.gate.cluster_ordinals`, the
same function the other two copies of this predicate use, so the three cannot
disagree about which clusters a workspace has.
"""

from __future__ import annotations

from pathlib import Path

from ai_rfc.draft.gate import cluster_ordinals

from . import CoreError


def known_cluster_ids(workspace: Path) -> frozenset[str]:
    """Every cluster id this workspace's timeline holds.

    The whole timeline, not a window: a pre-seeded baseline is copied into a
    workspace whole, so a legitimate id can sit below the window's first
    ordinal.

    Three ways the set cannot be produced, and one error type for all of them:
    the file is not there or will not open, a line will not parse, or a line
    parses and carries no ``id``/``ordinal``. The last two are the shapes a
    kill mid-write and a hand-edit leave — ``clusters.jsonl`` is written a
    record per line — and neither is an :exc:`OSError`, so catching that alone
    let the guard's own failure path fail in a type the frontends document
    nothing about.

    Args:
        workspace: The workspace root.

    Returns:
        The ids, as a set.

    Raises:
        CoreError: If the timeline has not been written, or cannot be read as
            the record-per-line document it is.
    """
    timeline = workspace / "timeline"
    rows = timeline / "clusters.jsonl"
    try:
        return frozenset(cluster_ordinals(timeline))
    except OSError as error:
        raise CoreError(
            f"no timeline to check a cluster id against at {rows}: {error}; "
            "run the timeline stage before naming a cluster"
        ) from None
    except (ValueError, KeyError) as error:
        raise CoreError(
            f"{rows} is not a cluster record per line ({error!r}); a cluster "
            "id cannot be checked against a timeline that does not read"
        ) from None


def checked_cluster_id(workspace: Path, cluster_id: str) -> str:
    """The id, confirmed to name a cluster this workspace's timeline has.

    Args:
        workspace: The workspace the id will be joined under.
        cluster_id: The id to check.

    Returns:
        ``cluster_id`` unchanged.

    Raises:
        CoreError: If the timeline is unreadable, or the id is not one of it.
    """
    if cluster_id not in known_cluster_ids(workspace):
        # Repr, not the bare value: this message reaches a per-line diagnostic
        # list and an agent's context, and a forged id carries a newline.
        raise CoreError(
            f"cluster id {cluster_id!r} is not a cluster of this workspace's "
            f"timeline; it would reach a path under {workspace} unchecked"
        )
    return cluster_id
