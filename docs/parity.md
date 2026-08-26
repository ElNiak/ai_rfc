# Tool ↔ CLI parity table

The instrument of the AI+MCP vs AI+CLI comparison. Both frontends call the
same core functions — the parity test suite keeps every write byte-identical
and every read JSON-identical across arms, and a test fails if a tool is
missing from this table.

| MCP tool | `arfc` verb | Raw substrate command (when one exists) |
|---|---|---|
| `arfc_status` | `arfc status` | — (composite over report.json, timeline.json, questions.yaml, git describe) |
| `arfc_corpus_query` | `arfc corpus-query SQL` | `history.index.open_index` (Python) |
| `arfc_cluster_next` | `arfc cluster-next` | — (clusters.jsonl minus checkpoints/revisions) |
| `arfc_cluster_get` | `arfc cluster-get ID [--patch]` | `cat clusters/<id>/view.json`, `span.diff`, `evidence/pr.json` |
| `arfc_claim_upsert` | `arfc claim-upsert ID --text … --anchor …` | — (schema-validated write; hand-editing + linter is the unguarded fallback) |
| `arfc_claim_adjudicate` | `arfc claim-adjudicate` | `python -m …a_rfc <manifest> --out …` → report.json `claims` |
| `arfc_claim_record_status` | `arfc claim-record-status [IDS…]` | — (writes exactly the supported values) |
| `arfc_question_draft` | `arfc question-draft TEXT --claim ID…` | — (strict register write) |
| `arfc_question_export` | `arfc question-export` | — |
| `arfc_answer_record` | `arfc answer-record QID --answer … --transcript … --quote …` | — (verbatim-quote + exact-wording guardrails) |
| `arfc_revision_record` | `arfc revision-record TAG --cluster ID --normative/--no-normative --note …` | — (validated via the gate's own loader) |
| `arfc_checkpoint` | `arfc checkpoint ID` | `python -m …a_rfc.draft checkpoint …` |
| `arfc_gate` | `arfc gate [--strict]` | `python -m …a_rfc <manifest> --out … --repo … [--strict]` |
| `arfc_citation_gate` | `arfc citation-gate [--strict]` | `python -m …a_rfc.draft gate … [--strict]` |
| `arfc_draft_commit` | `arfc draft-commit -m MSG` | `git -C draft add -A && git -C draft commit -m MSG` |
| `arfc_revision_tag` | `arfc revision-tag TAG -m MSG` | `git -C draft tag -a TAG -m MSG`, then `python -m …a_rfc.draft gate … --strict` (the tool deletes the tag on findings; the raw route leaves that to the author) |

Asymmetries accepted and measured, not hidden: the raw-CLI arm can hand-edit
YAML (the gate catches overstatement after the fact, where the tool arm's
`claim_upsert` refuses it up front), and has no single-call equivalent for
the register/answer guardrails. The raw arm's only corpus-index path is the
`sqlite3` CLI over `corpus/index.sqlite` (the index is derived and
disposable; a write through it is detected by nothing), and its tag is not
rolled back on citation findings.
