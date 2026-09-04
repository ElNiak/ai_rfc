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
whenever a toolchain is configured, refusing the tag on any build finding;
`GITHUB_TOKEN` / `GITLAB_TOKEN`, a credential `forge fetch` sends when present
and never stores (without it the discussion endpoints are refused and the
snapshot records the fidelity it reached); and `AI_RFC_EXPERIMENTS_ROOT`, the
experiment harness's state root (default `~/ai-rfc-experiments`).

## Three entry names, two dispatchers

| Entry | What it is | Surface |
|---|---|---|
| `ai-rfc <verb>` = `python -m ai_rfc <verb>` | The substrate door: one dispatcher (`ai_rfc/cli.py`) over the eight programs `history`, `forge`, `timeline`, `views`, `check`, `draft`, `coverage`, `pipeline`, each also reachable as `python -m ai_rfc.<sub>` | What a person, or the raw experiment arm, runs |
| `ai_rfc <verb>` (underscore) | The parity CLI (`ai_rfc/server/cli.py`): eighteen workspace-level verbs, one per MCP tool, over the same core the server uses | What the AI+CLI experiment arm runs through Bash |
| `python -m ai_rfc.server` | The stdio MCP server exposing the same eighteen operations as `ai_rfc_*` tools | What Claude Code mounts from the plugin's `.mcp.json`, and what the AI+MCP arm gets |

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
library lives under `<root>/tools/i-d-template`. `campaign init` does not
read `AI_RFC_TOOLCHAIN`; it takes `--toolchain` (defaulting to
`<root>/tools/toolchain.json` when that file exists) and verifies the
record by default.

## Optimising the plugin skills with GEPA

`python -m ai_rfc.experiment optimize` searches for better skill texts by
measuring them: a backend proposes a rewrite, the harness freezes a campaign
on it, drives one real agent session, and hands back a score. Four texts
travel as one delimited candidate — `ai_rfc/experiment/prompts/loop.tmpl.md`
and the bodies of the `ai-rfc-evidence-hygiene`, `ai-rfc-interviewing` and
`ai-rfc-rfc-style` skills. Everything else is fixed: each skill's frontmatter,
the `references/` files, and the task prompt every session is given. A
candidate that drops a section, carries a frontmatter block, loses one of the
loop template's `{{slot}}` placeholders, or shrinks below a quarter or grows
past twice the seed body is rejected unmeasured, with the broken rules named.

**The score.** A loop evaluation is one session given one cluster. Two
preconditions score zero on their own: the session must finish the cluster —
claiming, checkpointing, writing prose, tagging a revision — and must not have
hand-edited the claim register or the interview transcripts, nor worked
outside the tools it was offered. Past those, the value is a weighted sum of
how well a judge rates each newly anchored claim against the code that cluster
changed (0.45), whether those claims are cited in the prose (0.25), how cleanly
the tagged draft compiles (0.20) and how few turns it took (0.10), scaled by
how much of the cluster's file set the new claims reach. The interview
evaluation is scored on its own terms, with two further hard zeros: altering
the planted transcript, and signing off a claim the author did not confirm word
for word.

**The environment.** The backend installs only under the `optimize` extra, on
Python 3.11; the rest of the harness runs on 3.10. `gepa` is pinned to a git
commit — the released 0.1.4 carries no `optimize_anything`.

```bash
python3.11 -m venv .superpowers/venv-optimize
SSLKEYLOGFILE= .superpowers/venv-optimize/bin/python -m pip install \
    --timeout 120 --retries 5 -e '.[optimize,tests]'
```

The long timeouts are not decoration: the default retry dies on
`files.pythonhosted.org`. An optimization must also run with any command
sandbox off, because the backend always stands up an eval server and that
binds a TCP socket.

**The examples file** is JSON, one entry per thing a candidate is measured on.
A loop entry names the single in-window cluster it scores; an interview entry
names a baseline built by `optimize prepare-interview`, which writes a sidecar
beside it recording what was planted. `budget_usd` defaults to 4.0 for a loop
entry and 2.0 for an interview one.

```json
{"examples": [
  {"kind": "loop", "id": "loop-1", "pristine_dir": "/…/pristine/aioquic",
   "cluster_id": "c0002-pr-abcdef", "budget_usd": 4.0},
  {"kind": "interview", "id": "int-1", "pristine_dir": "/…/pristine/interview-fixture"}
]}
```

**Stage `fake`** rehearses the whole loop for nothing: it drives the fake agent
under `tests/experiment/fake_claude/`, rates every anchored claim a perfect fit
without a network call, and proposes the seed straight back, so no model is
ever paid. It still freezes real campaigns and runs them, so it needs a
`--toolchain` record (a stub one is fine) and a scenario for the fake agent to
replay at `<profile-dir>/fake-scenarios/default.json`. `--max-evals` defaults
to three per example, which is one whole round; below that the proposal is
never scored and the search only looks converged.

**Stage `pilot`** spends money and says so first. It refuses to start unless
`ANTHROPIC_API_KEY` is set and `--max-evals`, `--max-token-cost`, `--model`,
`--reflection-lm` and `--judge-model` are all given — nothing that costs is
defaulted. It then prints the worst case (`--max-evals` × the largest example
budget, plus the proposer ceiling) and stops until `--yes`. Everything lands
under `<root>/optimize/<name>/`. A second run over an existing one **resumes**
rather than starting over; `touch <root>/optimize/<name>/gepa/gepa.stop` is the
graceful stop.

**Applying what it found.** `optimize apply <candidate> --plugin-root PATH`
decodes the candidate against the plugin, writes the three prose bodies under
the frontmatter each skill already carries, writes the loop template, and
regenerates the loop SKILL.md from that file. It prints `git diff --stat` and
commits nothing: the result is a working tree for a person to read, reject or
keep. Re-run `pytest tests/experiment/test_render.py` afterwards — it pins the
committed skill to the template.

## Tests

```bash
pip install -e '.[mcp,tests,dev]'
pytest -n auto                 # tests/substrate, tests/server, tests/experiment
```
