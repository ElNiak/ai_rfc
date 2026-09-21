---
name: ai-rfc-rfc-style
description: Internet-Draft prose discipline — section ownership, RFC 2119 keyword policy, claim citations, references, revision tagging and the build. Use when writing or revising the draft document of a reconstruction workspace.
---

# RFC prose for a reconstructed specification

The draft lives in `$AI_RFC_WORKSPACE/draft/`, a git repository laid out as an
adopter of `ElNiak/auto-i-d-template` (kramdown-rfc markdown; a `Makefile`
that includes the template's `main.mk`). The prose is yours; every claim of
fact in it is not. It must cite a claim the paired checkpoint manifest
holds, and the citation gate verifies that mechanically.

## Document structure

One source file `draft-<name>.md` at the repo root (exactly one; the gate
refuses zero or several). The skeleton fixes the sections; fill them, never
rename or reorder them:

| Section | Holds |
|---|---|
| Abstract, Introduction, Scope, Organization | What the system is, for a reader who never saw it. No cluster ordinals, no counts of claims added or withdrawn. |
| Conventions and Definitions, Terminology | BCP 14 boilerplate, the citation convention, the system's own terms. |
| Architecture Overview | Components and their interactions, with a cited figure. |
| Data Model and Structures | Records, messages, enumerations, state machines, as tables and figures, each introduced by a cited normative sentence. |
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
evidence, a default is a SHOULD, an option is a MAY. Use **exactly** the
keyword the claim's level maps to, capitalised, in the same sentence as the
citation. A weaker or missing keyword leaves the claim cited but unstated.
If the prose should be weaker, the claim's level is wrong; fix the claim
instead. Keywords appear only in normative sections, one behaviour per
sentence, one keyword per sentence.

## Claim citations

Every normative statement carries a backticked token naming its claim, as
`references/claim-citation.md` describes; read it before writing prose.
Cite each claim where it is stated: the sentence that carries its keyword
and says what it requires, worded no broader than the claim itself. A
citation in a bullet list, a Change Log entry or a caption alone does not
count as stating it. Enumerations, records and default values need a real
sentence too, for example "A sender MUST encode frame types with the
values in the table below `claim-id`." or "The default connection ID
length SHOULD be 8 bytes `claim-id`." A figure's caption additionally
cites the claims the figure depicts.

## References

The front matter's `normative:` and `informative:` lists are the document's
references. RFC and Internet-Draft entries are resolved from a sealed cache
the workspace carries. An entry the cache does not hold breaks the build and
must be written inline (`title`, `author`, `target`) instead of by number.
Never list RFC 2119 or RFC 8174: the BCP 14 boilerplate adds them itself, and
listing them again is a build warning. Cite every listed reference in the
text (`{{?RFC9000}}`-style) and list every reference you cite; an unused or
undeclared reference is an idnits warning. When no reference is needed,
add none.

## Clean compilation

idnits warns on lines over 72 characters inside artwork and tables, on
non-ASCII characters (including curly quotes and dashes), on unused
references and on keywords outside the boilerplate's reach. Keep figures
narrow, use ASCII only, and leave no empty section headings.

## Revisions

- One revision per spec-relevant round: extend the prose, commit, build, then
  tag with an **annotated** tag `draft-<name>-NN` (two digits, monotone across
  the sweep).
- Record every revision in `$AI_RFC_WORKSPACE/revisions.yaml` with, at minimum:
  `cluster_id`, `checkpoint_manifest_sha256` (from the checkpoint's
  `checkpoint.json`), an explicit boolean `normative_change`, a one-line
  `note`. A round may carry further fields; this list is not closed.
- A round that changes nothing normative still gets a revision entry with
  `normative_change: false` and a rationale. In a cluster round its citations
  are exactly the previous revision's; the gate checks. A consolidation round
  is the carve-out: it keeps every citation the previous revision carried and
  may add one, never drop one.

## The build

Before every revision tag, the draft is compiled once through the
template's own toolchain (`make txt html lint idnits`), offline, in a
scratch clone of the committed draft. It must exit 0 with no findings
before the tag is created. The reconstruction loop's own step names the
exact command for the arm in use, and the tagging step runs the build
again and refuses on findings. Never run `make` yourself and never edit
the template's own files.
