# The reconstruction loop

One iteration turns one timeline cluster into evidence-honest claims and,
when it changed normative behaviour, a new draft revision. Work through the
clusters in ordinal order; never skip silently.

{{guidance}}

{{preamble}}

## Preconditions

- `AI_RFC_WORKSPACE` is set; {{runtime}}.
- The workspace holds `corpus/`, `timeline/`, `clusters/` and the pinned
  `clone/`. When a forge snapshot exists, the timeline MUST have been built
  with `--forge` **before any checkpoint is written** — forge data
  restructures cluster ids, and checkpoints pin them.

## One iteration

1. **Pick the next cluster**: {{cluster_next}}.
2. **Read its evidence**: {{cluster_get}}. For context beyond the cluster,
   query the corpus index — churn-ranked reading beats the directory tree:
   {{corpus_query}}.
3. **Mine claims**: behaviours the cluster introduces or changes, each with
   pinned anchors (the cluster's member commits are the natural pins) and
   NO `status`: {{claim_upsert}}. A commit message stating a decision is an
   `adr` anchor; PR discussion explaining intent supports `intent:` but is
   not itself an anchor class.
4. **Lint**: {{lint}} — fix every unverified anchor (wrong paths, wrong
   commits, wrong lines) BEFORE anything is built on top.
5. **Record statuses**: {{record_status}}. Then the strict gate:
   {{gate}} — exit 0 is the bar.
6. **Decide spec relevance**:
   - Normative behaviour changed → update the draft per the RFC-style
     rules, citing the new/changed claims.
   - Nothing normative → no prose edit; the revision entry will say so.
7. **Checkpoint**: {{checkpoint}}.
8. **Record and tag the revision**: {{revision_record}} — the tag
   `draft-<name>-NN` (two digits, monotone in cluster ordinal), the cluster
   id, an explicit `normative_change`, a one-line note. Commit any prose
   change ({{draft_commit}}), then create the annotated tag
   ({{revision_tag}}). Every revision entry needs its tag, no-change
   revisions included.
9. **Gate**: {{citation_gate}} — exit 0 before advancing.
10. **Open questions**: any claim stuck at `gap`/`inferred` that blocks a
    section gets a question: {{question_draft}}.

## Failure recovery

| Failure | Response |
|---|---|
| Strict gate exit 3 | The system working. Fix anchors first (weakest link), re-adjudicate, re-run. Never hand-edit a status upward, never bypass. |
| `StaleIndexError` | Rebuild the index (`build_index`), never migrate. If the corpus itself moved, STOP — every anchor needs re-verification. |
| Citation-gate finding | Reconcile prose or claims. Never delete a claim to silence a citation. |
| Checkpoint refused (exists) | The cluster was processed; re-running is a new decision — investigate before deleting anything. |
| Giant epoch cluster | Read paginated, mine what you can carry; claims may cover a subset — understatement is safe. |
| Clone HEAD ≠ corpus tip | Someone moved the clone. Restore the pin; never re-anchor to the new HEAD. |
