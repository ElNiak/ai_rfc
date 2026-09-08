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
PANTHER's `ai-rfc` subcommand forwards everything after `ai-rfc` to this tool's
own dispatcher, exiting with whatever it returns, so `panther ai-rfc` and
`ai-rfc` cannot disagree about a verb, a flag or an exit code.

## Reconstructing a specification

One config file and six commands. `ai-rfc` is the only door an operator needs:
`init`, `run` and `verify` perform the substrate programs a reconstruction
needs, with every path worked out from the config, so the explicit-path forms
documented in `ai_rfc/README.md` are for driving one stage by hand.

```bash
ai-rfc config example > recon.yaml    # a starter recon.yaml, every field documented
$EDITOR recon.yaml
ai-rfc doctor                         # claude, profile, toolchain, forge token, deps, workspace
ai-rfc init   --config recon.yaml     # networked once: clone at the pin, fetch the forge,
                                      # scaffold the draft, seal the config
ai-rfc run    --config recon.yaml     # history, timeline, views; stop at the agent boundary
ai-rfc status --config recon.yaml     # stage states, the cluster ledger, the pin, config drift
ai-rfc verify --config recon.yaml --strict   # every gate the workspace can pass, one exit code
```

`run` performs every deterministic stage that is next and then **stops** at the
agent boundary, printing the ledger and whose turn it is: mining a cluster's
claims needs a model session, and nothing in the substrate calls one. Driving
those sessions from `run` is CLI-2's work; until it lands they are driven by
the plugin's loop skill or by the experiment instrument below. `verify` skips a
check whose inputs do not exist yet, so 0 means nothing that ran failed rather
than that everything ran — its last line tallies what ran and names every skip.

Two more lifecycle verbs stand outside that flow: `ai-rfc toolchain provision`
installs the Internet-Draft toolchain once over the network and `ai-rfc
toolchain verify` re-checks it offline, and `ai-rfc config reference` prints the
`recon.yaml` field table both `config example` and the loader are rendered from,
so neither can drift from what the loader accepts.

## Environment contract

`AI_RFC_CONFIG` names the `recon.yaml` a reconstruction is described by.
`init`, `run`, `status` and `verify` read it when `--config` is not given and
refuse with `no config: pass --config or set AI_RFC_CONFIG` when neither is;
`doctor` takes it optionally and falls back to generic checks. `config` and
`toolchain` take no config at all.

Two variables are required by the plugin and the server: `AI_RFC_PYTHON`, the
interpreter with the `ai-rfc` distribution installed (e.g. a venv's
`bin/python`), read by the plugin's `.mcp.json`; and `AI_RFC_WORKSPACE`, a
reconstruction workspace (clone, corpus, timeline, clusters, checkpoints,
manifest, questions, revisions, draft), read by the MCP server and the
`ai_rfc` parity verbs until CLI-3 derives it from the config. Missing either
fails loudly; nothing guesses. The manifest's `structures:` registry is
rendered by the tool — `ai_rfc_draft_render`, or `ai_rfc draft-render` — and
the blocks it emits are pasted into the draft and never hand-edited there.

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
| `ai-rfc <verb>` = `python -m ai_rfc <verb>` | The one door: a single dispatcher (`ai_rfc/cli.py`) over the seven lifecycle verbs `config`, `init`, `run`, `status`, `verify`, `toolchain`, `doctor` and the eight programs `history`, `forge`, `timeline`, `views`, `check`, `draft`, `coverage`, `pipeline`, each also reachable as `python -m ai_rfc.<sub>` | What a person, or the raw experiment arm, runs |
| `ai_rfc <verb>` (underscore) | The parity CLI (`ai_rfc/server/cli.py`): twenty workspace-level verbs, one per MCP tool, over the same core the server uses | What the AI+CLI experiment arm runs through Bash |
| `python -m ai_rfc.server` | The stdio MCP server exposing the same twenty operations as `ai_rfc_*` tools | What Claude Code mounts from the plugin's `.mcp.json`, and what the AI+MCP arm gets |

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

## The experiment instrument

The three-arm comparison is a separate program from the operator's flow above,
kept because it measures the tool rather than uses it.

