# the model-driven layer over PANTHER's ai_rfc substrate

Two things share the name now, so this README keeps them apart: the
**substrate** is the `ai_rfc` package inside PANTHER, and **this repository**
is the driver over it, checked out at `ai_rfc/harness/`.

The substrate is deterministic: it extracts a repository's
history into a citable corpus, clusters it into a PR/epoch timeline, emits
per-cluster evidence views, and gates claim manifests and prose drafts. It
deliberately makes no model calls — "mining is model-driven and lives in
agents outside the framework". **This repository is that outside layer**: a
claude-code plugin whose skills, commands and MCP server drive the
reconstruction loop, one cluster at a time, without ever being able to
overstate what the evidence supports.

## Install

```bash
claude plugin marketplace add /path/to/ai_rfc
claude plugin install ai-rfc
```

## Environment contract

Everything here operates on one PANTHER checkout and one reconstruction
workspace, named by two required environment variables:

| Variable | Meaning |
|---|---|
| `PANTHER_REPO` | Root of a PANTHER checkout (the `ai_rfc` substrate) |
| `AI_RFC_WORKSPACE` | One reconstruction workspace (corpus, timeline, clusters, checkpoints, manifest, questions, revisions, draft) |

Missing either fails loudly; nothing guesses.

## Layout

- `plugins/ai-rfc/skills/` — `ai-rfc-reconstruction-loop` (the driver),
  `ai-rfc-rfc-style` (I-D prose + claim-citation discipline),
  `ai-rfc-interviewing` (question register round-trip),
  `ai-rfc-evidence-hygiene` (the promotion rule as working intuition).
- `plugins/ai-rfc/commands/` — `/ai-rfc-init`, `/ai-rfc-next-cluster`,
  `/ai-rfc-interview-import`, `/ai-rfc-release-revision`, `/ai-rfc-status`.
- `plugins/ai-rfc/server/` — the `ai_rfc` MCP server and its parity CLI:
  one core, two frontends, so the AI+MCP and AI+CLI experiment arms are
  capability-identical by construction.
- `docs/parity.md` — the tool ↔ CLI parity table (the experiment
  instrument).

## Guardrails the tools enforce

- `ai_rfc_claim_upsert` rejects any `status` input — a claim's standing is
  adjudicated from its evidence, never asserted.
- `ai_rfc_answer_record` grants `signed_off_by` only when the author
  confirmed the exact claim wording; a paraphrase earns an interview
  anchor, not a sign-off.
- `ai_rfc_revision_tag` creates a tag only after the strict manifest gate
  passes and deletes it again if the strict citation gate finds anything.
- Every write is atomic (temp + rename); every gate failure is surfaced
  verbatim, never worked around.

## Experiment harness

`experiment/` runs the AI+MCP vs AI+CLI comparison the protocol in
`docs/experiment-protocol.md` describes: `python -m experiment profile init`
creates the isolated Claude Code profile, `workspace prepare` builds a
pristine reconstruction workspace, and the campaign commands (see the
harness plan) launch, audit and analyze runs. State lives under
`AI_RFC_EXPERIMENTS_ROOT` (default `~/ai-rfc-experiments`), never inside a
repository.

**Run every `python -m experiment` command from this directory.** This
repository is nested inside PANTHER but is not a package of it: there is no
`__init__.py` here, deliberately, because adding one would give `experiment` two
import identities — `experiment` and the dotted path through PANTHER — and so
two module objects with two sets of module-level state. `experiment` therefore
resolves only when this directory is what Python searches, which means running
from here or putting it on `PYTHONPATH` yourself.

The first full campaign is reported in
[`docs/experiments/2026-08-31-pilot-aioquic.md`](docs/experiments/2026-08-31-pilot-aioquic.md)
— six runs over aioquic, all three arms completing the whole window, with the
cost, bypass and hand-edit differences between them, and the defaults it
settles for the main run.

### A production sweep is a target, not a driver

Reconstructing a whole repository needs no separate machinery. It is a target
whose window spans every cluster, launched with `--session-mode per-cluster`:

```bash
python -m experiment workspace prepare --target mark --panther-repo <PANTHER>
python -m experiment campaign init --id mark-full --baseline <pristine> \
    --arms A --repeats 1 --session-mode per-cluster \
    --budget 200 --timeout 86400 --panther-repo <PANTHER>
python -m experiment run <campaign-dir>
```

`per_cluster.run_per_cluster` spawns one session per outstanding cluster and
derives which cluster that is from the workspace — checkpoints, revision
entries and tags on disk — so a killed run resumes where the artifacts say it
stopped rather than where a counter thought it was. `--budget` caps the **run**,
not the session: each session is handed what the run has left, so sixty-nine
clusters cannot spend sixty-nine times the flag. A cluster that will not finish
in `ATTEMPTS_PER_CLUSTER` halts the sweep rather than being skipped, because
later prose builds on earlier prose and a draft with a hole in it is worse than
a short one.

Pick the arm from the pilot's measurements rather than by preference: arm A
completed the window at $1.90 per cluster with zero hand edits, against $2.27
and $2.36 for B and C.

Measure the result with `ai_rfc.draft completeness`, which reports the clusters
that produced no claim and the claims no revision cites. `draft gate` cannot
answer that — it checks consistency, which doing nothing preserves.
