"""The MCP tool surface, importable without the ``mcp`` package.

Each function here is one tool: it resolves the environment contract and
calls exactly one core operation. :mod:`ai_rfc_server.server` registers
these callables with FastMCP; the parity tests call them directly, so the
tool arm and the CLI arm are compared function-for-function even where the
``mcp`` runtime is not installed.
"""

from __future__ import annotations

from typing import Any

from .core import claims, draft, gates, queries, questions, revisions
from .paths import resolve_context


def ai_rfc_status() -> dict[str, Any]:
    """Composite workspace status, quoted from the substrate's artifacts."""
    return queries.status(resolve_context())


def ai_rfc_corpus_query(sql: str) -> list[dict[str, Any]]:
    """Run one SELECT over the corpus index (at most 200 rows)."""
    return queries.corpus_query(resolve_context(), sql)


def ai_rfc_cluster_next() -> dict[str, Any] | None:
    """The lowest-ordinal cluster with neither checkpoint nor revision."""
    return queries.cluster_next(resolve_context())


def ai_rfc_cluster_get(
    cluster_id: str,
    include_patch: bool = False,
    patch_offset: int = 0,
    patch_limit: int = 20000,
) -> dict[str, Any]:
    """One cluster's view, forge evidence, and optionally a patch slice."""
    return queries.cluster_get(
        resolve_context(),
        cluster_id,
        include_patch=include_patch,
        patch_offset=patch_offset,
        patch_limit=patch_limit,
    )


def ai_rfc_claim_upsert(claim_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Add or update a claim; ``status`` is never accepted — it is adjudicated."""
    return claims.upsert_claim(resolve_context(), claim_id, fields)


def ai_rfc_claim_adjudicate() -> list[dict[str, Any]]:
    """Every claim's stored status beside what its evidence supports."""
    return claims.adjudicate_preview(resolve_context())


def ai_rfc_claim_record_status(
    claim_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Set stored statuses to exactly the supported values; returns changes."""
    return claims.record_statuses(resolve_context(), claim_ids)


def ai_rfc_question_draft(
    question: str, claim_ids: list[str], question_id: str | None = None
) -> dict[str, Any]:
    """Draft an open author question tied to existing claims."""
    return questions.draft_question(
        resolve_context(), question, claim_ids, question_id=question_id
    )


def ai_rfc_question_export() -> str:
    """Render every open question as one markdown bundle."""
    return questions.export_open(resolve_context())


def ai_rfc_answer_record(
    question_id: str,
    answer: str,
    answered_by: str,
    transcript: str,
    quote: str,
    author_confirmed_exact_text: bool = False,
) -> dict[str, Any]:
    """Ingest one answer from a saved transcript; sign-off needs exact wording."""
    return questions.record_answer(
        resolve_context(),
        question_id,
        answer,
        answered_by,
        transcript,
        quote,
        author_confirmed_exact_text=author_confirmed_exact_text,
    )


def ai_rfc_revision_record(
    tag: str, cluster_id: str, normative_change: bool, note: str
) -> dict[str, Any]:
    """Record a revision entry pinned to its on-disk checkpoint."""
    return revisions.record_revision(
        resolve_context(), tag, cluster_id, normative_change, note
    )


def ai_rfc_checkpoint(cluster_id: str) -> dict[str, Any]:
    """Freeze the manifest against one cluster (exit code surfaced raw)."""
    return gates.write_checkpoint(resolve_context(), cluster_id)


def ai_rfc_gate(strict: bool = False) -> dict[str, Any]:
    """Run the manifest gate; strict exit 3 is information, never bypassed."""
    return gates.manifest_gate(resolve_context(), strict=strict)


def ai_rfc_citation_gate(strict: bool = False) -> dict[str, Any]:
    """Run the draft citation gate over the revision map."""
    return gates.citation_gate(resolve_context(), strict=strict)


def ai_rfc_draft_commit(message: str) -> dict[str, Any]:
    """Commit every change in the draft repository; a clean tree is an error."""
    return draft.commit_draft(resolve_context(), message)


def ai_rfc_revision_tag(tag: str, message: str) -> dict[str, Any]:
    """Tag a recorded revision once both strict gates accept it (exit code raw)."""
    return draft.tag_revision(resolve_context(), tag, message)


#: Every tool, in the order they appear in docs/parity.md.
ALL_TOOLS = (
    ai_rfc_status,
    ai_rfc_corpus_query,
    ai_rfc_cluster_next,
    ai_rfc_cluster_get,
    ai_rfc_claim_upsert,
    ai_rfc_claim_adjudicate,
    ai_rfc_claim_record_status,
    ai_rfc_question_draft,
    ai_rfc_question_export,
    ai_rfc_answer_record,
    ai_rfc_revision_record,
    ai_rfc_checkpoint,
    ai_rfc_gate,
    ai_rfc_citation_gate,
    ai_rfc_draft_commit,
    ai_rfc_revision_tag,
)
