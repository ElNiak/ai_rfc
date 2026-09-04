---
name: ai-rfc-rfc-style
description: Internet-Draft prose discipline for reconstructed specifications — section ownership, keyword policy, claim citations, references, revision tagging and the build. Use when writing or revising the draft document of a reconstruction workspace.
---

# RFC prose for a reconstructed specification

The draft lives in `$AI_RFC_WORKSPACE/draft/`, a git repository laid out as an
adopter of `ElNiak/auto-i-d-template` (kramdown-rfc markdown; a `Makefile`
that includes the template's `main.mk`). The prose is yours; every claim of
fact in it is not — it must cite a claim the paired checkpoint manifest holds,
and the citation gate verifies that mechanically.

## Document structure

One source file `draft-<name>.md` at the repo root (exactly one — the gate
refuses zero or several). The skeleton fixes the sections; fill them, never
rename or reorder them:

| Section | Holds |
|---|---|
| Abstract, Introduction, Scope, Organization | What the system is, for a reader who never saw it. No cluster ordinals, no counts of claims added or withdrawn. |
| Conventions and Definitions, Terminology | BCP 14 boilerplate, the citation convention, the system's own terms. |
| Architecture Overview | Components and their interactions, with a cited figure. |
| Data Model and Structures | Records, messages, enumerations, state machines — tables and figures. |
| Protocol Operation | Behaviour by concern, one subsection each; the normative core. |
| Configuration and Defaults, Error Handling | Keys and defaults as a table; failure behaviour. |
| Observed Accidental Behaviour | `intent: accidental` claims, described and never as requirements. |
| Security Considerations | Real analysis of the interface's exposure, even when the answer is "nothing protects it". |
| Change Log (appendix) | One entry per revision tag: the per-cluster narration goes here and nowhere else. |
| Implementation Notes (appendix) | Facts that are not requirements: class paths, test doubles, packaging, `/tmp` paths, literal return strings. Move them here; never drop a cited sentence. |

## What is not specification material

A test double, a fixture value, a class or file path, a build artefact, a
literal return string or a temporary path describes the implementation, not
the behaviour a second implementation must reproduce. It goes to
Implementation Notes with its citation, or nowhere.

## Keywords

A normative statement's keyword comes from the cited claim's `level` and the
keyword policy in `references/keyword-policy.md`: MUST needs enforcing
evidence, a default is a SHOULD, an option is a MAY. Keywords appear only in
normative sections, capitalised, one behaviour per sentence. Prose may be
weaker than the claim's level, never stronger.

## Claim citations

Every normative statement carries a backticked token naming its claim, as
`references/claim-citation.md` describes — read it before writing prose. A
figure's caption sentence cites the claims the figure depicts.

## References

The front matter's `normative:` and `informative:` lists are the document's
references. RFC and Internet-Draft entries are resolved from a sealed cache
the workspace carries; an entry the cache does not hold breaks the build and
must be written inline (`title`, `author`, `target`) instead of by number.
Never list RFC 2119 or RFC 8174: the BCP 14 boilerplate adds them itself, and
listing them again is a build warning.

## Revisions

- One revision per spec-relevant round: extend the prose, commit, build, then
  tag with an **annotated** tag `draft-<name>-NN` (two digits, monotone across
  the sweep).
- Record every revision in `$AI_RFC_WORKSPACE/revisions.yaml` with, at minimum:
  `cluster_id`, `checkpoint_manifest_sha256` (from the checkpoint's
  `checkpoint.json`), an explicit boolean `normative_change`, a one-line
  `note`. A round may carry further fields; this list is not closed.
- A round that changes nothing normative still gets a revision entry with
  `normative_change: false` and a rationale. Its citation set must equal the
  previous revision's; the gate checks.

## The build

The draft is compiled only through `draft build` (the `ai_rfc_draft_build`
tool or the `ai_rfc draft-build` verb): the template's `make txt html lint
idnits`, run offline in a scratch clone of the committed draft. It must exit 0
with no findings before a revision is tagged; the tag tool runs it again and
refuses on findings. Never run `make` yourself and never edit the template's
own files.
