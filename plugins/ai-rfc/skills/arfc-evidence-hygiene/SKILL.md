---
name: arfc-evidence-hygiene
description: The a_rfc promotion rule as working intuition — status is adjudicated never asserted, anchors pin commits, sign-off needs exact wording. Use when writing or editing claims, anchors, statuses or sign-offs in a reconstruction manifest.
user-invocable: false
---

# Evidence hygiene for reconstruction manifests

The substrate (`$PANTHER_REPO/panther/plugins/services/testers/a_rfc/`) is
built so that a claim's evidential standing is **computed from its evidence,
never asserted by its author**. These rules keep you on the right side of
that design; each exists because the failure it prevents exits zero while
producing wrong results.

## Never write `status`

Omit `status` on every claim you write or edit. Omission is always safe: it
defaults to `gap`, the lowest rank, and a violation only ever fires when a
*stored* status exceeds what the evidence supports. When the report's
`claims` payload shows `supported` above `stored`, record exactly the
supported value — never more. Understatement is always permitted;
overstatement is a gate finding. The single authority is
`promotion.adjudicate`:

> `confirmed` requires developer sign-off, runtime corroboration, or two
> distinct evidence classes **at least one of which is primary** (`code` or
> `runtime`). Claims resting only on `adr` or `paper` cap at `inferred`. No
> anchors means `gap`, sign-off notwithstanding.

## Why interview + paper stays `inferred`

A paper and an interview are two classes but may be one person speaking
twice — in the case this substrate was built for, the papers share authors
with the developers who validate the claims. Promoting on two narrative
sources launders exactly the circularity that evidence-provenance
stratification exists to detect. `{interview, code}` reaches `confirmed`;
`{interview, paper}` does not.

## Anchor discipline

- Pin every `code`/`runtime` anchor to a **corpus commit**, never HEAD or a
  branch name. A `path:line` reference into a moving tree silently points
  at different code as the tree advances; verification refuses commit-less
  anchors rather than checking the working tree.
- Record `line` where the claim is about a specific statement, and
  `line_sha256` (hex digest of the line's bytes, newline stripped) when the
  citation must survive file drift. Both are verified at the pinned commit.
- `adr` locators are commit shas whose message states a decision; `paper`
  locators are DOIs; `interview` locators are transcript ids
  (`int-NNN`). None of these carries a `commit` field.

## The honesty metric

`checked_fraction_by_req_class` (report.json) is the fraction of *confirmed*
claims a non-model oracle — a person or a run — actually saw. For a spec
mined from source and prose it starts at **0.0, and that is expected**: it
measures how much of what is called confirmed rests on nothing but a
reading. It moves only through sign-offs and runtime anchors, never through
more reading.

## Four silent-failure traps (why the substrate is strict)

| Trap | Consequence if relaxed |
|---|---|
| Promotion failing open | Every claim promotes; the manifest looks excellent and means nothing |
| Anchors without pinned commits | Plausible-looking citations into a moving tree |
| YAML type coercion of identifiers | `4.10` sorts before `4.9`; quote every section and id |
| Serialisation dropping derived values | Counts and fractions vanish silently |

Treat a gate exit 2 as the system working, never as an obstacle: fix the
anchors first (they are the weakest link), re-adjudicate, re-run.
