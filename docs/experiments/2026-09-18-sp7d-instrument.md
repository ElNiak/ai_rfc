# SP7d instrument — before/after, both sides by the final lint — 2026-09-18

This is SP7d's before/after table. Both columns were produced by **one
instrument**: the lint as it stands at the end of SP7d, run over fresh copies
of two existing workspaces. Neither column came from a paid run. **No model
was called to produce any number in this document**, and nothing here was
written back into the trees it was measured from.

It is the artifact `docs/experiments/2026-09-03-sp7a-mark-baseline.md`
anticipated when it said its own numbers are *not* SP7d's "before", because
comparing them against a later "after" would measure instrument drift rather
than content drift. Re-linting both sides with the final lint is what removes
that confound — and it is the only confound removed. See the caveat below.

## Provenance

| column | source | size | copy |
|---|---|---|---|
| **before** | `~/ai-rfc-experiments/baselines/mark-a1-2026-09-03/workspace` | 232 MB | read-only sealed tree, `dr-xr-xr-x`; verified still `dr-xr-xr-x` after copying |
| **after** | `~/arfc-experiments/campaigns/pilot-aioquic-w02-11-20260831/runs/A1/workspace` | 20 MB | pilot run **A1** |

Both were copied out and linted outside their evidence roots. The sealed tree
was never written to. Run **A1** is used rather than C1 because C1's manifest
carries `level: descriptive` and will not load under the current schema, which
would have reported every one of its revisions as unmeasured.

## Every revision on both sides was measured

| | revisions | `manifest_status` | `draft_status` | `revisions_status` |
|---|---|---|---|---|
| before (MARK A1) | **37** | all `read` | all `read` | `read`, no error |
| after (pilot A1) | **10** | all `read` | all `read` | `read`, no error |

This matters more than any single metric. Every number below is a
*measurement* rather than an absence: no manifest was unreadable, no draft
text was missing at its tag, and no revision map failed to enumerate. Where
this instrument cannot measure something it says so, and it said so nowhere
here.

## The table

Final revision of each side — `draft-elniak-mark-reconstructed-37` against
`draft-elniak-aioquic-reconstructed-10`.

| metric | MARK A1 (before) | pilot A1 (after) | delta |
|---|---|---|---|
| abstract.is_stub | yes | yes | — |
| abstract.word_count | 47 | 47 | 0 |
| blocks.figures | 0 | 0 | 0 |
| blocks.figures_without_caption_citation | 0 | 0 | 0 |
| blocks.tables | 0 | 0 | 0 |
| citations.cited_fraction | 0.731 | 0.000 | -0.731 |
| citations.tokens | 478 | 0 | -478 |
| citations.uncited | 159 | 40 | -119 |
| finding_count | 71 | 17 | -54 |
| keywords.must_fraction | 0.844 | 0.964 | +0.121 |
| narration_count | 88 | 2 | -86 |
| references.informative | 0 | 0 | 0 |
| references.inline | 0 | 0 | 0 |
| references.normative | 0 | 0 | 0 |
| sections.missing | 0 | 2 | +2 |
| structures.defined | 0 | 0 | 0 |
| structures.malformed | 0 | 0 | 0 |
| structures.rendered | 0 | 0 | 0 |

## What it says

**Zero figures, zero tables and zero structures on both sides.** This is the
claim SP7d exists to make sayable. The design spec opened by asserting that
every draft the loop has produced has no figures, no tables and no state
machines; that assertion is now a measurement, taken by the instrument, on
both a finished MARK reconstruction and a pilot aioquic run. `structures` is
SP7b's own block — the machinery for defining and rendering a structure
exists, and across 47 revisions neither draft defined a single one.

**Neither draft wrote its own abstract.** Both report `is_stub: true` at
exactly **47 words**. Two different reconstructions, two different corpora,
two different harness versions — and the same untouched template stub at the
end of each. An identical count across independent runs is what makes this
legible: it is not a short abstract, it is the boilerplate.

**The pilot draft cites nothing at all.** `citations.tokens` is **0** against
40 uncited claims, so its `cited_fraction` of 0.000 is a *measured* zero — the
manifest loaded, the claims exist, and no citation token points at any of
them. That is a different statement from the em dash this instrument prints
when a manifest declares no claims, which means nothing was measured. The
distinction is the point of the instrument.

**The pilot is missing two required sections** — Security Considerations and
IANA Considerations — where the MARK draft is missing none.

## What it does not say

**This is not a controlled comparison, and the two columns must not be read
as a regression.** The MARK A1 draft is 166,554 bytes across 2,911 lines; the
pilot drafts run 9,028–18,450 bytes. That is a 10–18× size gap over
**different corpora**, produced by different harness versions.

Re-linting both sides with the final instrument removes **instrument drift**.
It does not remove **content drift**, and no arrangement of artifacts that
already exist can: eliminating it requires a paid consolidation run on the
MARK copy under the current loop, which SP7d deliberately does not perform.

So `0.731` against `0.000` is **not evidence that the loop got worse**. Two
drafts over different corpora at an order-of-magnitude size difference will
differ on a density metric for reasons that have nothing to do with the loop.
The rows worth reading across the pair are the ones no size or corpus
difference explains away: **figures, tables and structures, all zero on both
sides**, and **both abstracts left as the same stub**.

## Reproducing it

Copy each workspace out of its evidence root, then run
`ai_rfc.experiment.quality.revision_lints` over the copy and
`compare_lints` over the two final revisions. Copy out, never in: the sealed
MARK tree is mode `dr-xr-xr-x` and must stay that way, and neither source
directory is written to by any step above.
