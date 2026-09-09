# Consolidation round

The draft has grown one cluster at a time and now reads like the order it was
written in. This round changes no claim and adds no evidence. It makes the
document read like a specification.

Work only from what is already in the manifest and the draft. Do not open the
corpus, do not adjudicate, do not answer questions. If you find yourself wanting
a fact that is not already a claim, stop and record it as a question instead.

1. Read every revision recorded since the last consolidation, and the draft as
   it stands: {{revisions_since}}
2. Declare any structure the clusters described in prose but never registered —
   a format, message, record, enumeration or state machine whose fields you can
   each bind to an existing claim: {{structure_upsert}}
3. Render the structures and paste each block verbatim into its owning section,
   delimiters included: {{draft_render}}
4. The editorial pass. In this order:
   - Rewrite the abstract so it says what the protocol does, not how the
     document was produced.
   - Rewrite the Introduction: scope, method, organization. No cluster
     ordinals, no counts of statements added or withdrawn, no "this revision".
   - Regroup the body by concern, not by the order clusters were processed.
   - Move every per-cluster narration sentence into Appendix A, Change Log.
   - Move implementation trivia into Appendix B, Implementation Notes.
   - **Move, never drop.** A sentence you cannot place goes to an appendix. A
     citation that disappears is a normative change, and this round is not one.
   - Add the references the body already relies on, and a figure where a
     reader would otherwise have to imagine the shape. A figure's caption
     cites the claims it depicts.
5. Freeze a consolidation checkpoint. Its requirements must be byte-identical
   to the checkpoint you consolidate from; only the structures may
   differ: {{checkpoint_consolidation}}
6. Record the revision as a consolidation, commit, build and tag:
   {{revision_record_consolidation}}
   {{draft_commit}}
   {{revision_tag}}
7. Lint the result and fix what it finds: {{draft_lint}}

`normative_change` is `false` for this round, and it must not lose a citation the
previous revision carried. The gate rejects drops; adding a citation is allowed.
