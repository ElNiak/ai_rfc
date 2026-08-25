---
description: Import an author reply transcript as interview anchors, answers, and (when exact) sign-offs
---

Import the author reply named by `$ARGUMENTS` (a file path, or text to be
saved) into the workspace at `$ARFC_WORKSPACE`, following the
`arfc-interviewing` skill exactly:

1. Save the transcript verbatim as `interviews/int-NNN.md` (next free NNN),
   dated and attributed.
2. Walk the answered question ids: attach `interview` anchors
   (`locator: int-NNN`) to every affected claim, and update each register
   entry to `answered` with the author's words, `answered_by` and
   `answered_at`.
3. Apply the sign-off rule strictly: `signed_off_by` only where the author
   confirmed the exact claim wording; quote the confirming sentence when
   you record one.
4. Re-run the linter, record newly supported statuses, and re-run the
   strict gate.

End by summarising: questions answered, anchors added, sign-offs granted
(with the confirming quotes) and refused (with why), and every claim whose
status moved.
