"""Adopt pull records obtained without this tool's forge client.

A repository we hold no credentials for can still be reconstructed when its
records reach us some other way — a forge project export, a glab/gh dump, or
another operator's snapshot. Only reading happens here: the records are handed
to :func:`write_snapshot`, which owns comment-kind validation, the write-once
rule and the layout downstream discovery depends on, so an adopted snapshot
cannot carry anything a fetched one could not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import FULL_FIDELITY, ForgeError, read_snapshot, write_snapshot

Record = dict[str, Any]

_SECTIONS = ("pulls", "reviews", "comments")


def adopt_snapshot(source: Path, out_root: Path) -> Path:
    """Re-file a snapshot another run fetched, without fetching anything.

    A reconstruction's cluster ids are a function of ``pulls.jsonl`` alone, so
    a window prepared months after its forge data was captured is only
    reproducible while those rows are the *same* rows. Refetching cannot give
    that: the forge has moved on, and the new snapshot describes a different
    repository state. This carries the captured rows across instead, and the
    writer — which owns comment-kind validation, the ordering and the
    write-once rule — puts them on disk, so an adopted snapshot cannot hold
    anything a fetched one could not.

    ``acquisition`` becomes ``adopt`` because this process did not fetch them.
    Everything describing *what was captured* is carried: the same
    ``fetched_at`` (so the snapshot keeps the identity it was filed under),
    the same ``clone_head``, and the same completeness.

    Args:
        source: A snapshot directory written by :func:`write_snapshot`.
        out_root: The forge cache root to file the copy under.

    Returns:
        The adopted snapshot's directory.

    Raises:
        ForgeError: If the destination already holds this snapshot, or the
            records carry a comment kind the writer refuses.
        OSError: If ``source`` cannot be read.
        KeyError: If ``source``'s ``meta.json`` omits a field naming what was
            captured. Deliberately not defaulted: guessing the host or the
            pinned head of someone else's capture is how a snapshot ends up
            filed under a repository it does not describe.
    """
    snapshot = read_snapshot(source)
    meta = snapshot["meta"]
    return write_snapshot(
        out_root,
        host=meta["host"],
        owner=meta["owner"],
        repo=meta["repo"],
        kind=meta["kind"],
        clone_head=meta["clone_head"],
        fetched_at=meta["fetched_at"],
        authenticated=meta["authenticated"],
        pulls=snapshot["pulls"],
        reviews=snapshot["reviews"],
        comments=snapshot["comments"],
        # Carried, not recomputed: both describe the route that captured these
        # records, which this one is not. A snapshot written before either
        # field existed still states what it delivered — discussion rows are
        # there or they are not — and that is the most an older capture can
        # say about its own ceiling.
        denied_subfetches=meta.get("denied_subfetches", 0),
        acquisition="adopt",
        fidelity_ceiling=meta.get(
            "fidelity_ceiling",
            FULL_FIDELITY if snapshot["reviews"] or snapshot["comments"] else "pulls",
        ),
    )


def read_records(path: Path) -> tuple[list[Record], list[Record], list[Record]]:
    """Read a records file into the three sequences a snapshot carries.

    A section that is absent reads as empty rather than raising: a forge with
    no reviews omits the key, and that is not a malformed file.

    Args:
        path: A JSON file holding ``{pulls, reviews, comments}``.

    Returns:
        The pulls, reviews and comments, in that order.

    Raises:
        ForgeError: If the file cannot be read as JSON, does not hold an
            object, or names a section that is not a list of objects.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        # ValueError subsumes JSONDecodeError and UnicodeDecodeError; a dump
        # from a non-UTF-8 toolchain must reach the CLI's handler as a
        # ForgeError rather than escaping it as a traceback.
        raise ForgeError(f"{path} could not be read as JSON: {error}") from error

    if not isinstance(payload, dict):
        raise ForgeError(
            f"{path} holds {type(payload).__name__}; an object with "
            f"{', '.join(_SECTIONS)} keys is required"
        )

    sections: list[list[Record]] = []
    for name in _SECTIONS:
        rows = payload.get(name, [])
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ForgeError(f"{path}: {name} must be a list of objects")
        sections.append(rows)

    pulls, reviews, comments = sections
    return pulls, reviews, comments
