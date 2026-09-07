"""Substrate stage runs: checkpoints and gates, exit codes surfaced raw.

These shell out to the substrate CLIs — the same commands the AI+CLI arm
types — with ``cwd`` at the workspace, and never reinterpret an exit code:
3 from a strict gate is information, not an obstacle. The substrate leaves 2
to argparse, so a 2 here means the invocation was malformed, which is a
defect in the caller rather than a finding about the manifest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..paths import Context
from . import CoreError

_A_RFC = "ai_rfc"


def _run(ctx: Context, module: str, *args: str) -> tuple[int, list[str]]:
    result = subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        cwd=ctx.workspace,
    )
    return result.returncode, [
        line for line in result.stderr.splitlines() if line.strip()
    ]


def write_checkpoint(
    ctx: Context,
    cluster_id: str,
    consolidation: int | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    """Freeze the workspace manifest against one cluster, or as a consolidation.

    Args:
        ctx: The resolved context.
        cluster_id: The cluster to checkpoint against; for a consolidation, the
            cluster its base checkpoint belongs to.
        consolidation: The consolidation's ordinal; when given, the checkpoint
            lands under ``consolidations/<NN>`` instead of ``checkpoints/``.
        base: The cluster checkpoint a consolidation follows, relative to the
            workspace (``checkpoints/<cluster>``). Required with
            ``consolidation``.

    Returns:
        ``{exit_code, stderr, manifest_sha256?}`` — the sha is read back from
        the written checkpoint on success.

    Raises:
        CoreError: If either of ``consolidation`` and ``base`` is given without
            the other. Both refusals precede the shell-out, so a mistyped
            consolidation cannot spend the cluster's write-once checkpoint.
    """
    if consolidation is None:
        if base is not None:
            raise CoreError("a base checkpoint needs consolidation=<NN>")
        out = ctx.workspace / "checkpoints"
        extra: list[str] = []
        record_dir = out / cluster_id
    else:
        if base is None:
            raise CoreError(
                "a consolidation checkpoint needs base=checkpoints/<cluster>"
            )
        out = ctx.workspace / "consolidations"
        extra = [
            "--consolidation",
            str(consolidation),
            "--base",
            str(ctx.workspace / base),
        ]
        record_dir = out / f"{consolidation:02d}"
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
        str(out),
        *extra,
    )
    result: dict[str, Any] = {"exit_code": code, "stderr": stderr}
    record = record_dir / "checkpoint.json"
    if code == 0 and record.exists():
        result["manifest_sha256"] = json.loads(record.read_text())["manifest_sha256"]
    return result


def manifest_gate(ctx: Context, strict: bool = False) -> dict[str, Any]:
    """Run the manifest gate (linter without ``strict``).

    Args:
        ctx: The resolved context.
        strict: Exit 3 on any finding.

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
    code, stderr = _run(ctx, f"{_A_RFC}.check", *args)
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
        strict: Exit 3 on any finding.

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
        json.loads(report_path.read_text())["findings"] if report_path.exists() else []
    )
    return {"exit_code": code, "stderr": stderr, "findings": findings}
