# ai_rfc — reconstruct a specification from a repository's own history

`ai_rfc` mines a software project's history into an RFC-style specification
whose every claim is gated against the evidence behind it. One repository
holds all of it:

- `ai_rfc/` — the deterministic **substrate**: corpus extraction, forge
  snapshots, timeline clustering, evidence views, claim manifests, checkpoints
  and gates. It makes no model calls and opens no socket except in `forge`.
  Its design and schema are documented in `ai_rfc/README.md`.
- `ai_rfc/server/` — the **MCP server** and its parity CLI (`ai_rfc <verb>`):
  one core, two frontends, so an agent cannot overstate what the evidence
  supports whichever door it uses.
- `ai_rfc/experiment/` — the **driver and instrument**: pristine workspaces,
  hermetic `claude -p` sessions, per-cluster sweeps, audit and metrics.
- `plugins/ai-rfc/` — the **Claude Code plugin**: skills, commands, `.mcp.json`.

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

Two variables: `AI_RFC_PYTHON`, the interpreter with the `ai-rfc` distribution
installed (e.g. a venv's `bin/python`); and `AI_RFC_WORKSPACE`, a
reconstruction workspace (clone, corpus, timeline, clusters, checkpoints,
manifest, questions, revisions, draft). Missing either fails loudly; nothing
guesses.

## Two doors, one behaviour

`ai-rfc <verb>` and `python -m ai_rfc <verb>` are the same dispatcher over the
same eight programs (`history`, `forge`, `timeline`, `views`, `check`,
`draft`, `coverage`, `pipeline`), each also reachable as
`python -m ai_rfc.<sub>`. Exit codes everywhere: 0 clean, 1 unusable input,
2 malformed invocation (argparse), 3 strict findings.

## Experiment harness

`python -m ai_rfc.experiment` runs from any directory: `profile init`,
`preflight`, `render`, `workspace prepare|reseal`, `campaign init`, `run`,
`audit`, `questions`, `analyze`. State lives under `AI_RFC_EXPERIMENTS_ROOT` (default
`~/ai-rfc-experiments`), never inside a repository. The first full campaign is
reported in `docs/experiments/2026-08-31-pilot-aioquic.md`; the protocol is
`docs/experiment-protocol.md`; the tool-to-CLI parity table is
`docs/parity.md`. A whole-repository sweep is a target whose window spans every
cluster, run with `--session-mode per-cluster`; see `ai_rfc/experiment/per_cluster.py`.

## Tests

```bash
pip install -e '.[mcp,tests,dev]'
pytest -n auto                 # tests/substrate, tests/server, tests/experiment
```
