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


QUESTIONS_FILE = "questions.yaml"


def question_ids(workspace: Path) -> set[str]:
    """Every question id the workspace records.

    Read leniently rather than through the draft package's strict loader: this
    is a progress figure, and one malformed entry must not end a run.

    Args:
        workspace: The run's workspace.

    Returns:
        The ids; empty when the file is absent or unreadable.
    """
    try:
        document = yaml.safe_load((workspace / QUESTIONS_FILE).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {str(key) for key in (document.get("questions") or {})}


def new_questions(workspace: Path, before: set[str]) -> dict[str, Any]:
    """The questions raised since ``before`` was taken.

    Snapshot-differenced rather than filtered by time: ``asked_at`` is a date,
    so several clusters in one day are indistinguishable by it.

    Args:
        workspace: The run's workspace.
        before: The ids present before the cluster ran.

    Returns:
        ``{new_count, new: [{id, first_line}]}``.
    """
    try:
        document = yaml.safe_load((workspace / QUESTIONS_FILE).read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {"new_count": 0, "new": []}
    fresh = []
    for key, entry in sorted((document.get("questions") or {}).items()):
        if str(key) in before or not isinstance(entry, dict):
            continue
        text = str(entry.get("question") or "").strip()
        fresh.append(
            {"id": str(key), "first_line": text.splitlines()[0] if text else ""}
        )
    return {"new_count": len(fresh), "new": fresh}


#: Tool arguments that name a file the session looked at or changed.
_PATH_KEYS = ("file_path", "path", "notebook_path")


def transcript_facts(
    events: list[dict[str, Any]], sessions: list[str]
) -> dict[str, Any]:
    """Timing, tokens, tool surface and files touched, for one cluster's slice.

    Args:
        events: The whole run transcript, already salvaged.
        sessions: The session ids belonging to this cluster.

    Returns:
        ``{timing, tokens, surface, files_touched}``. A session killed on its
        cap emits no result event, so timing and tokens report absence rather
        than zero.
    """
    from .stream import init_event, result_events, session_events, tool_uses

    sliced: list[dict[str, Any]] = []
    for session in sessions:
        sliced.extend(session_events(events, session))

    results = result_events(sliced)
    totals = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    duration = api = ttft = None
    for result in results:
        usage = result.get("usage") or {}
        totals["input"] += int(usage.get("input_tokens") or 0)
        totals["output"] += int(usage.get("output_tokens") or 0)
        totals["cache_creation"] += int(usage.get("cache_creation_input_tokens") or 0)
        totals["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
        duration = (duration or 0) + int(result.get("duration_ms") or 0)
        api = (api or 0) + int(result.get("duration_api_ms") or 0)
        ttft = ttft if ttft is not None else result.get("ttft_ms")

    init = init_event(sliced) or {}
    servers = {
        str(server.get("name")): str(server.get("status"))
        for server in (init.get("mcp_servers") or [])
        if isinstance(server, dict)
    }

    touched: list[str] = []
    for use in tool_uses(sliced):
        arguments = use.get("input") or {}
        for key in _PATH_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value and value not in touched:
                touched.append(value)

    return {
        "timing": {"duration_ms": duration, "duration_api_ms": api, "ttft_ms": ttft},
        "tokens": {
            **totals,
            "total": sum(totals.values()),
            "source": "result" if results else "absent",
        },
        "surface": {
            "model": init.get("model"),
            "tools_count": len(init.get("tools") or []),
            "mcp_servers": servers,
        },
        "files_touched": touched,
    }


def build_summary(
    campaign: Campaign,
    ref: Any,
    cluster: dict[str, Any],
    *,
    outcome: str,
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    sessions: list[str],
    seen_claim_ids: frozenset[str],
    held_ids: frozenset[str],
    wall_s: float,
    errors: list[str],
) -> dict[str, Any]:
    """Assemble one cluster's record. Never raises.

    Args:
        campaign: The frozen campaign.
        ref: The run being executed.
        cluster: The timeline row.
        outcome: ``complete``, ``attempts_exhausted`` or ``surface_shortfall``.
        attempts: One entry per attempt made on this cluster.
        events: The whole run transcript, already salvaged.
        sessions: The session ids belonging to this cluster.
        seen_claim_ids: Every claim id held before this cluster.
        held_ids: The claim ids this cluster's checkpoint holds.
        wall_s: Seconds spent on this cluster, from the loop's own clock.
        errors: Failures collected by the caller, extended in place.

    Returns:
        The record, with a null field and an ``errors`` entry for any section
        that could not be computed.
    """
    from .progress import cluster_span

    _extend_sys_path(campaign)
    record: dict[str, Any] = {
        "schema_version": 1,
        "run_id": ref.run_id,
        "arm": ref.arm,
        "outcome": outcome,
        "cluster": {
            "id": cluster.get("id"),
            "ordinal": cluster.get("ordinal"),
            "kind": cluster.get("kind"),
            "title": cluster.get("title"),
            "span": None,
        },
        "attempts": attempts,
        "sessions": sessions,
        "wall_s": round(wall_s, 1),
        "cost_usd": round(sum(a.get("cost_usd") or 0.0 for a in attempts), 4),
        "claim_delta": {
            "new_ids": sorted(held_ids - seen_claim_ids),
            "held_count": len(held_ids),
            "basis": "cumulative",
        },
        "citation_delta": None,
        "diffstat": None,
        "normative_change": None,
        "note": None,
        "questions": {"new_count": 0, "new": []},
        "errors": errors,
    }

    try:
        record["cluster"]["span"] = cluster_span(ref.workspace, cluster)
    except Exception as error:  # noqa: BLE001 - a display line may not end a run
        errors.append(f"span: {error}")

    try:
        record.update(transcript_facts(events, sessions))
    except Exception as error:  # noqa: BLE001
        errors.append(f"transcript: {error}")

    try:
        entry = revision_of(ref.workspace, str(cluster.get("id")))
    except Exception as error:  # noqa: BLE001
        errors.append(f"revision: {error}")
        entry = None

    if entry is None:
        errors.append(f"revision: no entry for {cluster.get('id')}")
        return record

    record["normative_change"] = entry.get("normative_change")
    record["note"] = entry.get("note")
    tag = str(entry.get("tag"))
    try:
        record["citation_delta"] = citation_delta(ref.workspace, tag)
    except Exception as error:  # noqa: BLE001
        errors.append(f"citation_delta: {error}")
    try:
        record["diffstat"] = diffstat(ref.workspace, tag)
    except Exception as error:  # noqa: BLE001
        errors.append(f"diffstat: {error}")
    return record


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
