---
name: ai-rfc-editorial
description: Use during a consolidation round, when the draft must be reorganised into a specification without changing what it claims.
---

# Editorial

A consolidation round changes how the document reads, never what it asserts.
You are reorganising evidence that is already adjudicated.

## The rule that governs everything

**Move, never drop.** Every sentence you remove from the body goes somewhere:
narration to Appendix A, Change Log; implementation trivia to Appendix B,
Implementation Notes. If a sentence belongs nowhere, it is telling you the
document is missing a section — add the section.

A citation that disappears is a normative change, and this round records
`normative_change: false`. The citation set after your edit must equal the set
before it, and the gate checks. If you genuinely believe a claim should no
longer be cited, stop: that is a cluster round's decision, not yours.

## The order

1. **Abstract.** Say what the protocol does. A reader should not learn from it
   that the document was reconstructed.
2. **Introduction.** Scope, method, organization. Strip every cluster ordinal,
   every count of statements added or withdrawn, every "this revision".
3. **Body.** Regroup by concern. The order clusters were processed is an
   artefact of how the work happened and means nothing to a reimplementer.
4. **Appendices.** Change Log first, Implementation Notes second.
5. **Figures and references.** Add a figure where a reader would otherwise have
   to imagine a shape; its caption cites the claims it depicts. Add the
   references the body already relies on.

## What you may not do

Do not adjudicate, do not open the corpus, do not answer an open question, do
not add a claim. If you want a fact that is not already a claim, record a
question and leave the prose alone.
