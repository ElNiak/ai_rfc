# SP7a end-to-end proof on the finished MARK workspace — 2026-09-03

These numbers are the **SP7a waypoint**: the finished MARK reconstruction
(campaign `mark-full-1`, run A1) measured with SP7a's own instrument — a
lint that has no `extra.structures` block, because SP7b has not landed. They
are **not** SP7d's "before". SP7d's replay re-runs the *final*, SP7b-extended
lint on a fresh copy of the same workspace so that both sides of its
before/after table come from one instrument; comparing this document's
numbers against that later "after" would measure instrument drift, not
content drift. Reread `narration`, `keywords`, `citations` and `blocks`
below as "what SP7a's lint said about the finished draft", not as a
regression baseline for SP7d to beat.

The working copy this document was measured from lives at
`/tmp/claude/sp7a/mark-a1` and is disposable: every number below is
reproducible from the campaign directory
(`~/ai-rfc-experiments/campaigns/mark-full-1/runs/A1/workspace`), and a
read-only, checksum-verified snapshot of the same bytes — taken before any
SP7 code landed — is sealed at
`~/ai-rfc-experiments/baselines/mark-a1-2026-09-03` (finished
2026-09-02T16:17:07+00:00, 37 of 69 checkpoints, arm A, repeat 1).

## Whole suite

Measured against `ai_rfc` commit `9085781` (`test: pin the arm prompts to
the loop template a campaign was handed`). Peer sessions kept committing to
this checkout while this task ran: this document's own commit lands on
`a350f3b`, three commits later, not on `9085781` itself. The suite count
below reflects the tree at `9085781`, the commit it was actually run
against.

```
======================= 928 passed, 1 skipped in 27.41s ========================
```

## Toolchain verify

```
$ python -m ai_rfc.experiment toolchain verify --root ~/ai-rfc-experiments
ok
exit=0
```

Record: `recorded_at` `2026-09-04T12:02:09+00:00`, `template_commit`
`dcdd985a86afad97a50f7b5e1b613f57c194b774`, `template_home`
`/Users/elniak/ai-rfc-experiments/tools/i-d-template`. This is the record
Task 5 re-provisioned; the earlier hand-made 2026-09-03 record at
`~/ai-rfc-experiments/tools.manual-2026-09-03` was left untouched and was
not used here.

## `workspace migrate-draft`

Pre-migration draft HEAD: `8ef6da625736895457f8c9590ae13bcd521021d3`
("Cluster 37: report page shows the subject as a single value"), tree clean.

```
$ python -m ai_rfc.experiment workspace migrate-draft /tmp/claude/sp7a/mark-a1
draft HEAD: f1513f8cd17525c5b3d72388d28af3bef9d12f61
exit=0
```

`migrate_draft`'s default `--template` clones
`https://github.com/ElNiak/auto-i-d-template` over the network to read the
three adopter files; the toolchain root's local copy of that template at
`~/ai-rfc-experiments/tools/i-d-template` has no `.git` (it was provisioned
without one), so it cannot serve as an offline `--template` clone source. The
migration above ran with the default (networked) template source. Anyone
reproducing this step needs network access to GitHub, or a locally
git-initialised copy of the template pinned to commit
`dcdd985a86afad97a50f7b5e1b613f57c194b774`.

Post-migration:

```
$ git -C /tmp/claude/sp7a/mark-a1/draft ls-files
.editorconfig
.gitignore
Makefile
draft-elniak-mark-reconstructed.md
```

Four files, as expected. Tag count: **37** (`draft-elniak-mark-reconstructed-01`
through `-37`). The migration commit's date is `2026-08-26T00:00:00Z` — the
harness's fixed `PINNED_DATE` constant, not today's date; every `migrate_draft`
commit carries this same fixed date so the operation is reproducible byte-for
-byte, and it is why the build's `date` field below reads `2026-08-26` rather
than the day the build actually ran (`build()` defaults the xml2rfc `-D` date
to the built ref's commit date, and the built ref here is the migration
commit).

## `draft build`

