---
description: Tag the current draft state as the next revision after all gates pass
---

Release the current draft state of `$ARFC_WORKSPACE` as revision `-NN`
(next number, monotone in cluster ordinal):

1. Preconditions, all hard: the draft repo tree is clean; the strict
   manifest gate exits 0; the citation gate exits 0. Any failure stops the
   release — report the findings verbatim and do not work around them.
2. Confirm `revisions.yaml` carries the entry for this tag (cluster id,
   checkpoint sha, explicit `normative_change`, note); write it if the loop
   left it pending.
3. Create the **annotated** tag `draft-<name>-NN` with a message naming the
   cluster.
4. Re-run the citation gate strict (the tag bijection check now covers the
   new tag) — exit 0 closes the release.

Building txt/html via the template's Makefile is optional; when asked for
it, follow the draft repo's own CLAUDE.md.
