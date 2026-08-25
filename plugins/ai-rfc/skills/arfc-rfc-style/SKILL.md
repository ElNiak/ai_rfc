---
name: arfc-rfc-style
description: Internet-Draft prose discipline for reconstructed specifications — RFC 2119 keyword mapping, claim-citation tokens, revision tagging on the auto-i-d-template. Use when writing or revising the draft document of a reconstruction workspace.
---

# RFC prose for a reconstructed specification

The draft lives in `$ARFC_WORKSPACE/draft/`, a git repository scaffolded
from `ElNiak/auto-i-d-template` (kramdown-rfc markdown, Makefile-driven).
The prose is yours; every claim of fact in it is not — it must cite a claim
the paired checkpoint manifest holds, and the citation gate verifies that
mechanically.

## Document structure

One source file `draft-<name>.md` at the repo root (exactly one — the gate
refuses zero or several). kramdown-rfc front matter (`title`, `abbrev`,
`docname: draft-<name>-latest`, `category`, `author`), then `--- abstract`,
`--- middle`, `--- back`. Include the BCP 14 boilerplate in a Conventions
section: `{::boilerplate bcp14-tagged}`.

## Mapping claim levels to RFC 2119 keywords

A normative statement's keyword comes from the cited claim's `level`:
`MUST`/`MUST NOT`, `SHOULD`/`SHOULD NOT`, `MAY` — used only in normative
sections, capitalised, one behaviour per sentence. Prose may be weaker than
the claim's level, never stronger.

## Accidental behaviour is never normative

A claim with `intent: accidental` records a defect the history marks as
unintended. It appears ONLY in a descriptive section (for example
"Observed Accidental Behaviour"), introduced as such, so that recorded
defects never become requirements. The substrate's report renderer makes
the same split; the prose must not undo it.

## Claim citations

Every normative statement carries a backticked token naming its claim:

```
Evidences MUST be presented in decreasing score order. `a_rfc:mark:proto.1`
```

The gate extracts these tokens and verifies each against the checkpoint
manifest paired with the revision. The exact convention, with dos and
don'ts, is in `references/claim-citation.md` — read it before writing
prose.

## Revisions

- One revision per spec-relevant cluster: extend the prose, commit, then
  tag with an **annotated** tag `draft-<name>-NN` (two digits, monotone in
  cluster ordinal).
- Record every revision in `$ARFC_WORKSPACE/revisions.yaml`:
  `cluster_id`, `checkpoint_manifest_sha256` (from the checkpoint's
  `checkpoint.json`), an explicit boolean `normative_change`, and a
  one-line `note`.
- A cluster that changes nothing normative still gets a revision entry with
  `normative_change: false` and a rationale — an auditable no-change
  marker, not a silent skip. Its citation set must equal the previous
  revision's; the gate checks.

## Build mechanics belong to the template

`make`, `make lint`, `make idnits`, datatracker upload and gh-pages are the
template's machinery — follow the draft repo's own CLAUDE.md for them and
do not reimplement any of it. Ignore the template's speckit commands
(`specs/…`); they belong to the template's own development, not to
reconstruction work.
