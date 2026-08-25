"""The MCP tool surface, importable without the ``mcp`` package.

Each function here is one tool: it resolves the environment contract and
calls exactly one core operation. :mod:`ai_rfc_server.server` registers
these callables with FastMCP; the parity tests call them directly, so the
tool arm and the CLI arm are compared function-for-function even where the
``mcp`` runtime is not installed.
"""

from __future__ import annotations

from typing import Any

from .core import claims, gates, queries, questions, revisions
from .paths import resolve_context


def arfc_status() -> dict[str, Any]:
    """Composite workspace status, quoted from the substrate's artifacts."""
    return queries.status(resolve_context())


def arfc_corpus_query(sql: str) -> list[dict[str, Any]]:
    """Run one SELECT over the corpus index (at most 200 rows)."""
    return queries.corpus_query(resolve_context(), sql)


def arfc_cluster_next() -> dict[str, Any] | None:
    """The lowest-ordinal cluster with neither checkpoint nor revision."""
    return queries.cluster_next(resolve_context())


def arfc_cluster_get(
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


def arfc_claim_upsert(claim_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Add or update a claim; ``status`` is never accepted — it is adjudicated."""
    return claims.upsert_claim(resolve_context(), claim_id, fields)


def arfc_claim_adjudicate() -> list[dict[str, Any]]:
    """Every claim's stored status beside what its evidence supports."""
    return claims.adjudicate_preview(resolve_context())


def arfc_claim_record_status(
    claim_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Set stored statuses to exactly the supported values; returns changes."""
    return claims.record_statuses(resolve_context(), claim_ids)


def arfc_question_draft(
    question: str, claim_ids: list[str], question_id: str | None = None
) -> dict[str, Any]:
    """Draft an open author question tied to existing claims."""
    return questions.draft_question(
        resolve_context(), question, claim_ids, question_id=question_id
    )


def arfc_question_export() -> str:
    """Render every open question as one markdown bundle."""
    return questions.export_open(resolve_context())


def arfc_answer_record(
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


def arfc_revision_record(
    tag: str, cluster_id: str, normative_change: bool, note: str
) -> dict[str, Any]:
    """Record a revision entry pinned to its on-disk checkpoint."""
    return revisions.record_revision(
        resolve_context(), tag, cluster_id, normative_change, note
    )


def arfc_checkpoint(cluster_id: str) -> dict[str, Any]:
    """Freeze the manifest against one cluster (exit code surfaced raw)."""
    return gates.write_checkpoint(resolve_context(), cluster_id)


def arfc_gate(strict: bool = False) -> dict[str, Any]:
    """Run the manifest gate; strict exit 2 is information, never bypassed."""
    return gates.manifest_gate(resolve_context(), strict=strict)


def arfc_citation_gate(strict: bool = False) -> dict[str, Any]:
    """Run the draft citation gate over the revision map."""
    return gates.citation_gate(resolve_context(), strict=strict)


#: Every tool, in the order they appear in docs/parity.md.
ALL_TOOLS = (
    arfc_status,
    arfc_corpus_query,
    arfc_cluster_next,
    arfc_cluster_get,
    arfc_claim_upsert,
    arfc_claim_adjudicate,
    arfc_claim_record_status,
    arfc_question_draft,
    arfc_question_export,
    arfc_answer_record,
    arfc_revision_record,
    arfc_checkpoint,
    arfc_gate,
    arfc_citation_gate,
)
