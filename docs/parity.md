# Tool ↔ CLI parity table

The instrument of the AI+MCP vs AI+CLI comparison. Both frontends call the
same core functions — the parity test suite keeps every write byte-identical
and every read JSON-identical across arms, and a test fails if a tool is
missing from this table.

The middle column is the **workspace form** of the one door, `ai-rfc`: no
paths, because the workspace and the toolchain are resolved from the sealed
config. That is the argv an agent types. The right-hand column is the same
dispatcher reached by its **explicit-path** leaf form, or a raw `git`, `cat`
or Python route where no verb exists at all — so the two columns are now one
program in two forms rather than two programs, and what separates them is
which inputs the caller has to name.

| MCP tool | `ai-rfc` verb | Raw substrate command (when one exists) |
|---|---|---|
| `ai_rfc_status` | — (MCP only) | — (composite over report.json, timeline.json, questions.yaml, git describe) |
| `ai_rfc_corpus_query` | `ai-rfc corpus query SQL` | `history.index.open_index` (Python) |
| `ai_rfc_cluster_next` | `ai-rfc cluster next` | — (clusters.jsonl minus checkpoints/revisions) |
| `ai_rfc_cluster_get` | `ai-rfc cluster get ID [--patch]` | `cat clusters/<id>/view.json`, `span.diff`, `evidence/pr.json` |
| `ai_rfc_claim_upsert` | `ai-rfc claim upsert ID --text … --anchor …` | — (schema-validated write; hand-editing + linter is the unguarded fallback) |
| `ai_rfc_claim_adjudicate` | `ai-rfc claim check` | `python -m ai_rfc check <manifest> --out …` → report.json `claims` |
| `ai_rfc_claim_record_status` | `ai-rfc claim record-status [IDS…]` | — (writes exactly the supported values) |
| `ai_rfc_question_draft` | `ai-rfc question draft TEXT --claim ID…` | — (strict register write) |
| `ai_rfc_question_export` | `ai-rfc question export` | — |
| `ai_rfc_answer_record` | `ai-rfc answer record QID --answer … --transcript … --quote …` | — (verbatim-quote + exact-wording guardrails) |
| `ai_rfc_revision_record` | `ai-rfc revision record TAG --cluster ID (--normative\|--no-normative) --note MSG [--kind consolidation --checkpoint consolidations/NN]` | — (validated via the gate's own loader) |
| `ai_rfc_checkpoint` | `ai-rfc checkpoint CLUSTER [--consolidation NN --base checkpoints/CLUSTER]` | `python -m ai_rfc draft checkpoint … [--consolidation NN --base DIR]` |
| `ai_rfc_gate` | `ai-rfc gate [--strict]` | `python -m ai_rfc check <manifest> --out … --repo … [--strict]` |
| `ai_rfc_citation_gate` | `ai-rfc citation-gate [--strict]` | `python -m ai_rfc draft gate … [--strict]` |
| `ai_rfc_draft_commit` | `ai-rfc draft commit -m MSG` | `git -C draft add -A && git -C draft commit -m MSG` |
| `ai_rfc_revision_tag` | `ai-rfc revision tag TAG -m MSG` | `git -C draft tag -a TAG -m MSG`, then `python -m ai_rfc draft gate … --strict` (the tool deletes the tag on findings; the raw route leaves that to the author), and runs `draft build` before the tag when `AI_RFC_TOOLCHAIN` is set |
| `ai_rfc_draft_build` | `ai-rfc draft build [--ref REF]` | — (not available in arm C: frozen at the pre-v2 surface, spec D42) |
| `ai_rfc_draft_lint` | `ai-rfc draft lint [--committed]` | — (not available in arm C, D42) |
| `ai_rfc_structure_upsert` | `ai-rfc structure upsert ID --json …` | — (not available in arm C, D42) |
| `ai_rfc_draft_render` | `ai-rfc draft render` | — (not available in arm C, D42; an operator has `python -m ai_rfc draft render MANIFEST`) |

CLI-1 left these verbs alone; CLI-3 folded them into `ai-rfc` as grouped
subcommands and retired the `ai_rfc` console script, the campaign `bin/ai_rfc`
shim and both `prog="ai_rfc"` values with them (D56). The middle column is not
prose: `test_the_parity_table_names_a_verb_the_parser_owns` walks
`cli.build_parser()` and fails on any cell naming a verb the live parser does
not own, which is what the column lacked while it drifted through the rename.

**One row has no verb (CLI-3 D16).** `ai_rfc_status` stays an MCP tool and
gains no `ai-rfc` verb, because `ai-rfc status` already means the operator's
ledger and
a folded read has no claim on a name that is taken. The `draft` verbs, by
contrast, spell their **workspace** form above; naming a path selects the
explicit form in the right-hand column instead, which prints no JSON and reads
a different revision.

Arm C is frozen at its pre-v2 surface (D42): it never sees the
`structure-upsert` or `draft-render` verbs or the consolidation flags on
`revision-record` and `checkpoint`, though the raw `python -m ai_rfc draft
render` and `draft checkpoint --consolidation` stay reachable behind its
unchanged `Bash(python -m ai_rfc*)` prefix. What D42 freezes is the documented
tool and verb set, not the substrate the prefix reaches.

Arms A and B share nineteen of the twenty operations above; the twentieth is
CLI-3 D16's, and it is an **arm-surface change** rather than a documentation one.
Arm A has an `ai_rfc_status` tool and arm B has no verb for it, so the two
surfaces are no longer the same size. The information is not withheld from arm
B — it reads the same four artifacts with Read, and `ai-rfc status` prints the
operator's ledger over them — but obtaining the tool's composite costs it
several calls instead of one, and any per-call measure must be read knowing
that. A v2 campaign still compares those two arms.

**A second asymmetry, recorded 2026-09-22 (`0e0062f`).** The `ai_rfc_corpus_query`
tool's description now carries the corpus index's schema — every table and
column, derived from `history/index.py`'s DDL with sqlite itself — because a
Phase 5 run guessed three columns that do not exist. Arm A reads that
description; arms B and C reach the same index through `ai-rfc corpus query`
and `sqlite3`, whose only schema hint is the one example in the rendered
prompt slot. Unlike D16's, this gap costs arms B and C **accuracy** rather than
calls: an arm that must guess column names pays in failed queries. The
prompt-slot half — deriving `{{corpus_query}}` and the A/B/C entries from the
same summary and regenerating the pinned loop skill — changes every arm's
rendered prompt and therefore re-freezes a campaign's instrument, so it is
owed to the main-campaign map as #62b rather than landed here.

## Exit codes

Every gate route — MCP tool, `ai-rfc` verb, or raw substrate command — surfaces
the substrate's own exit code untouched, so all three arms read the same
number for the same outcome.

| Code | Meaning |
|---|---|
| 0 | Clean |
| 1 | The command could not complete — an input that will not load, a path that is not a repository, an `--out` that cannot be written, a refusal, a crash, or a sweep stopped with work outstanding |
| 2 | A usage error raised by `argparse` itself: the invocation was malformed |
| 3 | Findings — a promotion violation, an unresolved anchor, a citation the gate refused, or any other verdict a command reached and gates on |

A verb that takes `--strict` returns 3 only when given it; `doctor`,
`toolchain verify` and `pipeline substrate` take no such flag and return 3
whenever their verdict carries something. 3 names the outcome, not the flag.

The 2/3 split matters because argparse owns 2 unconditionally. While strict
findings also exited 2, a caller branching on it could not distinguish a
mistyped flag from a real finding about the manifest, and the two demand
opposite responses: fix the command, or fix the evidence.

Only argparse's own errors exit 2. A usage error the shared core catches —
`--base` without `--consolidation`, say, which one core function must reject
for both frontends — is a `CoreError` and exits 1 like every other one. The
raw substrate command rejects the same combination in its own parser, so it
exits 2 there; that difference is a consequence of D42's one-core rule, not a
disagreement about what the invocation means.

Asymmetries accepted and measured, not hidden: the raw-CLI arm can hand-edit
YAML (the gate catches overstatement after the fact, where the tool arm's
`claim_upsert` refuses it up front), and has no single-call equivalent for
the register/answer guardrails. The raw arm's only corpus-index path is the
`sqlite3` CLI over `corpus/index.sqlite` (the index is derived and
disposable; a write through it is detected by nothing), and its tag is not
rolled back on citation findings.
