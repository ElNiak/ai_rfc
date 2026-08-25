---
name: arfc-interviewing
description: Drafting author questions from gap and inferred claims, exporting them, and importing answers as interview anchors and sign-offs. Use when a claim needs author input, when preparing a question batch, or when ingesting an author's reply transcript.
---

# The author-feedback loop

Questions turn an author's knowledge into citable evidence. The register
(`$ARFC_WORKSPACE/questions.yaml`) is strict: an answered question without
its answer fails to load, and a question tied to no claim is refused.

When the `arfc` MCP server is connected, use `arfc_question_draft`,
`arfc_question_export` and `arfc_answer_record` (or the `arfc` CLI verbs) —
they enforce the verbatim-quote and exact-wording guardrails up front.

## When to draft a question

A claim capped at `gap` or `inferred` that blocks a draft section — the
evidence cannot say whether the behaviour is intended, or two narrative
sources need a primary corroboration the author can give.

## Question quality

- One behaviour per question.
- Quote the claim text **verbatim** inside the question — the eventual
  sign-off is on exact wording, so the author must see exact wording.
- Answerable with yes / no / a correction; never "tell me about X".
- Register entry: fresh `q-NNN` id, `claim_ids` listing every claim it
  unblocks, `status: open`, `asked_at` today.

## Export

Render every `open` question into one markdown bundle (id, question,
affected claim ids) for whatever channel reaches the author — email, issue
text, a call agenda. The register is channel-agnostic; only ids round-trip.

## Import

1. Save the author's reply as `$ARFC_WORKSPACE/interviews/int-NNN.md`,
   dated and attributed, verbatim.
2. For each answered question id:
   - Attach an `interview` anchor to each affected claim:
     `evidence_class: interview`, `locator: int-NNN`.
   - Update the register entry: `status: answered`, `answer` (the author's
     words), `answered_by`, `answered_at`.
3. **Sign-off rule**: record `signed_off_by` on a claim ONLY when the
   author explicitly confirmed the claim's exact wording. "Roughly yes" or
   a paraphrase earns the interview anchor, not the sign-off — the honest
   route to `confirmed` is then interview + code through the two-class
   rule, which adjudication computes on its own. This rule is the defense
   against the circularity the substrate exists to detect; relaxed once,
   `checked_fraction` stops meaning anything.
4. Re-run the linter, record newly supported statuses, re-gate.

## An unanswered question is information

`withdrawn` records a question that stopped mattering; `open` stays open
across revisions. Neither blocks a checkpoint — but a claim whose
`question-id` is missing from the register is a gate finding, so never
delete register entries that claims still reference.
