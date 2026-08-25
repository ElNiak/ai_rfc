"""Read-only queries over the workspace artifacts."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import yaml

from ..paths import Context
from . import CoreError, GuardrailError

_SELECT_ONLY = re.compile(r"^\s*select\b", re.IGNORECASE)
_ROW_CAP = 200


def corpus_query(ctx: Context, sql: str) -> list[dict[str, Any]]:
    """Run one SELECT over the corpus index.

    Args:
        ctx: The resolved context.
        sql: A single SELECT statement.

    Returns:
        At most 200 rows as dicts.

    Raises:
        GuardrailError: If the statement is not a lone SELECT.
        CoreError: If the index is stale — rebuilt, never migrated — or
            missing.
    """
    if not _SELECT_ONLY.match(sql) or ";" in sql.rstrip().rstrip(";"):
        raise GuardrailError(
            "corpus_query accepts exactly one SELECT statement; the index "
            "is derived and disposable, and nothing writes through it"
        )
    from panther.plugins.services.testers.a_rfc.history.index import (
        StaleIndexError,
        open_index,
    )

    try:
        conn = open_index(ctx.workspace / "corpus")
    except (StaleIndexError, FileNotFoundError) as error:
        raise CoreError(f"{error}") from None
    try:
        conn.row_factory = None
        cursor = conn.execute(sql.rstrip().rstrip(";"))
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(_ROW_CAP)
    finally:
        conn.close()
    return [dict(zip(columns, row)) for row in rows]


def _clusters(ctx: Context) -> list[dict[str, Any]]:
    path = ctx.workspace / "timeline" / "clusters.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _processed_cluster_ids(ctx: Context) -> set[str]:
    checkpoints = ctx.workspace / "checkpoints"
    processed = (
        {entry.name for entry in checkpoints.iterdir() if entry.is_dir()}
        if checkpoints.is_dir()
        else set()
    )
    revisions = (
        yaml.safe_load(ctx.revisions.read_text())
        if ctx.revisions.exists()
        else None
    )
    if isinstance(revisions, dict):
        for body in (revisions.get("revisions") or {}).values():
            if isinstance(body, dict) and "cluster_id" in body:
                processed.add(body["cluster_id"])
    return processed


def cluster_next(ctx: Context) -> dict[str, Any] | None:
    """Return the lowest-ordinal cluster not yet processed, or ``None``."""
    processed = _processed_cluster_ids(ctx)
    for cluster in _clusters(ctx):
        if cluster["id"] not in processed:
            return cluster
    return None


def cluster_get(
    ctx: Context,
    cluster_id: str,
    include_patch: bool = False,
    patch_offset: int = 0,
    patch_limit: int = 20000,
) -> dict[str, Any]:
    """Return one cluster's view, evidence, and optionally a patch slice.

    Args:
        ctx: The resolved context.
        cluster_id: The cluster to read.
        include_patch: Also return a slice of ``span.diff``.
        patch_offset: Byte offset into the patch.
        patch_limit: Maximum bytes returned (giant epochs stay readable
            through pagination, not truncation-by-surprise).

    Returns:
        ``{view, evidence, patch?, patch_total_bytes?}``.

    Raises:
        CoreError: If the cluster view does not exist.
    """
    cluster_dir = ctx.workspace / "clusters" / cluster_id
    view_path = cluster_dir / "view.json"
    if not view_path.exists():
        raise CoreError(f"no view for {cluster_id}; emit views first")
    result: dict[str, Any] = {"view": json.loads(view_path.read_text())}
    evidence_path = cluster_dir / "evidence" / "pr.json"
    result["evidence"] = (
        json.loads(evidence_path.read_text()) if evidence_path.exists() else None
    )
    if include_patch:
        raw = (cluster_dir / "span.diff").read_bytes()
        result["patch_total_bytes"] = len(raw)
        result["patch"] = raw[patch_offset : patch_offset + patch_limit].decode(
            errors="replace"
        )
    return result


def status(ctx: Context) -> dict[str, Any]:
    """One composite status view, quoted from the substrate's artifacts."""
    timeline_path = ctx.workspace / "timeline" / "timeline.json"
    timeline = (
        json.loads(timeline_path.read_text()) if timeline_path.exists() else None
    )
    clusters = _clusters(ctx) if timeline else []
    processed = _processed_cluster_ids(ctx) if timeline else set()
    report_path = ctx.workspace / "out" / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None

    questions_summary = None
    if ctx.questions.exists():
        from panther.plugins.services.testers.a_rfc.draft.questions import (
            load_questions,
        )

        entries = load_questions(ctx.questions)
        questions_summary = {
            state: sum(1 for entry in entries if entry.status.value == state)
            for state in ("open", "answered", "withdrawn")
        }

    draft_tag = None
    draft = ctx.workspace / "draft"
    if (draft / ".git").exists():
        described = subprocess.run(
            ["git", "-C", str(draft), "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
        )
        draft_tag = described.stdout.strip() if described.returncode == 0 else None

    next_cluster = cluster_next(ctx) if timeline else None
    return {
        "timeline": timeline,
        "clusters_total": len(clusters),
        "clusters_processed": len(processed),
        "next_cluster": next_cluster["id"] if next_cluster else None,
        "report": (
            {
                "count_by_status": report["count_by_status"],
                "promotable_count": report.get("promotable_count"),
                "checked_fraction_by_req_class": report[
                    "checked_fraction_by_req_class"
                ],
                "violations": len(report.get("violations", [])),
                "unverified_anchors": len(report.get("unverified_anchors", [])),
            }
            if report
            else None
        ),
        "questions": questions_summary,
        "draft_tag": draft_tag,
    }