```bash
python -m ai_rfc.experiment workspace prepare --config recon.yaml
```

A pristine workspace is built from the same `recon.yaml` the lifecycle verbs
read; the closed target table `{aioquic, mark}` it used to be selected from is
gone. The rest of the instrument runs from any directory: `profile init`,
`preflight`, `render`, `workspace prepare|reseal|migrate-draft`, `campaign
init`, `run`, `audit`, `questions`, `analyze`, `optimize`. Provisioning the
Internet-Draft toolchain is no longer one of them — that is `ai-rfc toolchain
provision|verify`. State lives under `AI_RFC_EXPERIMENTS_ROOT` (default
`~/ai-rfc-experiments`), never inside a repository. The first full campaign is
reported in `docs/experiments/2026-08-31-pilot-aioquic.md`; the protocol is
`docs/experiment-protocol.md`; the tool-to-CLI parity table is
`docs/parity.md`; the harness's own usage page is `ai_rfc/experiment/README.md`.
A whole-repository sweep is a `recon.yaml` whose window spans every cluster, in
a campaign initialised with `--session-mode per-cluster`; see
`ai_rfc/experiment/per_cluster.py`. The draft repository is scaffolded as a
template adopter (`Makefile`, `.gitignore`, `.editorconfig`); the shared
library lives under `<root>/tools/i-d-template`. `campaign init` does not
read `AI_RFC_TOOLCHAIN`; it takes `--toolchain` (defaulting to
`<root>/tools/toolchain.json` when that file exists) and verifies the
record by default.

**`reconstructions/mark` exits 3 under the strict gate, and should.** Running
`draft gate --strict` directly on PANTHER's `reconstructions/mark` reports one
finding: `draft-elniak-mark-reconstructed-01: recorded as a normative change,
but its checkpoint manifest is identical to the previous revision's`. Two of
that artifact's checkpoints, `c0049-pr-ba8ca432c304` and
`c0069-epoch-b901f36095d7`, hold byte-identical manifests while both revisions
record `normative_change: true`. This is a data defect in that reconstruction,
not a gate regression: the rule says a revision claiming to change the
specification must change the manifest the specification is made of, and there
two consecutive revisions did not. Preparing MARK is unaffected — `init`
resets `revisions.yaml`, so a campaign starting from a prepared workspace never
carries those two entries.

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
changed (0.45), whether those claims are cited from a normative statement in
the prose, meaning a paragraph that carries the claim's own BCP 14 keyword
rather than a bare list of ids (0.25), how cleanly
the tagged draft compiles (0.20) and how few turns it took (0.10), scaled by
how much of the cluster's file set the new claims reach. The interview
evaluation is scored on its own terms, with two further hard zeros: altering
the planted transcript, and signing off a claim the author did not confirm word
for word. Its feedback names the planted roles (exact, paraphrase, correction),
never the claim ids: the proposer reads that feedback round after round, and
which id the author confirmed verbatim is the answer key.

**The environment.** The backend installs only under the `optimize` extra, on
Python 3.11; the rest of the harness runs on 3.10. `gepa` is pinned to a git
commit — the released 0.1.4 carries no `optimize_anything`.

```bash
python3.11 -m venv ~/ai-rfc-experiments/venv-optimize
SSLKEYLOGFILE= ~/ai-rfc-experiments/venv-optimize/bin/python -m pip install \
    --timeout 120 --retries 5 -e '.[optimize,tests]'
```

Keep that environment outside the checkout. In this project's worktrees on
macOS, every `.pth` file under the tree acquires the hidden flag within about
ninety seconds of being written or un-hidden (observed 2026-09-08; the process
doing it is not identified, and clearing the flag does not hold), and an
interpreter that skips hidden `.pth` files — 3.11.9 does, 3.10.12 does not —
then loses an editable install placed there in every subprocess: the strict
gates report `No module named 'ai_rfc'` while in-process tests still pass.
The long timeouts are not decoration: the default retry dies on
`files.pythonhosted.org`. An optimization must also run with any command
sandbox off, because the backend always stands up an eval server and that
binds a TCP socket.

