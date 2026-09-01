---
description: Tag the current draft state as the next revision after all gates pass
---

Release the current draft state of `$AI_RFC_WORKSPACE` as revision `-NN`
(next number, monotone in cluster ordinal):

1. Confirm `revisions.yaml` carries the entry for this tag (cluster id,
   checkpoint sha, explicit `normative_change`, note); record it with
   `ai_rfc_revision_record` / `ai_rfc revision-record` if the loop left it
   pending. The citation gate reports a recorded-but-untagged revision as a
   finding, so it cannot be a precondition here.
2. Commit any prose change (`ai_rfc_draft_commit` / `ai_rfc draft-commit -m …`);
   a dirty tree refuses the tag.
3. Create the tag with `ai_rfc_revision_tag` / `ai_rfc revision-tag TAG -m …`:
   it runs the strict manifest gate first, creates the annotated tag, then
   runs the strict citation gate and deletes the tag again on findings.
   Report every finding verbatim and do not work around any of them.
4. Exit code 0 closes the release.

Building txt/html via the template's Makefile is optional; when asked for
it, follow the draft repo's own CLAUDE.md.
