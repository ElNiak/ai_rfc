# The reconstruction loop

One iteration turns one timeline cluster into evidence-honest claims and,
when it changed normative behaviour, a new draft revision. Work through the
clusters in ordinal order; never skip silently. Finish the cluster you
were given: claims recorded, checkpoint written, prose committed when the
change is normative, and a revision recorded and tagged. Claims without a
tagged revision leave the reconstruction incomplete however good they are,
so never stop before the tag.

{{guidance}}

{{preamble}}

## Preconditions

- `AI_RFC_WORKSPACE` is set; {{runtime}}.
- The workspace holds `corpus/`, `timeline/`, `clusters/` and the pinned
  `clone/`. When a forge snapshot exists, the timeline MUST have been built
  with `--forge` **before any checkpoint is written**. Forge data
  restructures cluster ids, and checkpoints pin them.
- Use only the tools offered. Never hand-edit the claim register, the
  question register or any interview transcript; every change goes through
  a verb. Never shell out to reach what a verb already does.

## What a good iteration produces

A small set of claims, each stating only what the cluster's own diff
shows, anchored to a file that cluster changed, and cited once in a
normative sentence carrying that claim's keyword. One claim the diff
proves beats three it only suggests. Spread claims across the cluster's
changed source files, one or two per file, so the claims cover what the
cluster changed rather than one corner of it. Spend turns on reading the
diff, not on re-running tools: batch independent calls into one turn, and
never repeat a call whose result you already hold.

## One iteration

1. **Pick the next cluster**: {{cluster_next}}.
2. **Read its evidence once**: {{cluster_get}}. Write down the changed file
   paths exactly as the evidence prints them, and the member commit shas;
   anchors use those paths at those commits (no guessed prefixes such as
   `src/`). Skip test, fixture and build files as anchors unless nothing
   else changed. Query the corpus only when the diff alone cannot tell you
   what a changed line does: {{corpus_query}}.
3. **Mine claims**: behaviours the cluster introduces or changes, each with
   pinned anchors on a file the cluster changed (a member commit is the
   pin, and `line` is a line the diff added or changed) and NO `status`:
   {{claim_upsert}}. Before each upsert, check the claim text against the
   diff line by line (see evidence hygiene: *Scope the claim to the
   diff*). A commit message stating a decision is an `adr` anchor; PR
   discussion explaining intent supports `intent:` but is not itself an
   anchor class. Issue all independent upserts together in one turn.
3b. If this cluster defines or changes a wire format, message, record,
    enumeration or state machine, register it, binding every field, value
    or transition to a claim you just recorded: {{structure_upsert}}
    Then render the block and paste it into its owning section:
    {{draft_render}}. Skip this step when the cluster describes only
    behaviour.
4. **Lint once, after all claims exist**: {{lint}}. Fix every unverified
   anchor (wrong paths, commits, lines) in one pass, then lint again only
   if you changed something. If an anchor cannot be verified, narrow or
   drop that claim rather than moving it onto an untouched file.
5. **Record statuses**: {{record_status}}. Record exactly the supported
   value the report shows, never more. Then the strict gate: {{gate}}.
   Exit 0 is the bar.
6. **Decide spec relevance**:
   - Normative behaviour changed → write the prose in one edit per
     section, per the RFC-style rules. Every new claim gets exactly one
     normative sentence that states it, carries the keyword its `level`
     maps to (that exact keyword, capitalised) and ends with its backticked
     citation. That includes structure claims: a table or figure needs its
     own sentence such as "Senders MUST use the values in Table 2
     `claim-id`." Prose goes to the owning section (Protocol Operation,
     Data Model and Structures, Configuration and Defaults, Error
     Handling), never the Introduction. Before committing, check the list
     of new claim ids against the prose: each appears once, in a sentence
     with its own keyword.
   - Nothing normative → no prose edit; the revision entry will say so.
7. **Checkpoint**: {{checkpoint}}.
8. **Record, build and tag the revision**: {{revision_record}}. Record the
   tag `draft-<name>-NN` (two digits, monotone in cluster ordinal), the
   cluster id, an explicit `normative_change` and a one-line note. Commit
   any prose change ({{draft_commit}}), then build it: {{draft_build}}.
   Fix any finding it reports (long lines, non-ASCII, unused or undeclared
   references) before tagging. Then create the annotated tag
   ({{revision_tag}}). Every revision entry needs its tag, no-change
   revisions included.
9. **Gate**: {{citation_gate}}. Exit 0 before advancing.
10. **Open questions**: a claim stuck at `gap`/`inferred` that blocks a
    section gets a question: {{question_draft}}.

## Failure recovery

| Failure | Response |
|---|---|
| Strict gate exit 3 | The system working. Fix anchors first (weakest link), re-adjudicate, re-run. Never hand-edit a status upward, never bypass. |
| `StaleIndexError` | Rebuild the index (`build_index`), never migrate. If the corpus itself moved, STOP, because every anchor needs re-verification. |
| Citation-gate finding | Reconcile prose or claims. Never delete a claim to silence a citation. |
| Checkpoint refused (exists) | The cluster was processed; re-running is a new decision, so investigate before deleting anything. |
| Build finding | Fix the prose or front matter, commit, rebuild once; never run `make` or edit template files. |
| Giant epoch cluster | Mine the changes whose diff lines plainly show the behaviour, one or two per changed source file; claims may cover a subset, since understatement is safe. |
| Clone HEAD ≠ corpus tip | Someone moved the clone. Restore the pin; never re-anchor to the new HEAD. |