A pilot also needs the `claude` CLI on `PATH` and an authenticated profile:
`python -m ai_rfc.experiment profile init` creates one and prints the one-time
`claude auth login` command for it. No API key is needed or read by the
`claude-cli:` forms this project uses.

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
without a network call, reports a clean compile without running a toolchain,
and proposes the seed straight back, so no model is ever paid. Two of the four
graded terms — the judge's relevance and the draft's compile — are therefore
constants rather than measurements. A rehearsal proves the wiring; its scores
mean nothing beside a pilot's. It still
freezes real campaigns and runs them, so it needs a `--toolchain` record and a
scenario for the fake agent to replay at
`<profile-dir>/fake-scenarios/default.json`. Because the build is stubbed here,
any well-formed record will do: the executables it names are never invoked.
`--max-evals` defaults to three per example, which is one whole round; below
that the proposal is never scored and the search only looks converged.

**Stage `pilot`** spends and says so first. It refuses to start unless
`--max-evals`, `--model`, `--reflection-lm` and `--judge-model` are all given —
nothing that costs is defaulted — then prints the worst case and stops until
`--yes`. The proposer and the judge each take one of two forms:

- `claude-cli:<model>` runs the role through `claude -p` on the profile under
  `--profile-dir`, with the prompt on stdin, every customization source
  disabled, and no tools. Nothing bills a key: every call draws on the
  subscription behind that profile, whose usage limit is the only meter. The
  proposer runs at `--effort` with `--timeout-s`; the judge at low effort with
  a 120 s cap. `--max-token-cost` is refused beside a `claude-cli:` proposer,
  because gepa meters a callable at 0.00 and the cap would be a promise nothing
  enforces; `--max-evals` and `--timeout-s` are the caps, and the worst case is
  printed in sessions and calls: twice `--max-evals` harness sessions, up to
  `--max-evals` proposer calls, and one judge call per anchored claim per
  evaluation. The CLI exposes no temperature, so a rerun may grade a hunk
  differently; no judge cache is wired in the CLI today. This is the form this
  project's pilot uses.
- A LiteLLM id for `--reflection-lm` and an Anthropic API id for
  `--judge-model` bill `ANTHROPIC_API_KEY`, which must then be set. The worst
  case is printed in USD: twice `--max-evals` × the largest example budget,
  plus `--max-token-cost`, which is required here and binds only for an id
  litellm can price — the pilot refuses an unpriced one. The factor of two is
  the evaluator's one retry per faulted run; judge calls sit on top of that
  figure. This project does not use this form.

The two forms may also be mixed — a `claude-cli:` proposer beside an Anthropic
API judge, or a LiteLLM proposer beside a `claude-cli:` judge — and each mixture
still needs `ANTHROPIC_API_KEY` for the role that names the API, without which
the pilot refuses to start.

Stage `fake` sets `LITELLM_LOCAL_MODEL_COST_MAP=True`, so a rehearsal never
fetches litellm's cost map, and refuses any `--reflection-lm` or
`--judge-model`: it proposes the seed back and rates every claim itself.

A pilot builds each draft for real, so its `--toolchain` record must name
executables that exist: use one from `ai-rfc toolchain provision`.
Everything lands under
`<root>/optimize/<name>/`. A second run over an existing one **resumes** rather
than starting over; `touch <root>/optimize/<name>/gepa/gepa.stop` is the
graceful stop.

**Applying what it found.** `optimize apply <candidate> --plugin-root PATH`
decodes the candidate against the plugin, writes the three prose bodies under
the frontmatter each skill already carries, writes the loop template, and
regenerates the loop SKILL.md from that file. It refuses first if any of those
files holds work nobody committed — overwritten, it would be indistinguishable
from the candidate in the diff — unless `--force` says otherwise. It prints
`git diff --stat` and commits nothing: the result is a working tree for a
person to read, reject or keep. Re-run `pytest tests/experiment/test_render.py`
afterwards — it pins the committed skill to the template.

## Tests

```bash
pip install -e '.[mcp,tests,dev]'
pytest -n auto                 # tests/substrate, tests/server, tests/experiment
```
