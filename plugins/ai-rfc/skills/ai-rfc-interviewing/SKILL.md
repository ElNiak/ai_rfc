---
name: ai-rfc-interviewing
description: Drafting author questions from gap and inferred claims, exporting them, and importing answers as interview anchors and sign-offs. Use when a claim needs author input, when preparing a question batch, or when ingesting an author's reply transcript.
---

# The author-feedback loop

Questions turn an author's knowledge into citable evidence. The register
(`$AI_RFC_WORKSPACE/questions.yaml`) is strict: an answered question without
its answer fails to load, and a question tied to no claim is refused.

When the `ai_rfc` MCP server is connected, use `ai_rfc_question_draft`,
`ai_rfc_question_export` and `ai_rfc_answer_record` (or the `ai_rfc` CLI verbs).
They enforce the verbatim-quote and exact-wording guardrails up front. Never
edit the register or a transcript by hand.

## When to draft a question

A claim capped at `gap` or `inferred` that blocks a draft section: the
evidence cannot say whether the behaviour is intended, or two narrative
sources need a primary corroboration the author can give. When the
workspace already holds claims and a transcript answering them, every
such claim gets its question, even if the answer came first.

## Question quality

- Exactly one question per claim, one behaviour per question. Never a
  second question for a claim that already has one.
- Quote the claim text **verbatim** inside the question, copied from the
  register, not retyped. The eventual sign-off is on exact wording, so
  the author must see exact wording.
- Answerable with yes / no / a correction; never "tell me about X".
- Register entry: fresh `q-NNN` id, `claim_ids` listing the claim it
  unblocks, `status: open`, `asked_at` today.

## Export

Render every `open` question into one markdown bundle (id, question,
affected claim ids) for whatever channel reaches the author: email, issue
text, a call agenda. The register is channel-agnostic; only ids round-trip.

## Import

1. The author's reply lives at `$AI_RFC_WORKSPACE/interviews/int-NNN.md`,
   dated, attributed and verbatim. If the transcript already exists, read
   it and never alter it, not even to fix typos or formatting. Only a new
   reply is saved, once.
2. For each answered question id:
   - Record the answer through the answer verb, quoting the author's words
     from the transcript: `status: answered`, `answer`, `answered_by`,
     `answered_at`.
   - Attach an `interview` anchor to each affected claim:
     `evidence_class: interview`, `locator: int-NNN`. Every answered claim
     gets this anchor, whether or not it is signed off.
3. **Sign-off rule**: record `signed_off_by` on a claim ONLY when the
   author's recorded answer repeats the claim's wording **word for word**.
   Compare the two texts before deciding, token by token. A bare "yes",
   "roughly yes", a paraphrase, a reordering, a synonym, a narrowed or
   widened scope, or any correction earns the interview anchor and never
   the sign-off. When unsure, do not sign off; omission is always safe.
   The honest route to `confirmed` is then interview + code through the
   two-class rule, which adjudication computes on its own. This rule is
   the defense against the circularity the substrate exists to detect;
   relaxed once, `checked_fraction` stops meaning anything.
4. Re-run the linter, record newly supported statuses, re-gate.

## Checklist per claim

Question drafted with the verbatim quote; answer recorded from the
transcript; interview anchor attached; sign-off only on an exact repeat.
Work all claims in parallel turns, then lint and gate once.

## An unanswered question is information

`withdrawn` records a question that stopped mattering; `open` stays open
across revisions. Neither blocks a checkpoint. A claim whose `question-id`
is missing from the register is a gate finding, however, so never delete
register entries that claims still reference.
