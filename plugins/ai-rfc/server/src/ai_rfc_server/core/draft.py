"""Draft-repository operations: commit prose, tag a revision.

Both exist so the tool arm can finish a normative revision without a
shell. A tag is created only after the strict manifest gate passes and is
deleted again if the strict citation gate then finds anything, so a tag
that survives is one both gates accepted. Exit codes are surfaced, never
reinterpreted.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..paths import Context
from . import CoreError
from .gates import citation_gate, manifest_gate


def _git(ctx: Context, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ctx.workspace / "draft"), *args],
        capture_output=True,
        text=True,
    )


def _require_repo(ctx: Context) -> None:
    if not (ctx.workspace / "draft" / ".git").exists():
        raise CoreError(f"{ctx.workspace / 'draft'} is not a git repository")


def _dirty(ctx: Context) -> list[str]:
    status = _git(ctx, "status", "--porcelain")
    if status.returncode != 0:
        raise CoreError(f"git status failed: {status.stderr.strip()}")
    return [line for line in status.stdout.splitlines() if line.strip()]


def commit_draft(ctx: Context, message: str) -> dict[str, Any]:
    """Stage and commit every change in the draft repository.

    Args:
        ctx: The resolved context.
        message: The commit message.

    Returns:
        ``{commit, files}`` — the new HEAD and the paths it touched.

    Raises:
        CoreError: If there is no repository, no message, nothing to
            commit, or git fails.
    """
    _require_repo(ctx)
    if not message.strip():
        raise CoreError("a commit needs a non-empty message")
    if not _dirty(ctx):
        raise CoreError("nothing to commit; the draft tree is clean")
    added = _git(ctx, "add", "-A")
    if added.returncode != 0:
        raise CoreError(f"git add failed: {added.stderr.strip()}")
    committed = _git(ctx, "commit", "-q", "-m", message)
    if committed.returncode != 0:
        raise CoreError(f"git commit failed: {committed.stderr.strip()}")
    head = _git(ctx, "rev-parse", "HEAD").stdout.strip()
    files = _git(ctx, "show", "--pretty=", "--name-only", head).stdout.split()
    return {"commit": head, "files": sorted(files)}


def tag_revision(ctx: Context, tag: str, message: str) -> dict[str, Any]:
    """Create the annotated tag for a recorded revision, gated twice.

    Order: the tag must be well-formed and recorded in ``revisions.yaml``,
    the draft tree clean, the strict manifest gate at 0; then the tag is
    created and the strict citation gate runs — on findings the tag is
    deleted again. The citation gate cannot run first: it reports a
    recorded-but-untagged revision as a finding.

    Args:
        ctx: The resolved context.
        tag: The revision tag (``draft-<name>-NN``).
        message: The annotated tag message.

    Returns:
        ``{exit_code, tag, commit?, stage?, findings, rolled_back}``; a
        non-zero ``exit_code`` names the ``stage`` that refused.

    Raises:
        CoreError: On a malformed or unrecorded tag, a dirty tree, an
            existing tag, or a git failure.
    """
    from panther.plugins.services.testers.ai_rfc.draft.gate import (
        REVISION_TAG,
        GateError,
        load_revisions,
    )

    _require_repo(ctx)
    if not REVISION_TAG.match(tag):
        raise CoreError(f"{tag}: not a revision tag; expected draft-<name>-NN")
    if not message.strip():
        raise CoreError("a tag needs a non-empty message")
    try:
        recorded = {entry.tag for entry in load_revisions(ctx.revisions)}
    except GateError as error:
        raise CoreError(str(error)) from None
    if tag not in recorded:
        raise CoreError(f"{tag} is not in revisions.yaml; record the revision first")
    if _dirty(ctx):
        raise CoreError("the draft tree has uncommitted changes; commit before tagging")
    if _git(ctx, "tag", "-l", tag).stdout.strip():
        raise CoreError(f"tag {tag} already exists")

    manifest = manifest_gate(ctx, strict=True)
    if manifest["exit_code"] != 0:
        return {
            "exit_code": manifest["exit_code"],
            "tag": tag,
            "stage": "manifest_gate",
            "findings": manifest["stderr"],
            "rolled_back": False,
        }
    tagged = _git(ctx, "tag", "-a", tag, "-m", message)
    if tagged.returncode != 0:
        raise CoreError(f"git tag failed: {tagged.stderr.strip()}")
    citation = citation_gate(ctx, strict=True)
    if citation["exit_code"] != 0:
        deleted = _git(ctx, "tag", "-d", tag)
        if deleted.returncode != 0:
            raise CoreError(
                f"citation gate refused {tag} and the tag could not be removed: "
                f"{deleted.stderr.strip()}"
            )
        return {
            "exit_code": citation["exit_code"],
            "tag": tag,
            "stage": "citation_gate",
            "findings": citation["findings"] or citation["stderr"],
            "rolled_back": True,
        }
    commit = _git(ctx, "rev-list", "-n", "1", tag).stdout.strip()
    return {
        "exit_code": 0,
        "tag": tag,
        "commit": commit,
        "findings": [],
        "rolled_back": False,
    }
