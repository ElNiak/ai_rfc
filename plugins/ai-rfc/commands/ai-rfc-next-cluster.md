---
description: Process the next unprocessed timeline cluster through the reconstruction loop
---

Run ONE iteration of the reconstruction loop on the workspace at
`$AI_RFC_WORKSPACE`, following the `ai-rfc-reconstruction-loop` skill exactly:
pick the lowest-ordinal cluster with neither a checkpoint nor a revision
entry, read its evidence, mine claims (never writing `status`), lint and
fix anchors, record supported statuses, revise the draft or record an
explicit no-change revision, checkpoint, and finish with both gates at
exit 0.

If `$ARGUMENTS` names a cluster id, process that cluster instead — but
refuse (and say why) if an earlier cluster is unprocessed, unless the user
explicitly said to skip.

End by summarising: cluster id and title, claims added/updated with their
adjudicated statuses, draft revision tagged or the no-change rationale, and
any questions drafted.
