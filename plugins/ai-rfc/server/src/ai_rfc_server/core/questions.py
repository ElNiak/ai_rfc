"""Question-register operations and the interview-import guardrails."""

from __future__ import annotations

import datetime
from typing import Any

from ..paths import Context
from . import CoreError, GuardrailError
from .claims import _atomic_write, _document, _normalize_and_write


def _register(ctx: Context):  # noqa: ANN202
    del ctx
    from panther.plugins.services.testers.ai_rfc.draft import questions as register

    return register


def draft_question(
    ctx: Context,
    question: str,
    claim_ids: list[str],
    question_id: str | None = None,
    asked_at: str | None = None,
) -> dict[str, Any]:
    """Append an open question tied to existing claims.

    Args:
        ctx: The resolved context.
        question: The question text; quote the claim wording verbatim.
        claim_ids: The claims this question would unblock; all must exist.
        question_id: Explicit id; ``None`` takes the next free ``q-NNN``.
        asked_at: ISO date; ``None`` uses today.

    Returns:
        ``{id, question, claim_ids, linked}`` — ``linked`` names the claims
        whose ``question-id`` was set (claims already carrying a different
        question keep it and are reported unlinked).

    Raises:
        CoreError: If a claim does not exist.
        GuardrailError: If an open question already covers all these claims.
    """
    register = _register(ctx)
    existing = list(register.load_questions(ctx.questions))

    document = _document(ctx)
    requirements = document["requirements"]
    missing = [claim_id for claim_id in claim_ids if claim_id not in requirements]
    if missing:
        raise CoreError(f"no such claim(s): {', '.join(sorted(missing))}")

    for entry in existing:
        if entry.status.value == "open" and set(claim_ids) <= set(entry.claim_ids):
            raise GuardrailError(
                f"open question {entry.id} already covers "
                f"{', '.join(sorted(claim_ids))}; answer or withdraw it "
                f"instead of asking twice"
            )

    if question_id is None:
        taken = {entry.id for entry in existing}
        number = 1
        while f"q-{number:03d}" in taken:
            number += 1
        question_id = f"q-{number:03d}"

    entry = register.Question(
        id=question_id,
        question=question.strip(),
        claim_ids=tuple(claim_ids),
        status=register.QuestionStatus.OPEN,
        asked_at=asked_at or datetime.date.today().isoformat(),
    )
    _atomic_write(
        ctx.questions, register.dump_questions([*existing, entry])
    )

    linked = []
    for claim_id in claim_ids:
        if requirements[claim_id].get("question-id") in (None, question_id):
            requirements[claim_id]["question-id"] = question_id
            linked.append(claim_id)
    _normalize_and_write(ctx, document)
    return {
        "id": question_id,
        "question": entry.question,
        "claim_ids": list(claim_ids),
        "linked": linked,
    }


def export_open(ctx: Context) -> str:
    """Render every open question as one markdown bundle for the author."""
    register = _register(ctx)
    lines = ["# Questions for the implementation's authors", ""]
    open_entries = [
        entry
        for entry in register.load_questions(ctx.questions)
        if entry.status is register.QuestionStatus.OPEN
    ]
    if not open_entries:
        return "No open questions.\n"
    for entry in open_entries:
        lines += [
            f"## {entry.id}",
            "",
            entry.question,
            "",
            f"_Claims affected: {', '.join(entry.claim_ids)}_",
            "",
        ]
    return "\n".join(lines)


def record_answer(
    ctx: Context,
    question_id: str,
    answer: str,
    answered_by: str,
    transcript: str,
    quote: str,
    author_confirmed_exact_text: bool = False,
    answered_at: str | None = None,
) -> dict[str, Any]:
    """Ingest one answered question from a saved interview transcript.

    Args:
        ctx: The resolved context.
        question_id: The register entry being answered.
        answer: The author's answer, in their words.
        answered_by: Who answered.
        transcript: Transcript filename under ``interviews/`` (e.g.
            ``int-001.md``); must already be saved.
        quote: A verbatim span that must appear in the transcript — the
            evidence the answer actually happened.
        author_confirmed_exact_text: True ONLY when the author confirmed
            the exact claim wording; grants ``signed_off_by``. A paraphrase
            earns the interview anchor, never the sign-off.
        answered_at: ISO date; ``None`` uses today.

    Returns:
        ``{question_id, claims, anchored, signed_off}``.

    Raises:
        CoreError: If the question or transcript is missing.
        GuardrailError: If ``quote`` is not found verbatim in the
            transcript.
    """
    register = _register(ctx)
    entries = list(register.load_questions(ctx.questions))
    by_id = {entry.id: entry for entry in entries}
    if question_id not in by_id:
        raise CoreError(f"no question {question_id} in the register")

    transcript_path = ctx.workspace / "interviews" / transcript
    if not transcript_path.exists():
        raise CoreError(
            f"transcript {transcript_path} does not exist; save the "
            f"author's reply verbatim before recording answers"
        )
    if quote not in transcript_path.read_text():
        raise GuardrailError(
            f"the quote is not found verbatim in {transcript}; an answer "
            f"that cannot be pointed at in the transcript is not evidence"
        )

    target = by_id[question_id]
    updated = register.Question(
        id=target.id,
        question=target.question,
        claim_ids=target.claim_ids,
        status=register.QuestionStatus.ANSWERED,
        asked_at=target.asked_at,
        answer=answer.strip(),
        answered_by=answered_by,
        answered_at=answered_at or datetime.date.today().isoformat(),
    )
    replaced = [updated if entry.id == question_id else entry for entry in entries]
    _atomic_write(ctx.questions, register.dump_questions(replaced))

    document = _document(ctx)
    locator = transcript.rsplit(".", 1)[0]
    anchored, signed_off = [], []
    for claim_id in target.claim_ids:
        body = document["requirements"].get(claim_id)
        if body is None:
            continue
        anchors = body.setdefault("anchors", [])
        if not any(
            anchor.get("evidence_class") == "interview"
            and anchor.get("locator") == locator
            for anchor in anchors
        ):
            anchors.append({"evidence_class": "interview", "locator": locator})
            anchored.append(claim_id)
        if author_confirmed_exact_text:
            body["signed_off_by"] = answered_by
            signed_off.append(claim_id)
    _normalize_and_write(ctx, document)
    return {
        "question_id": question_id,
        "claims": list(target.claim_ids),
        "anchored": anchored,
        "signed_off": signed_off,
    }