```
$ AI_RFC_TOOLCHAIN=~/ai-rfc-experiments/tools/toolchain.json \
  python -m ai_rfc.draft build /tmp/claude/sp7a/mark-a1/draft --out /tmp/claude/sp7a/mark-a1/out
note: build of f1513f8cd175 exited 0; report at /tmp/claude/sp7a/mark-a1/out/build/build-report.json
exit=0
```

From `build-report.json`:

- `exit_code`: `0`
- `findings`: `[]`
- `date`: `"2026-08-26"` (see the `PINNED_DATE` note above)
- `commit`: `f1513f8cd17525c5b3d72388d28af3bef9d12f61`
- `source_sha256`: `c170e07901264575ffa177da6835f1dc3ac3df65304ffd478764a4ec52deb76b`
- `diagnostics`: `[]` (empty — see "Regex validation" below; this is not
  itself evidence that the two unvalidated regexes are broken)
- `broken_references`: `[]`
- `idnits`: `{"WARNING": 2}`
- `outputs`:
  - `draft-elniak-mark-reconstructed.html` — sha256
    `e99e677e31eebebc7e92dfec93be7f30d455ac2024057ac2aff4bb8026562450`
  - `draft-elniak-mark-reconstructed.txt` — sha256
    `b9e6f6a7e68b14369270367365e98039dbcec0c46b6c6d5b1f3240ac44d333cc`
- `template`: commit `dcdd985a86afad97a50f7b5e1b613f57c194b774`, path
  `/Users/elniak/ai-rfc-experiments/tools/i-d-template`
- `stages`: 10 stages recorded by the template's own trace (`kramdown-rfc`,
  `venue`, `v2v3`, `xml2rfc-txt`, `xml2rfc-html`, `ws` for
  `draft-elniak-mark-reconstructed`; `kramdown-rfc`, `venue`, `v2v3`,
  `idnits` for `versioned/draft-elniak-mark-reconstructed-38`), every one
  `status: 0` with an empty `stderr` (the template's trace only records
  stderr lines for a stage that fails).

### Regex validation

This is the first real-toolchain build this plan has run against a draft
with actual idnits warnings, so it is the first chance to check the four
build diagnostic regexes against real tool output rather than a
hand-written fake `make` stderr.

- **`_IDNITS_SUMMARY` — positively validated.** The real idnits run printed
  `" WARNING  2 nits of ⚠️ warning severity"` for two findings
  (`LINE_PI` — the document contains `<?line 123?>` processing-instruction
  tags; `UNEXPECTED_DOC_VERSION` — the docName version is not `00`), and
  `build-report.json`'s `idnits` field correctly recorded `{"WARNING": 2}`.
  Both are WARNING-severity, so `BuildReport.findings` correctly reported no
  finding (it only promotes `idnits["ERROR"]`).
- **`_OFFLINE_STUB` — not exercised by this run** (`broken_references` is
  empty: every reference this draft cites is already in the sealed
  refcache). Reported by the build implementer as validated against real
  output previously; this run is consistent with, but does not itself
  re-confirm, that claim.
- **`_KRAMDOWN_WARNING` and `_XML2RFC_UNRESOLVED` — not exercised, and
  confirmed not silently broken.** `diagnostics` came back empty for both.
  To distinguish "kramdown-rfc/xml2rfc emitted nothing to match" from "they
  emitted matching lines the regex missed", the exact `make` invocation
  (argv and env reconstructed verbatim from the report, listed above) was
  rerun by hand into a fresh scratch clone with raw stdout+stderr captured
  independently of `_parse_output`. The raw capture is 3665 bytes, saved at
  `/tmp/claude/sp7a/mark-a1/out/verify-raw.txt`; grepping it directly for
  `^\*\* \(` (the `_KRAMDOWN_WARNING` shape) and `Unable to resolve external
  request` (the `_XML2RFC_UNRESOLVED` shape) found zero matches. kramdown-rfc
  and xml2rfc produced clean output for this draft; the two regexes remain
  unvalidated against a real positive match, but this run rules out a
  silent parsing failure.

