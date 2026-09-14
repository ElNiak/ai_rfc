"""Draft build and lint over the substrate verbs, exit codes surfaced raw.

Both call the substrate's own functions in process and reproduce the
``ai-rfc draft`` CLI's exit code and diagnostics; :mod:`ai_rfc.server.core.gates`
carries the reasoning, including why each verb names its own exception family
and why the boundary around everything else is rebuilt rather than removed.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any

from ai_rfc.draft.build import BUILD_DIR
from ai_rfc.draft.build import REPORT_FILE as BUILD_REPORT
from ai_rfc.draft.build import BuildError, build, resolve_toolchain
from ai_rfc.draft.gate import GateError, draft_text
from ai_rfc.draft.lint import REPORT_FILE as LINT_REPORT
from ai_rfc.draft.lint import lint, write_lint_report
from ai_rfc.schema import SchemaError, load

from ..paths import Context
from . import CoreError, diagnostics, reported

_METRIC_KEYS = (
    "sections",
    "abstract",
    "references",
    "keywords",
    "blocks",
    "citations",
    "narration",
    "extra",
)


def _build_draft(ctx: Context, ref: str) -> tuple[int, list[str]]:
    """Compile the draft the way ``ai-rfc draft build`` compiles it.

    The CLI's ``--targets`` and ``--date`` defaults are the API's own, and the
    core never passed either, so neither is named here. ``--strict`` is never
    passed either: a build with findings is reported, not refused, and 3 cannot
    occur.

    Args:
        ctx: The resolved context; ``ctx.toolchain`` names the record.
        ref: Tag, branch or commit to build.

    Returns:
        The exit code and the diagnostics, one element per line.
    """
    out = ctx.workspace / "out"
    refcache = ctx.workspace / "refcache"
    try:
        report = build(
            ctx.workspace / "draft",
            toolchain=resolve_toolchain(ctx.toolchain),
            out=out,
            ref=ref,
            refcache=refcache if refcache.is_dir() else None,
        )
    except (BuildError, OSError) as error:
        return 1, diagnostics(f"error: {error}")
    messages = [f"finding: {finding}" for finding in report.findings]
    messages.append(
        f"note: build of {report.commit[:12]} exited {report.exit_code}; "
        f"report at {out / BUILD_DIR / BUILD_REPORT}"
    )
    return 0, diagnostics(*messages)


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
            "toolchain (ai-rfc toolchain provision)"
        )
    report_path = ctx.workspace / "out" / BUILD_DIR / BUILD_REPORT
    report_path.unlink(missing_ok=True)
    code, stderr = reported(partial(_build_draft, ctx, ref))
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return {
        "exit_code": code,
        "stderr": stderr,
        "findings": report["findings"] if report else stderr,
        "commit": report["commit"] if report else None,
        "outputs": report["outputs"] if report else {},
    }


def _lint_draft(ctx: Context, worktree: bool) -> tuple[int, list[str]]:
    """Measure the draft the way ``ai-rfc draft lint`` measures it.

    An unloadable manifest is a finding rather than an error, so its own family
    is caught separately and narrowly. ``lint`` renders the manifest itself and
    raises whatever the renderer does: a manifest the loader accepted can still
    refuse to render, and that must read as an error rather than a traceback.

    Args:
        ctx: The resolved context.
        worktree: Measure the uncommitted draft file rather than ``HEAD``.

    Returns:
        The exit code and the diagnostics, one element per line.
    """
    draft_repo = ctx.workspace / "draft"
    try:
        if worktree:
            candidates = sorted(
                path
                for path in draft_repo.iterdir()
                if path.name.startswith("draft-") and path.suffix == ".md"
            )
            if len(candidates) != 1:
                raise GateError(
                    f"{draft_repo}: expected exactly one draft-*.md, "
                    f"found {len(candidates)}"
                )
            text, ref = candidates[0].read_text(), "worktree"
        else:
            _, text = draft_text(draft_repo, "HEAD")
            ref = "HEAD"
        manifest = None
        manifest_error = None
        try:
            manifest = load(ctx.manifest)
        except (SchemaError, OSError) as error:
            manifest_error = str(error)
        report = lint(
            text,
            manifest=manifest,
            manifest_error=manifest_error,
            source={"path": str(draft_repo), "ref": ref},
        )
    except (ValueError, OSError) as error:
        return 1, diagnostics(f"error: {error}")
    report_path = write_lint_report(ctx.workspace / "out", report)
    messages = [f"finding: {finding}" for finding in report.findings]
    messages.append(f"note: lint report at {report_path}")
    return 0, diagnostics(*messages)


def draft_lint(ctx: Context, worktree: bool = True) -> dict[str, Any]:
    """Measure the draft's quality against the workspace manifest.

    Args:
        ctx: The resolved context.
        worktree: Measure the uncommitted draft file (the default, so an author
            can lint before committing) rather than ``HEAD``.

    Returns:
        ``{exit_code, stderr, findings, metrics}`` from ``out/lint-report.json``.
    """
    report_path = ctx.workspace / "out" / LINT_REPORT
    report_path.unlink(missing_ok=True)
    code, stderr = reported(partial(_lint_draft, ctx, worktree))
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return {
        "exit_code": code,
        "stderr": stderr,
        "findings": report["findings"] if report else stderr,
        "metrics": {key: report[key] for key in _METRIC_KEYS} if report else {},
    }
