"""Substrate stage runs: checkpoints and gates, exit codes surfaced raw.

These shell out to the substrate CLIs — the same commands the AI+CLI arm
types — with ``cwd`` at the PANTHER checkout, and never reinterpret an exit
code: 2 from a strict gate is information, not an obstacle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..paths import Context

_A_RFC = "panther.plugins.services.testers.a_rfc"


def _run(ctx: Context, module: str, *args: str) -> tuple[int, list[str]]:
    result = subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        cwd=ctx.panther_repo,
    )
    return result.returncode, [
        line for line in result.stderr.splitlines() if line.strip()
    ]


def write_checkpoint(ctx: Context, cluster_id: str) -> dict[str, Any]:
    """Freeze the workspace manifest against one cluster.

    Args:
        ctx: The resolved context.
        cluster_id: The cluster to checkpoint against.

    Returns:
        ``{exit_code, stderr, manifest_sha256?}`` — the sha is read back
        from the written checkpoint on success.
    """
    code, stderr = _run(
        ctx,
        f"{_A_RFC}.draft",
        "checkpoint",
        str(ctx.manifest),
        "--timeline",
        str(ctx.workspace / "timeline"),
        "--cluster",
        cluster_id,
        "--out",
        str(ctx.workspace / "checkpoints"),
    )
    result: dict[str, Any] = {"exit_code": code, "stderr": stderr}
    record = ctx.workspace / "checkpoints" / cluster_id / "checkpoint.json"
    if code == 0 and record.exists():
        result["manifest_sha256"] = json.loads(record.read_text())[
            "manifest_sha256"
        ]
    return result


def manifest_gate(ctx: Context, strict: bool = False) -> dict[str, Any]:
    """Run the manifest gate (linter without ``strict``).

    Args:
        ctx: The resolved context.
        strict: Exit 2 on any finding.

    Returns:
        ``{exit_code, stderr, report}`` — ``report`` is the summary slice
        of ``out/report.json`` when it was written.
    """
    args = [
        str(ctx.manifest),
        "--out",
        str(ctx.workspace / "out"),
        "--repo",
        str(ctx.workspace / "clone"),
    ]
    if strict:
        args.append("--strict")
    code, stderr = _run(ctx, _A_RFC, *args)
    report_path = ctx.workspace / "out" / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return {
        "exit_code": code,
        "stderr": stderr,
        "report": (
            {
                "count_by_status": report["count_by_status"],
                "promotable_count": report.get("promotable_count"),
                "violations": report.get("violations", []),
                "unverified_anchors": report.get("unverified_anchors", []),
            }
            if report
            else None
        ),
    }


def citation_gate(ctx: Context, strict: bool = False) -> dict[str, Any]:
    """Run the citation gate over the draft's revision map.

    Args:
        ctx: The resolved context.
        strict: Exit 2 on any finding.

    Returns:
        ``{exit_code, stderr, findings}`` — findings from
        ``out/gate-report.json`` when written.
    """
    args = [
        "gate",
        str(ctx.workspace / "draft"),
        "--timeline",
        str(ctx.workspace / "timeline"),
        "--checkpoints",
        str(ctx.workspace / "checkpoints"),
        "--questions",
        str(ctx.questions),
        "--revisions",
        str(ctx.revisions),
        "--out",
        str(ctx.workspace / "out"),
    ]
    if strict:
        args.append("--strict")
    code, stderr = _run(ctx, f"{_A_RFC}.draft", *args)
    report_path = ctx.workspace / "out" / "gate-report.json"
    findings = (
        json.loads(report_path.read_text())["findings"]
        if report_path.exists()
        else []
    )
    return {"exit_code": code, "stderr": stderr, "findings": findings}
