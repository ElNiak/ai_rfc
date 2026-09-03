"""What a run has done so far, and how to say it in one line.

Separated from the loop that prints it because the two fail differently. The
loop's job is to spend a budget correctly; this module's job is to describe
what the loop is doing, and nothing here may end a run — a wrong progress line
is a nuisance, a progress line that raises is hours of work lost.

The one exception is deliberate. :func:`cluster_span` refuses a timeline whose
``members.jsonl`` contradicts its ``clusters.jsonl``, because a span printed
from files that disagree would not be the span the agent was given. The caller
catches it and reports it as a line, rather than letting it propagate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import ExperimentError
from .metrics import cluster_artifacts, window_clusters

#: Longest commit subject rendered before it is elided.
TITLE_LIMIT = 60


def window_progress(
    workspace: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int, int, int]:
    """The next unfinished cluster, and where it sits in this run's work.

    Read from the workspace rather than counted in memory, so a resumed or
    re-entered run agrees with what is actually on disk.

    Pre-seeded clusters are excluded from both counts: they are work a baseline
    already did, so counting them would report progress this run did not make.
    ``total`` is therefore what this run is responsible for, which is not the
    window's size and does not shrink as clusters finish.

    Args:
        workspace: The run's workspace.

    Returns:
        ``(row, artifacts, position, done, total)``. ``row`` is the cluster to
        process next, or ``None`` when there is none, and ``artifacts`` is that
        row's record, returned so the caller need not read it a second time.
        ``position`` is the row's 1-based index among the counted clusters —
        not ``done + 1``, because a finished cluster may sit after an
        unfinished one — and is 0 when there is no row.
    """
    counted = [
        (row, artifacts)
        for row, artifacts in (
            (row, cluster_artifacts(workspace, row))
            for row in window_clusters(workspace)
        )
        if not artifacts["pre_seeded"]
    ]
    done = sum(1 for _, artifacts in counted if artifacts["artifacts"])
    for index, (row, artifacts) in enumerate(counted, start=1):
        if not artifacts["artifacts"]:
            return row, artifacts, index, done, len(counted)
    return None, None, 0, done, len(counted)


def _members(workspace: Path) -> list[dict[str, Any]]:
    """Member rows from the workspace's timeline, skipping what will not parse.

    Tolerant for the reason :func:`stream.salvage_stream` is: a kill can
    truncate the last line mid-write, and the cost of refusing to read the rest
    would be borne by a run that is otherwise fine.
    """
    try:
        text = (workspace / "timeline" / "members.jsonl").read_text(errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def cluster_span(workspace: Path, cluster: dict[str, Any]) -> str | None:
    """The commit range one cluster covers, as ``base..head``.

    The span ends at the cluster's LAST member — a PR's anchor merge, or an
    epoch's final spine commit — matching the rule :mod:`views.emit` uses to
    build the ``span.diff`` the agent reads. The anchor names an epoch's FIRST
    member, so ending at it would drop every later commit of the epoch; on the
    MARK corpus that is 29 of 32 epochs.

    Args:
        workspace: The run's workspace.
        cluster: One timeline row.

    Returns:
        ``"<base>..<head>"``, both abbreviated, with ``root`` for a cluster at
        the start of the timeline — where ``views.emit`` uses the empty-tree
        hash, which is correct to diff against but not to read. ``None`` when
        the workspace holds no usable members for this cluster.

    Raises:
        ExperimentError: If a PR's last member is not the anchor its row names.
            The two files disagree, so a span printed from them would not be
            the one the agent was given.
    """
    members = [
        row
        for row in _members(workspace)
        if row.get("cluster_id") == cluster.get("id")
        and isinstance(row.get("position"), int)
        and isinstance(row.get("sha"), str)
    ]
    if not members:
        return None
    head = max(members, key=lambda row: row["position"])["sha"]
    anchor = cluster.get("anchor_sha")
    if cluster.get("kind") == "pr" and head != anchor:
        raise ExperimentError(
            f"{cluster.get('id')}: a PR's last member is its anchor merge, but "
            f"members.jsonl ends at {head[:12]} and the row names "
            f"{str(anchor)[:12]}"
        )
    base = cluster.get("spine_prev_sha")
    return f"{base[:7] if base else 'root'}..{head[:7]}"


def _bar(done: int, total: int, cells: int = 10) -> str:
    """A fixed-width ASCII bar.

    Deliberately static: a campaign's output is redirected to a log read hours
    later, which carriage-return animation would corrupt.

    Args:
        done: Clusters finished.
        total: Clusters this run is responsible for.
        cells: Width of the bar in characters.

    Returns:
        The bar, e.g. ``[###-------]``.
    """
    filled = min(cells, done * cells // total) if total > 0 else 0
    return "[" + "#" * filled + "-" * (cells - filled) + "]"


def _duration(seconds: float) -> str:
    """Whole minutes under an hour, hours and minutes above it.

    Args:
        seconds: A count of seconds.

    Returns:
        A short reading, e.g. ``41m`` or ``8h00m``.
    """
    minutes = int(seconds // 60)
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m"


def _log_safe(text: str) -> str:
    """One line of printable ASCII, whatever the target's history contained.

    A commit subject is arbitrary text from someone else's repository, and it
    is being written into the log this output exists to make readable. A stray
    carriage return or escape sequence would corrupt the lines around it.
    """
    ascii_only = text.encode("ascii", "replace").decode("ascii")
    return "".join(ch if ch.isprintable() else " " for ch in ascii_only).strip()


def _tokens(total: int) -> str:
    """Token counts at log scale: 63000 reads as 63k."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M"
    return f"{total // 1000}k" if total >= 1000 else str(total)


def _names(ids: list[str], shown: int = 3) -> str:
    """A few ids and a count of the rest."""
    head = ", ".join(ids[:shown])
    rest = len(ids) - shown
    return f"{head} (+{rest} more)" if rest > 0 else head


def digest(record: dict[str, Any]) -> list[str]:
    """Render one cluster's record as at most six log-safe lines.

    The record is the durable artifact; this is the part an operator watching
    a sweep actually reads, so it leads with the outcome and keeps every figure
    on a line that can be grepped on its own.

    Args:
        record: A record from :func:`summary.build_summary`.

    Returns:
        The lines, without the run-id prefix the caller adds.
    """
    cluster = record.get("cluster") or {}
    outcome = record.get("outcome") or "complete"
    attempts = len(record.get("attempts") or [])
    head = "[done]" if outcome == "complete" else f"[FAILED {outcome}]"
    lines = [
        f"{head} cluster {cluster.get('ordinal')} ({cluster.get('id')}) - "
        f"{_duration(record.get('wall_s') or 0)} - ${record.get('cost_usd') or 0:.2f}"
        f" - {_tokens((record.get('tokens') or {}).get('total') or 0)} tok - "
        f"{attempts} attempt{'' if attempts == 1 else 's'}"
    ]

    claims = record.get("claim_delta") or {}
    new_ids = claims.get("new_ids") or []
    cited = record.get("citation_delta") or {}
    claim_line = f"claims {claims.get('held_count', 0)} (+{len(new_ids)})"
    if cited:
        # An unreadable predecessor tag makes every citation look added, so the
        # figure is withheld rather than printed as though it were measured.
        if cited.get("error"):
            claim_line += " - cited ? (predecessor unreadable)"
        else:
            claim_line += f" - cited +{len(cited.get('added') or [])}"
            removed = len(cited.get("removed") or [])
            if removed:
                claim_line += f"/-{removed}"
    if new_ids:
        claim_line += f" - new: {_names(new_ids)}"
    lines.append(claim_line)

    stat = record.get("diffstat")
    normative = record.get("normative_change")
    if stat is not None or normative is not None:
        kind = (
            "normative"
            if normative
            else "documentation" if normative is False else "unrecorded"
        )
        shape = (
            f"{stat['files']} file, +{stat['insertions']}/-{stat['deletions']}"
            if stat
            else "no diff"
        )
        lines.append(f"{cited.get('tag') or 'no tag'}: {kind} - {shape}")

    # Ahead of the optional lines, not after them: a full record plus an error
    # runs to seven lines, and truncating at six dropped the one line saying
    # the figures above are incomplete.
    for error in (record.get("errors") or [])[:1]:
        lines.append(f"summary partial: {_log_safe(str(error))[:90]}")

    touched = record.get("files_touched") or []
    if touched:
        lines.append(f"touched: {_names([Path(p).name for p in touched], shown=4)}")

    questions = record.get("questions") or {}
    if questions.get("new_count"):
        first = (questions.get("new") or [None])[0]
        named = (
            f": {first['id']} {_log_safe(str(first.get('first_line', '')))[:60]}"
            if first
            else ""
        )
        lines.append(f"{questions['new_count']} new question(s){named}")

    note = record.get("note")
    if note:
        lines.append(f'note: "{_log_safe(str(note))[:90]}"')
    return lines[:6]


def describe(cluster: dict[str, Any], span: str | None) -> str:
    """Name what a cluster covers, from whatever fields its row carries.

    Every field is optional. The loop's tests drive it with rows holding only
    an id and an ordinal, and a real row can always lose a field to a timeline
    written by an older build.

    Args:
        cluster: One timeline row.
        span: The commit range from :func:`cluster_span`, or None.

    Returns:
        A single dash-separated line.
    """
    parts = [str(cluster.get("id", "?"))]
    kind, count = cluster.get("kind"), cluster.get("member_count")
    if kind and count is not None:
        parts.append(f"{kind}, {count} commit{'' if count == 1 else 's'}")
    elif kind:
        parts.append(str(kind))
    if span:
        parts.append(span)
    title = _log_safe(str(cluster.get("title") or ""))
    if title:
        if len(title) > TITLE_LIMIT:
            title = title[: TITLE_LIMIT - 3] + "..."
        # An epoch's title is its FIRST commit's subject, not a summary of the
        # slice, so it is introduced rather than presented as the slice's name.
        parts.append(f'from "{title}"' if kind == "epoch" else f'"{title}"')
    return " - ".join(parts)
