"""Substrate stage runs: checkpoints and gates, exit codes surfaced raw.

These call the substrate's own functions in process — the same code the
``ai-rfc`` CLI reaches when the AI+CLI arm types the command — and reproduce
that CLI's exit code and diagnostics rather than reinterpreting them: 3 from a
strict gate is information, not an obstacle. 2 belonged to ``argparse`` and
cannot occur here at all, because there is no longer an argument vector to
malform; a caller's mistake now arrives as a refusal before any work is done.

Each verb catches the exception family its CLI branch catches, and only that
family; :func:`ai_rfc.server.core.reported` rebuilds the boundary the child
process used to provide for everything else.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any

from ai_rfc.draft.checkpoint import CheckpointError
from ai_rfc.draft.checkpoint import write_checkpoint as write_cluster_checkpoint
from ai_rfc.draft.checkpoint import write_consolidation_checkpoint
from ai_rfc.draft.gate import GateError, run_gate, write_gate_report
from ai_rfc.report import build as build_manifest_report
from ai_rfc.report import to_json, to_markdown, to_yaml
from ai_rfc.schema import SchemaError, load

from ..paths import Context
from . import CoreError, diagnostics, reported


def _freeze_checkpoint(freeze: partial[Path]) -> tuple[int, list[str]]:
    """Write one checkpoint the way ``ai-rfc draft checkpoint`` writes it.

    Args:
        freeze: The already-argumented checkpoint writer to call; which of the
            two it is, and with what, is the caller's decision.

    Returns:
        The exit code and the diagnostics, one element per line.
    """
    try:
        written = freeze()
    except (CheckpointError, SchemaError, OSError) as error:
        return 1, diagnostics(f"error: {error}")
    return 0, diagnostics(f"note: checkpoint written to {written}")


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
            the other, or if ``base`` is absolute or climbs out of the
            workspace. Every refusal precedes the write, so a mistyped
            consolidation cannot spend the cluster's write-once checkpoint, and
            a base naming another workspace cannot be consolidated from.
    """
    if consolidation is None:
        if base is not None:
            raise CoreError("a base checkpoint needs consolidation=<NN>")
        out = ctx.workspace / "checkpoints"
        record_dir = out / cluster_id
        freeze = partial(
            write_cluster_checkpoint,
            ctx.manifest,
            ctx.workspace / "timeline",
            cluster_id,
            out,
        )
    else:
        if base is None:
            raise CoreError(
                "a consolidation checkpoint needs base=checkpoints/<cluster>"
            )
        named = Path(base)
        if named.is_absolute() or ".." in named.parts:
            raise CoreError(
                "base must be a workspace-relative path such as "
                "checkpoints/<cluster>"
            )
        out = ctx.workspace / "consolidations"
        record_dir = out / f"{consolidation:02d}"
        freeze = partial(
            write_consolidation_checkpoint,
            ctx.manifest,
            consolidation,
            ctx.workspace / base,
            cluster_id,
            out,
        )
    code, stderr = reported(partial(_freeze_checkpoint, freeze))
    result: dict[str, Any] = {"exit_code": code, "stderr": stderr}
    record = record_dir / "checkpoint.json"
    if code == 0 and record.exists():
        result["manifest_sha256"] = json.loads(record.read_text())["manifest_sha256"]
    return result


def _check_manifest(ctx: Context, strict: bool) -> tuple[int, list[str]]:
    """Validate the manifest the way ``ai-rfc check`` validates it.

    The CLI's ``--repo not given`` note has no counterpart here: the core
    always names a repository and refuses above when that path is not one, so
    ``anchors_checked`` is true wherever the note could be reached.

    Args:
        ctx: The resolved context.
        strict: Exit 3 on any finding.

    Returns:
        The exit code and the diagnostics, one element per line.
    """
    out = ctx.workspace / "out"
    repo = ctx.workspace / "clone"
    try:
        manifest = load(ctx.manifest)
    except (SchemaError, OSError) as error:
        return 1, diagnostics(f"error: could not read manifest {ctx.manifest}: {error}")

    if not (repo / ".git").exists():
        return 1, diagnostics(f"error: {repo} is not a git repository")

    report = build_manifest_report(manifest, repo=repo)

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(to_json(report))
    (out / "report.yaml").write_text(to_yaml(report))
    (out / "report.md").write_text(to_markdown(report))

    messages = [
        f"violation: {violation.claim_id}: {violation.reason}"
        for violation in report.violations
    ]
    messages += [f"unverified: {item}" for item in report.unverified]
    if (report.violations or report.unverified) and strict:
        return 3, diagnostics(*messages)
    return 0, diagnostics(*messages)


def manifest_gate(ctx: Context, strict: bool = False) -> dict[str, Any]:
    """Run the manifest gate (linter without ``strict``).

    Args:
        ctx: The resolved context.
        strict: Exit 3 on any finding.

    Returns:
        ``{exit_code, stderr, report}`` — ``report`` is the summary slice
        of ``out/report.json`` when it was written.
    """
    code, stderr = reported(partial(_check_manifest, ctx, strict))
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


def _gate_citations(ctx: Context, strict: bool) -> tuple[int, list[str]]:
    """Gate the revision map the way ``ai-rfc draft gate`` gates it.

    ``write_gate_report`` sits outside the family on purpose, as it does in the
    CLI: a gate that ran and then could not record itself is not a finding
    about the draft.

    Args:
        ctx: The resolved context.
        strict: Exit 3 on any finding.

    Returns:
        The exit code and the diagnostics, one element per line.
    """
    try:
        findings = run_gate(
            ctx.workspace / "draft",
            ctx.workspace / "timeline",
            ctx.workspace / "checkpoints",
            ctx.questions,
            ctx.revisions,
        )
    except (GateError, OSError) as error:
        return 1, diagnostics(f"error: {error}")

    write_gate_report(ctx.workspace / "out", findings)

    messages = [f"finding: {finding}" for finding in findings]
    if findings and strict:
        return 3, diagnostics(*messages)
    if not findings:
        messages.append("note: gate clean")
    return 0, diagnostics(*messages)


def citation_gate(ctx: Context, strict: bool = False) -> dict[str, Any]:
    """Run the citation gate over the draft's revision map.

    Args:
        ctx: The resolved context.
        strict: Exit 3 on any finding.

    Returns:
        ``{exit_code, stderr, findings}`` — findings from
        ``out/gate-report.json`` when written.
    """
    code, stderr = reported(partial(_gate_citations, ctx, strict))
    report_path = ctx.workspace / "out" / "gate-report.json"
    findings = (
        json.loads(report_path.read_text())["findings"] if report_path.exists() else []
    )
    return {"exit_code": code, "stderr": stderr, "findings": findings}
