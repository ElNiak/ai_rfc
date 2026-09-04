"""Draft build and lint over the substrate verbs, exit codes surfaced raw."""

from __future__ import annotations

import json
from typing import Any

from ai_rfc.draft.build import BUILD_DIR
from ai_rfc.draft.build import REPORT_FILE as BUILD_REPORT
from ai_rfc.draft.lint import REPORT_FILE as LINT_REPORT

from ..paths import Context
from . import CoreError
from .gates import _run

_DRAFT = "ai_rfc.draft"
_METRIC_KEYS = (
    "sections",
    "abstract",
    "references",
    "keywords",
    "blocks",
    "citations",
    "narration",
)


def draft_build(ctx: Context, ref: str = "HEAD") -> dict[str, Any]:
    """Compile the draft at ``ref`` with the configured toolchain, offline.

    Args:
        ctx: The resolved context; ``ctx.toolchain`` must be set.
        ref: Tag, branch or commit to build.

    Returns:
        ``{exit_code, stderr, findings, commit, outputs}`` from
        ``out/build/build-report.json``; ``findings`` falls back to stderr when
        no report was written.

    Raises:
        CoreError: If no toolchain is configured.
    """
    if ctx.toolchain is None:
        raise CoreError(
            "AI_RFC_TOOLCHAIN is unset; the build gate needs a provisioned "
            "toolchain (experiment toolchain provision)"
        )
    args = [
        "build",
        str(ctx.workspace / "draft"),
        "--out",
        str(ctx.workspace / "out"),
        "--ref",
        ref,
        "--toolchain",
        str(ctx.toolchain),
    ]
    refcache = ctx.workspace / "refcache"
    if refcache.is_dir():
        args += ["--refcache", str(refcache)]
    report_path = ctx.workspace / "out" / BUILD_DIR / BUILD_REPORT
    report_path.unlink(missing_ok=True)
    code, stderr = _run(ctx, _DRAFT, *args)
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return {
        "exit_code": code,
        "stderr": stderr,
        "findings": report["findings"] if report else stderr,
        "commit": report["commit"] if report else None,
        "outputs": report["outputs"] if report else {},
    }


def draft_lint(ctx: Context, worktree: bool = True) -> dict[str, Any]:
    """Measure the draft's quality against the workspace manifest.

    Args:
        ctx: The resolved context.
        worktree: Measure the uncommitted draft file (the default, so an author
            can lint before committing) rather than ``HEAD``.

    Returns:
        ``{exit_code, stderr, findings, metrics}`` from ``out/lint-report.json``.
    """
    args = [
        "lint",
        str(ctx.workspace / "draft"),
        "--out",
        str(ctx.workspace / "out"),
        "--manifest",
        str(ctx.manifest),
    ]
    if worktree:
        args.append("--worktree")
    report_path = ctx.workspace / "out" / LINT_REPORT
    report_path.unlink(missing_ok=True)
    code, stderr = _run(ctx, _DRAFT, *args)
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return {
        "exit_code": code,
        "stderr": stderr,
        "findings": report["findings"] if report else stderr,
        "metrics": {key: report[key] for key in _METRIC_KEYS} if report else {},
    }
