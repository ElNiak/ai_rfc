---
description: One-screen reconstruction status — clusters processed, claim standings, open questions, last revision
---

Report the state of the workspace at `$ARFC_WORKSPACE`, computed from the
substrate's own artifacts (never re-derived by hand):

- **Timeline**: cluster counts by kind and provenance from
  `timeline/timeline.json` and `clusters.jsonl`; forge snapshot recorded or
  git-only.
- **Progress**: processed clusters (checkpoints + revision entries) over
  the total; the next unprocessed ordinal.
- **Claims**: `count_by_status`, `promotable_count` and
  `checked_fraction_by_req_class` from `out/report.json` (run the linter
  first if the report is stale or missing).
- **Questions**: open / answered / withdrawn counts from `questions.yaml`.
- **Draft**: latest tag (`git describe --tags` in `draft/`), revision count,
  and whether the citation gate is currently clean.

Present it as a short table plus one sentence naming the next action.
