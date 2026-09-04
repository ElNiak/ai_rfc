# ai_rfc — reconstruct a specification from a repository's own history

`ai_rfc` mines a software project's history into an RFC-style specification
whose every claim is gated against the evidence behind it. One repository
holds all of it:

- `ai_rfc/` — the deterministic **substrate**: eight programs for corpus
  extraction, forge snapshots, timeline clustering, evidence views, claim
  manifests, checkpoints and gates. The substrate makes no model calls and
  opens no socket except in `forge`; the two subpackages below live under the
  same directory but are not substrate. Its design and schema are documented
  in `ai_rfc/README.md`.
- `ai_rfc/server/` — the **MCP server** and its parity CLI (`ai_rfc <verb>`):
  one core, two frontends, so an agent cannot overstate what the evidence
  supports whichever door it uses. Tool-to-verb table: `docs/parity.md`.
- `ai_rfc/experiment/` — the **driver and instrument**: pristine workspaces,
  hermetic `claude -p` sessions, per-cluster sweeps, audit and metrics. The
  only code in the repository that launches an agent. Usage:
  `ai_rfc/experiment/README.md`; design: `docs/experiment-protocol.md`.
- `plugins/ai-rfc/` — the **Claude Code plugin**: four skills, five commands,
  `.mcp.json`. See "Plugin" below.

## Install

```bash
pip install -e '.[mcp]'        # the substrate, the server and the ai-rfc door
ai-rfc --help                  # every verb, in workflow order
claude plugin marketplace add /path/to/ai_rfc && claude plugin install ai-rfc
```

The plugin runs `${AI_RFC_PYTHON} -m ai_rfc.server`, so the distribution must
be installed in the interpreter `AI_RFC_PYTHON` names for the session.

PANTHER consumes this repository as the submodule
`panther/plugins/services/testers/ai_rfc`; `panther build dev` installs it and
PANTHER's `ai-rfc` subcommand forwards to `ai-rfc <verb>`.

## Environment contract

Two variables are required by the plugin and the server: `AI_RFC_PYTHON`, the
interpreter with the `ai-rfc` distribution installed (e.g. a venv's
`bin/python`), read by the plugin's `.mcp.json`; and `AI_RFC_WORKSPACE`, a
reconstruction workspace (clone, corpus, timeline, clusters, checkpoints,
manifest, questions, revisions, draft), read by the server and the `ai_rfc`
CLI. Missing either fails loudly; nothing guesses.

Three more are read where named and are optional there: `AI_RFC_TOOLCHAIN`, a
`toolchain.json` that `draft build` and the pipeline's build stage use when no
`--toolchain` is passed (without either, `draft build` refuses and says so);
the server's `revision_tag` also runs `draft build` before creating the tag
whenever a toolchain is configured, refusing the tag on any build finding,
and `campaign init` refuses to start without a verified toolchain;
`GITHUB_TOKEN` / `GITLAB_TOKEN`, a credential `forge fetch` sends when present
and never stores (without it the discussion endpoints are refused and the
snapshot records the fidelity it reached); and `AI_RFC_EXPERIMENTS_ROOT`, the
experiment harness's state root (default `~/ai-rfc-experiments`).

## Three entry names, two dispatchers

| Entry | What it is | Surface |
|---|---|---|
| `ai-rfc <verb>` = `python -m ai_rfc <verb>` | The substrate door: one dispatcher (`ai_rfc/cli.py`) over the eight programs `history`, `forge`, `timeline`, `views`, `check`, `draft`, `coverage`, `pipeline`, each also reachable as `python -m ai_rfc.<sub>` | What a person, or the raw experiment arm, runs |
| `ai_rfc <verb>` (underscore) | The parity CLI (`ai_rfc/server/cli.py`): sixteen workspace-level verbs, one per MCP tool, over the same core the server uses | What the AI+CLI experiment arm runs through Bash |
| `python -m ai_rfc.server` | The stdio MCP server exposing the same sixteen operations as `ai_rfc_*` tools | What Claude Code mounts from the plugin's `.mcp.json`, and what the AI+MCP arm gets |

The underscore name is interim: the one-door design folds it into `ai-rfc`
(see the pyproject comment on `[project.scripts]`). Exit codes are the same
through every door: 0 clean, 1 unusable input, 2 malformed invocation
(argparse), 3 strict findings.

## Plugin

`plugins/ai-rfc/` is a Claude Code plugin marketplace entry (`.claude-plugin/`
at the repository root). It carries:

| Command | Follows |
|---|---|
| `/ai-rfc-init URL` | Runs the deterministic stages for a fresh workspace and scaffolds the draft |
| `/ai-rfc-next-cluster` | One iteration of the `ai-rfc-reconstruction-loop` skill |
| `/ai-rfc-interview-import PATH` | The `ai-rfc-interviewing` skill |
| `/ai-rfc-release-revision` | The tagging tail of the loop, through the MCP tools or `ai_rfc` verbs |
| `/ai-rfc-status` | A one-screen report computed from the workspace's own artifacts |

Skills: `ai-rfc-reconstruction-loop` (the driver), `ai-rfc-evidence-hygiene`
(claims and anchors), `ai-rfc-rfc-style` with `references/claim-citation.md`
(prose), and `ai-rfc-interviewing` (author feedback). The loop skill is
**generated**: `python -m ai_rfc.experiment render` writes it from
`ai_rfc/experiment/prompts/loop.tmpl.md`, and a test pins the committed file
to that rendering — edit the template, not the skill. The evidence-hygiene and
RFC-style texts are hand-written and are also inlined verbatim into every
experiment arm's system prompt, so one edit reaches both the plugin and the
harness.

## Experiment harness

`python -m ai_rfc.experiment` runs from any directory: `profile init`,
`preflight`, `render`, `workspace prepare|reseal|migrate-draft`, `toolchain
provision|verify`, `campaign init`, `run`, `audit`, `questions`, `analyze`.
State lives under `AI_RFC_EXPERIMENTS_ROOT` (default
`~/ai-rfc-experiments`), never inside a repository. The first full campaign is
reported in `docs/experiments/2026-08-31-pilot-aioquic.md`; the protocol is
`docs/experiment-protocol.md`; the tool-to-CLI parity table is
`docs/parity.md`; the harness's own usage page is `ai_rfc/experiment/README.md`.
A whole-repository sweep is a target whose window spans every cluster, in a
campaign initialised with `--session-mode per-cluster`; see
`ai_rfc/experiment/per_cluster.py`. The draft repository is scaffolded as a
template adopter (`Makefile`, `.gitignore`, `.editorconfig`); the shared
library lives under `<root>/tools/i-d-template`.

## Tests

```bash
pip install -e '.[mcp,tests,dev]'
pytest -n auto                 # tests/substrate, tests/server, tests/experiment
```
