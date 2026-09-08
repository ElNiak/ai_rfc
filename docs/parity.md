# Tool ↔ CLI parity table

The instrument of the AI+MCP vs AI+CLI comparison. Both frontends call the
same core functions — the parity test suite keeps every write byte-identical
and every read JSON-identical across arms, and a test fails if a tool is
missing from this table.

The middle column is the `ai_rfc` console script (underscore,
`ai_rfc.server.cli`), a different program from `ai-rfc` / `python -m ai_rfc`
(hyphen, the substrate dispatcher in the right-hand column). Transcribing an
`ai_rfc` verb into the `python -m ai_rfc` form yields "unknown verb" and exit 2.

| MCP tool | `ai_rfc` verb | Raw substrate command (when one exists) |
|---|---|---|
| `ai_rfc_status` | `ai_rfc status` | — (composite over report.json, timeline.json, questions.yaml, git describe) |
| `ai_rfc_corpus_query` | `ai_rfc corpus-query SQL` | `history.index.open_index` (Python) |
| `ai_rfc_cluster_next` | `ai_rfc cluster-next` | — (clusters.jsonl minus checkpoints/revisions) |
| `ai_rfc_cluster_get` | `ai_rfc cluster-get ID [--patch]` | `cat clusters/<id>/view.json`, `span.diff`, `evidence/pr.json` |
| `ai_rfc_claim_upsert` | `ai_rfc claim-upsert ID --text … --anchor …` | — (schema-validated write; hand-editing + linter is the unguarded fallback) |
| `ai_rfc_claim_adjudicate` | `ai_rfc claim-adjudicate` | `python -m ai_rfc check <manifest> --out …` → report.json `claims` |
| `ai_rfc_claim_record_status` | `ai_rfc claim-record-status [IDS…]` | — (writes exactly the supported values) |
| `ai_rfc_question_draft` | `ai_rfc question-draft TEXT --claim ID…` | — (strict register write) |
| `ai_rfc_question_export` | `ai_rfc question-export` | — |
| `ai_rfc_answer_record` | `ai_rfc answer-record QID --answer … --transcript … --quote …` | — (verbatim-quote + exact-wording guardrails) |
| `ai_rfc_revision_record` | `ai_rfc revision-record TAG --cluster ID (--normative\|--no-normative) --note MSG [--kind consolidation --checkpoint consolidations/NN]` | — (validated via the gate's own loader) |
| `ai_rfc_checkpoint` | `ai_rfc checkpoint CLUSTER [--consolidation NN --base checkpoints/CLUSTER]` | `python -m ai_rfc draft checkpoint … [--consolidation NN --base DIR]` |
| `ai_rfc_gate` | `ai_rfc gate [--strict]` | `python -m ai_rfc check <manifest> --out … --repo … [--strict]` |
| `ai_rfc_citation_gate` | `ai_rfc citation-gate [--strict]` | `python -m ai_rfc draft gate … [--strict]` |
| `ai_rfc_draft_commit` | `ai_rfc draft-commit -m MSG` | `git -C draft add -A && git -C draft commit -m MSG` |
| `ai_rfc_revision_tag` | `ai_rfc revision-tag TAG -m MSG` | `git -C draft tag -a TAG -m MSG`, then `python -m ai_rfc draft gate … --strict` (the tool deletes the tag on findings; the raw route leaves that to the author), and runs `draft build` before the tag when `AI_RFC_TOOLCHAIN` is set |
| `ai_rfc_draft_build` | `ai_rfc draft-build [--ref REF]` | — (not available in arm C: frozen at the pre-v2 surface, spec D42) |
| `ai_rfc_draft_lint` | `ai_rfc draft-lint [--committed]` | — (not available in arm C, D42) |
| `ai_rfc_structure_upsert` | `ai_rfc structure-upsert ID --json …` | — (not available in arm C, D42) |
| `ai_rfc_draft_render` | `ai_rfc draft-render` | — (not available in arm C, D42; an operator has `python -m ai_rfc draft render MANIFEST`) |

The `ai_rfc` verbs are unchanged by CLI-1; CLI-3 folds them into `ai-rfc`.

Arm C is frozen at its pre-v2 surface (D42): it never sees the
`structure-upsert` or `draft-render` verbs or the consolidation flags on
`revision-record` and `checkpoint`, though the raw `python -m ai_rfc draft
render` and `draft checkpoint --consolidation` stay reachable behind its
unchanged `Bash(python -m ai_rfc*)` prefix. What D42 freezes is the documented
tool and verb set, not the substrate the prefix reaches. Arms A and B share the
full twenty-tool surface above, so a v2 campaign compares those two.

## Exit codes

Every gate route — MCP tool, `ai_rfc` verb, or raw substrate command — surfaces
the substrate's own exit code untouched, so all three arms read the same
number for the same outcome.

| Code | Meaning |
|---|---|
| 0 | Clean |
| 1 | Inputs unusable — a manifest that will not load, a path that is not a repository |
| 2 | A usage error raised by `argparse` itself: the invocation was malformed |
| 3 | Strict findings — a promotion violation, an unresolved anchor, or a citation the gate refused |

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
