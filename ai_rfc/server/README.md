# `server/` — one core, two frontends

Every operation an agent performs on a reconstruction workspace is
implemented once, in `core/`, and exposed twice: as an MCP tool and as an
`ai_rfc <verb>` command. The two frontends are thin — each tool or verb
resolves the workspace and calls exactly one core function — so the AI+MCP and
AI+CLI experiment arms are capability-identical by construction rather than by
assertion. `docs/parity.md` is the row-for-row table, and `tests/server/test_parity.py`
fails when a tool is missing from it.

This package is not substrate. It exists to be driven by a model, and it is
the only place under `ai_rfc/` that *writes* a claim, a question, an answer or
a revision entry on an agent's behalf. It still calls no model and opens no
network socket; the MCP transport is stdio.

## The three names, again

| Name | Module | Who uses it |
|---|---|---|
| `python -m ai_rfc.server` | `server.py`: a `FastMCP("ai_rfc")` instance registering every function in `tools.py` | Claude Code, from the plugin's `.mcp.json` or the experiment's per-run `ai_rfc.json` (arm A) |
| `ai_rfc <verb>` | `cli.py`: argparse over the same core functions, sixteen verbs | The AI+CLI arm through Bash, and a person at a terminal |
| `ai-rfc <verb>` (hyphen) | not this package — `ai_rfc/cli.py`, the substrate dispatcher | Everything deterministic; the raw arm C |

The underscore name is interim: the one-door design folds these verbs into
`ai-rfc`, at which point this package keeps its core and loses its parser.

## Layout

| Module | Holds |
|---|---|
| `paths.py` | `Context`: the resolved `AI_RFC_WORKSPACE` and the paths inside it. Required, never guessed — a tool quietly operating on the wrong workspace is the failure that looks like success |
| `core/queries.py` | Read-only: status, corpus SQL (SELECT only, capped at 200 rows), next cluster, one cluster's evidence with paged patches |
| `core/claims.py` | Schema-validated claim writes that refuse `status`; adjudication preview; recording exactly the supported statuses |
| `core/questions.py` | The question register and the interview-import guardrails (verbatim quote, exact wording) |
| `core/revisions.py` | Revision-map entries, validated through the gate's own loader |
| `core/gates.py` | Checkpoints and the two strict gates, run through the substrate CLIs with their exit codes surfaced untouched |
| `core/draft.py` | Commit prose and tag a revision in the workspace's `draft/` clone; a tag whose citation gate fails is deleted again |
| `tools.py` | The sixteen `ai_rfc_*` callables, importable without the `mcp` package so the parity tests can run where it is not installed |
| `server.py` | FastMCP registration and `run()` |
| `cli.py` | The `ai_rfc` parser and dispatcher |
| `testing.py` | Fixture workspaces built through the substrate's own code, shared by `tests/server/` |

## Errors and exit codes

`CoreError` means the operation cannot be performed as asked; its subclass
`GuardrailError` means it would break an evidence-honesty rule — writing a
`status`, a corpus query that is not a single SELECT, a duplicate open
question, an answer whose quote is not found verbatim in its transcript. The
MCP server surfaces both as tool errors. The CLI reports either on stderr and
exits 1 (the package's "unusable input" code), argparse owns 2, and a gate or
checkpoint verb returns the substrate's own exit code unchanged — 3 for strict
findings — so all three arms read the same number for the same outcome.

## Dependencies

`mcp>=1.0,<2` is an optional extra (`pip install -e '.[mcp]'`) and is imported
only by `server.py`. The bound is deliberate: 2.x renamed `FastMCP`, and a
server that dies at import does not fail the Claude Code session — it just
removes every write and gate tool from it. See the comment in `pyproject.toml`.