## `draft lint`

```
$ python -m ai_rfc.draft lint /tmp/claude/sp7a/mark-a1/draft --out /tmp/claude/sp7a/mark-a1/out --manifest /tmp/claude/sp7a/mark-a1/manifest.yaml
finding: abstract: still the skeleton stub
finding: references: none declared (normative and informative are both empty)
finding: introduction: narrates the reconstruction (87 line(s), e.g. line 42)
finding: keywords: MUST fraction 0.84 exceeds 0.8 over 460 keywords
note: lint report at /tmp/claude/sp7a/mark-a1/out/lint-report.json
exit=0
```

Findings, verbatim (also in `lint-report.json`'s `findings`):

```json
[
  "abstract: still the skeleton stub",
  "references: none declared (normative and informative are both empty)",
  "introduction: narrates the reconstruction (87 line(s), e.g. line 42)",
  "keywords: MUST fraction 0.84 exceeds 0.8 over 460 keywords"
]
```

`abstract`: `{"is_stub": true, "word_count": 47}`. `references`:
`{"normative": 0, "informative": 0, "inline": 0}`. `sections.missing`: `[]`
(all three required sections present, alongside ten others the draft adds).

`keywords`, verbatim:

```json
{
  "histogram": {"MAY": 45, "MUST": 374, "MUST NOT": 14, "SHOULD": 27},
  "must_fraction": 0.8435,
  "total": 460
}
```

`blocks`, verbatim:

```json
{
  "figures": 0,
  "tables": 0,
  "figures_without_caption_citation": []
}
```

`citations`, verbatim (159 `uncited` claim ids — every claim in the
manifest this draft's prose does not cite by `a_rfc:`/citation token — are
listed in full for reproducibility, not because a reader needs to read all
159):

```json
{
  "tokens": 478,
  "distinct": 433,
  "legacy_tokens": 0,
  "cited_unknown": [],
  "cited_fraction": 0.7314,
  "uncited": [
    "mark:act.1", "mark:act.10", "mark:act.11", "mark:act.12", "mark:act.13",
    "mark:act.16", "mark:act.17", "mark:act.2", "mark:act.21", "mark:act.22",
    "mark:act.23", "mark:act.25", "mark:act.26", "mark:act.27", "mark:act.28",
    "mark:act.29", "mark:act.3", "mark:act.30", "mark:act.4", "mark:act.46",
    "mark:act.47", "mark:act.5", "mark:act.6", "mark:act.7", "mark:act.8",
    "mark:act.9", "mark:agent.1", "mark:agent.10", "mark:agent.11",
    "mark:agent.13", "mark:agent.16", "mark:agent.17", "mark:agent.2",
    "mark:agent.20", "mark:agent.21", "mark:agent.23", "mark:agent.24",
    "mark:agent.25", "mark:agent.27", "mark:agent.28", "mark:agent.29",
    "mark:agent.3", "mark:agent.30", "mark:agent.31", "mark:agent.37",
    "mark:agent.40", "mark:agent.45", "mark:agent.59", "mark:agent.67",
    "mark:agent.72", "mark:agent.73", "mark:agent.74", "mark:agent.8",
    "mark:agent.87", "mark:agent.9", "mark:alg.104", "mark:alg.12",
    "mark:alg.13", "mark:alg.14", "mark:alg.15", "mark:alg.16", "mark:alg.17",
    "mark:alg.18", "mark:alg.20", "mark:alg.33", "mark:alg.34", "mark:alg.35",
    "mark:alg.36", "mark:alg.37", "mark:alg.38", "mark:alg.39", "mark:alg.51",
    "mark:alg.55", "mark:alg.7", "mark:alg.73", "mark:data.1", "mark:data.12",
    "mark:data.16", "mark:data.2", "mark:data.3", "mark:data.4", "mark:data.6",
    "mark:obs.1", "mark:obs.10", "mark:obs.11", "mark:obs.12", "mark:obs.13",
    "mark:obs.14", "mark:obs.15", "mark:obs.16", "mark:obs.19", "mark:obs.2",
    "mark:obs.21", "mark:obs.24", "mark:obs.25", "mark:obs.27", "mark:obs.28",
    "mark:obs.29", "mark:obs.3", "mark:obs.30", "mark:obs.31", "mark:obs.32",
    "mark:obs.33", "mark:obs.36", "mark:obs.37", "mark:obs.4", "mark:obs.40",
    "mark:obs.41", "mark:obs.46", "mark:obs.47", "mark:obs.48", "mark:obs.49",
    "mark:obs.5", "mark:obs.59", "mark:obs.6", "mark:obs.60", "mark:obs.61",
    "mark:obs.62", "mark:obs.63", "mark:obs.64", "mark:obs.7", "mark:obs.73",
    "mark:obs.76", "mark:obs.8", "mark:obs.81", "mark:obs.89", "mark:obs.9",
    "mark:proto.2", "mark:proto.6", "mark:proto.8", "mark:srv.10",
    "mark:srv.11", "mark:srv.12", "mark:srv.13", "mark:srv.15", "mark:srv.19",
    "mark:srv.21", "mark:srv.22", "mark:srv.26", "mark:srv.27", "mark:srv.28",
    "mark:srv.3", "mark:srv.31", "mark:srv.35", "mark:srv.37", "mark:srv.38",
    "mark:srv.39", "mark:srv.4", "mark:srv.40", "mark:srv.41", "mark:srv.5",
    "mark:srv.6", "mark:srv.7", "mark:srv.9", "mark:store.1", "mark:store.2",
    "mark:store.3", "mark:store.4", "mark:store.6"
  ]
}
```

`sections`, verbatim:

```json
{
  "missing": [],
  "present": [
    "Introduction", "Conventions and Definitions", "Architecture",
    "Data Model", "Server Interface", "The Datastore", "Activation",
    "Detection Agents", "Data Agents", "Server Lifecycle and Configuration",
    "Observed Implementation Behaviour", "Security Considerations",
    "IANA Considerations"
  ]
}
```

`narration`: **88 entries over 87 distinct lines** (one line, 53, carries
two tells: an ordinal-cluster reference and a "this revision" reference).
Per-pattern histogram, computed from the report, not by hand:

| Pattern | Count |
|---|---|
| `cluster` (generic fallback; fires only where no more specific pattern matched that line) | 39 |
| `ordinal cluster` | 33 |
| `added/withdrawn count` | 10 |
| `this revision` | 6 |

This is a genuine measurement change from the lint's first cut, whose
earlier pass over this same draft counted 121 pattern matches over the same
87 distinct lines (Task 2's review revised `narration` so a line's specific
tells are recorded once each and the generic `cluster` tag is recorded only
as a fallback, instead of every pattern independently double-counting a
line that also matches `cluster`). The distinct-line count is unchanged
because which lines carry a narration tell did not change — only how many
duplicate labels get attached per line did.

## `draft gate --strict`

```
$ python -m ai_rfc.draft gate /tmp/claude/sp7a/mark-a1/draft \
  --timeline /tmp/claude/sp7a/mark-a1/timeline \
  --checkpoints /tmp/claude/sp7a/mark-a1/checkpoints \
  --questions /tmp/claude/sp7a/mark-a1/questions.yaml \
  --revisions /tmp/claude/sp7a/mark-a1/revisions.yaml \
  --out /tmp/claude/sp7a/mark-a1/out --strict
note: gate clean
exit=0
```

`gate-report.json`: `{"findings": []}`. Clean, no findings, exit 0 — as the
plan predicted, since the migration commit sits after every tag and changes
nothing the gate reads. This run also exercised commit `93308a5` ("gate a
normative revision that changed no evidence"), landed by a peer session
before this run and already present in the tree: its added rule (a
`normative_change: true` revision whose checkpoint manifest is identical to
the previous revision's, or whose first entry holds no claims, is a strict
finding) produced zero findings against MARK's real 37-revision history — no
attribution note is needed because the gate found nothing to attribute.
