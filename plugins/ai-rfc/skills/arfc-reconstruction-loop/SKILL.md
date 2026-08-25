---
name: arfc-reconstruction-loop
description: The cluster-by-cluster reconstruction driver — read evidence, mine claims, adjudicate, revise the draft, gate, checkpoint, advance. Use when processing timeline clusters of a reconstruction workspace or when asked to continue a reconstruction.
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python -m panther.plugins.services.testers.a_rfc*), Bash(git *), Bash(arfc *)
---

# The reconstruction loop

One iteration turns one timeline cluster into evidence-honest claims and,
when it changed normative behaviour, a new draft revision. Work through the
clusters in ordinal order; never skip silently.

Load `arfc-evidence-hygiene` before touching claims and `arfc-rfc-style`
before touching prose.

## Preconditions

- `PANTHER_REPO` and `ARFC_WORKSPACE` are set; commands below run from
  `$PANTHER_REPO` with `PY` an interpreter that imports `panther`.
- The workspace holds `corpus/`, `timeline/`, `clusters/` and the pinned
  `clone/`. When a forge snapshot exists, the timeline MUST have been built
  with `--forge` **before any checkpoint is written** — forge data
  restructures cluster ids, and checkpoints pin them.

## One iteration

1. **Pick the next cluster**: the lowest ordinal in
   `timeline/clusters.jsonl` that has neither a checkpoint under
   `checkpoints/` nor an entry in `revisions.yaml`.
2. **Read its evidence**: `clusters/<id>/view.json` (file set, PR number),
   `span.diff`, `evidence/pr.json` (title, body, reviews, discussion) when
   present. For context beyond the cluster, query the corpus index —
   churn-ranked reading beats the directory tree:

   ```python
   from panther.plugins.services.testers.a_rfc.history.index import open_index
   conn = open_index(Path("$ARFC_WORKSPACE/corpus"))
   conn.execute("SELECT path, COUNT(*) c FROM file_changes GROUP BY path ORDER BY c DESC LIMIT 20")
   ```

3. **Mine claims** into `$ARFC_WORKSPACE/manifest.yaml`: behaviours the
   cluster introduces or changes, each with pinned anchors (the cluster's
   member commits are the natural pins) and NO `status`. A commit message
   stating a decision is an `adr` anchor; PR discussion explaining intent
   supports `intent:` but is not itself an anchor class.
4. **Lint**: `$PY -m panther.plugins.services.testers.a_rfc
   $ARFC_WORKSPACE/manifest.yaml --out $ARFC_WORKSPACE/out --repo
   $ARFC_WORKSPACE/clone` — fix every `unverified:` line (wrong paths,
   wrong commits, wrong lines) BEFORE anything is built on top.
5. **Record statuses**: read `out/report.json`'s `claims` payload and set
   each claim's `status` to exactly its `supported` value. Re-run with
   `--strict`; exit 0 is the bar.
6. **Decide spec relevance**:
   - Normative behaviour changed → update the draft per `arfc-rfc-style`,
     citing the new/changed claims.
   - Nothing normative → no prose edit; the revision entry will say so.
7. **Checkpoint**: `$PY -m panther.plugins.services.testers.a_rfc.draft
   checkpoint $ARFC_WORKSPACE/manifest.yaml --timeline
   $ARFC_WORKSPACE/timeline --cluster <id> --out
   $ARFC_WORKSPACE/checkpoints`.
8. **Record the revision** in `revisions.yaml` (tag, cluster id, the
   checkpoint's `manifest_sha256`, explicit `normative_change`, note); for
   normative changes commit the draft and add the annotated tag
   `draft-<name>-NN`.
9. **Gate**: `$PY -m panther.plugins.services.testers.a_rfc.draft gate
   $ARFC_WORKSPACE/draft --timeline … --checkpoints … --questions
   $ARFC_WORKSPACE/questions.yaml --revisions $ARFC_WORKSPACE/revisions.yaml
   --out $ARFC_WORKSPACE/out --strict` — exit 0 before advancing.
10. **Open questions**: any claim stuck at `gap`/`inferred` that blocks a
    section gets a question drafted per `arfc-interviewing`.

## Failure recovery

| Failure | Response |
|---|---|
| Strict gate exit 2 | The system working. Fix anchors first (weakest link), re-adjudicate, re-run. Never hand-edit a status upward, never bypass. |
| `StaleIndexError` | Rebuild the index (`build_index`), never migrate. If the corpus itself moved, STOP — every anchor needs re-verification. |
| Citation-gate finding | Reconcile prose or claims. Never delete a claim to silence a citation. |
| Checkpoint refused (exists) | The cluster was processed; re-running is a new decision — investigate before deleting anything. |
| Giant epoch cluster | Read paginated, mine what you can carry; claims may cover a subset — understatement is safe. |
| Clone HEAD ≠ corpus tip | Someone moved the clone. Restore the pin; never re-anchor to the new HEAD. |
