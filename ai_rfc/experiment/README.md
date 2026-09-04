# `experiment/` — the harness that launches agents

This is the only code in the repository that starts a language model. It runs
`claude -p` sessions over pristine copies of a reconstruction workspace, one
per run, confines each session to the surface its **arm** declares, records
everything the session emits, and recomputes every outcome from the artifacts
the session left behind. The protocol it implements is `docs/experiment-protocol.md`;
the spike that proved the CLI can be confined at all is `docs/spike-s0.md`; the
first campaign is reported in `docs/experiments/2026-08-31-pilot-aioquic.md`.

Nothing here uses the Agent SDK, subagents, teams or parallel sessions. A run
is one `subprocess.Popen` of the `claude` binary (or, under `--session-mode
per-cluster`, several in sequence), and the MCP server is that process's
child, started by `claude` from a per-run config — never by the harness.

## Where state lives

Everything is kept under `AI_RFC_EXPERIMENTS_ROOT` (default
`~/ai-rfc-experiments`), deliberately outside every repository so no
`CLAUDE.md` ancestry can leak into a session:

```
<root>/
  profile/                      CLAUDE_CONFIG_DIR every session runs under
  pristine/<target>-wLO-HI/     a prepared workspace, digest-manifested
  campaigns/<id>/
    campaign.json               the frozen run matrix and every pinned input
    prompts/arm-A.md …          rendered system prompts, task.md, diff-*.patch
    bin/ai_rfc                  the shim arm B's Bash reaches
    runs/<run-id>/              one directory per launch (see below)
    audit/                      per-run integrity verdicts
    analysis/                   aggregate.json and report.md
```

## The verbs, in workflow order

| Verb | What it does |
|---|---|
| `profile init` | Create the isolated `CLAUDE_CONFIG_DIR` under `<root>/profile` |
| `preflight` | S0: fourteen real `claude -p` calls, one dollar each, proving the profile is hermetic and that the `PreToolUse` guard actually blocks. Re-run whenever the CLI version moves |
| `render` | Regenerate `plugins/ai-rfc/skills/ai-rfc-reconstruction-loop/SKILL.md` from `prompts/loop.tmpl.md` (see "Prompts") |
| `workspace prepare TARGET` | Build a pristine workspace: copy `clone/`, `corpus/`, `timeline/` and the pinned forge snapshot from the source reconstruction, emit and re-verify every view, write an empty manifest and registers, scaffold the draft repository from the pinned Internet-Draft template, mark every cluster outside the window as processed by harness-authored checkpoints, and write `pristine.sha256` and `pristine.json` |
| `workspace reseal WORKSPACE --as NAME` | Turn a stopped run's workspace into a new pristine baseline, by copy; the run directory is evidence and is never modified |
| `campaign init` | Freeze a run matrix: arms, repeats, seeded interleaved order, model, effort, budget, timeout, the resolved `claude` binary and version, the rendered prompts and their digests, the pristine digest, git describes of both checkouts. Runs the parity suite first unless `--skip-parity` |
| `run CAMPAIGN [--only IDS]` | Launch pending runs in the frozen order; a run with a `status.json` is skipped, a run directory without one is refused as an interrupted launch |
| `audit CAMPAIGN` | Classify every tool call in every transcript by surface and judge arm integrity |
| `questions RUN_DIR [--all]` | List the developer questions a run left open in its workspace register |
| `analyze CAMPAIGN` | Recompute every outcome from artifacts and write `analysis/aggregate.json` and `analysis/report.md`; idempotent |

Run ids are `<arm><block>` — `A1`, `C1`, `B1`, `B2`, … — and the order is a
seeded shuffle inside each repeat block, so a campaign replays identically.

Both `workspace prepare` and `campaign init` take `--panther-repo`: the source
reconstructions the targets are built from (`reconstructions/aioquic`,
`reconstructions/mark`, and their pinned forge snapshots) live in the PANTHER
checkout, not in this repository. The two targets are declared in
`workspace.py` (`TARGETS`): `aioquic`, window 2–11, and `mark`, window 1–69.
A production sweep is simply a target whose window spans every cluster, run
with `--session-mode per-cluster`.

## Arms

An arm is the surface a session may reach, not a different agent or prompt.
`arms.py` declares them; the protocol's §1 explains the three-class taxonomy
they instantiate.

| Arm | Surface | Built-in tools | Allowlist | MCP |
|---|---|---|---|---|
| A | class 1, structured-typed: the sixteen `ai_rfc_*` MCP tools | Read, Edit, Write, Grep, Glob | those plus `mcp__ai_rfc` | mounted from a per-run `ai_rfc.json` |
| B | class 2, hybrid: the `ai_rfc <verb>` parity CLI through Bash | the same plus Bash | `Bash(ai_rfc *)` | not mounted |
| C | class 2, hybrid: raw substrate commands through Bash | the same plus Bash | `Bash(python -m ai_rfc*)`, `Bash(git *)`, `Bash(sqlite3 *)` | not mounted |

Enforcement is by removal (arm A has no Bash tool) or by a `PreToolUse` hook
(`guard.py`, mounted through `--settings`) that exits 2 for any Bash command
outside the arm's prefixes. The hook exists because `--allowedTools` does not
confine a built-in tool that `--tools` enabled — a measured property of the
installed CLI, recorded in `enforcement.py` and re-checked by `preflight`. A
blocked call is kept as data: the audit counts it as a bypass attempt, and an
*executed* call outside the arm is an integrity violation.

## One run

`runner.py` assembles the launch and `spawn.py` executes it:

1. Copy the pristine workspace into `runs/<id>/workspace`.
2. For arm A, write `ai_rfc.json` naming the interpreter and
   `-m ai_rfc.server` with `AI_RFC_WORKSPACE` set; for every arm, write the
   guard settings.
3. Build the argv with `arms.claude_argv()`: the shared flags
   (`--output-format stream-json --verbose --include-hook-events
   --append-system-prompt-file prompts/arm-<X>.md --disable-slash-commands
   --setting-sources project --model … --effort … --permission-mode dontAsk
   --max-budget-usd …`) plus the arm's `--tools`, `--allowedTools`,
   `--strict-mcp-config` and, for A, `--mcp-config`.
4. `Popen` with a minimal environment, `stdin` closed, its own session, stdout
   streamed to `events.jsonl` as it arrives, stderr to `stderr.log`. On the
   wall-clock cap the whole process group gets SIGTERM, then SIGKILL after a
   thirty-second grace, so the MCP server dies with the session.
5. Write `status.json` exactly once. A run is never relaunched in place.

The run directory keeps `argv.json`, `env.json`, `prompt.md` (a copy of the
arm prompt), `guard.json`, `events.jsonl`, `stderr.log`, `result.json` and
`status.json`; per-cluster mode adds `sessions.jsonl`.

**Session modes.** `single` gives the whole window to one session. `per-cluster`
(`per_cluster.py`) launches one session per cluster, sequentially, each with a
one-cluster window, retrying a cluster a bounded number of times and appending
every session's output to the same transcript. Progress is the workspace —
checkpoints, revision entries and tags on disk — so a killed sweep resumes
where the artifacts say it stopped, and the audit, metrics and report cannot
tell which mode produced a run.

## Prompts

`prompts/loop.tmpl.md` is the single source of the reconstruction loop. Its
`{{slot}}` placeholders name operations (`cluster_next`, `claim_upsert`,
`gate`, …); `render.py` fills them from one of four invocation tables:

- `interactive` → the plugin's `ai-rfc-reconstruction-loop/SKILL.md`, written
  by `render` and pinned by `tests/experiment/test_render.py`;
- `A`, `B`, `C` → each arm's system prompt, which is the rendered loop followed
  verbatim by the arm-neutral `ai-rfc-rfc-style/SKILL.md`, its
  `references/claim-citation.md`, and `ai-rfc-evidence-hygiene/SKILL.md`,
  frontmatter stripped.

So the arms differ only where a slot names the arm's surface, and
`campaign init` writes the pairwise `diff-*.patch` files that prove it.
`prompts/task.md` is the task prompt, identical across arms with the window
substituted; `prompts/draft-skeleton.md` seeds the draft repository.

## Measurement

`stream.py` reads the stream-json events; `progress.py` narrates a run in one
line and may never end one. `audit.py` classifies calls by surface, splitting
errors into the class-1 channel (typed tool errors) and the class-2 channel
(shell errors). `metrics.py` trusts nothing the model said: completion is read
from checkpoints, revision entries and tags, the strict gates are re-run on a
scratch copy of the final workspace, and cost comes from the result event.
`summary.py` is the one reader of the model's own account (the `note` in
`revisions.yaml`), which is why it is not part of `metrics`. `report.py`
renders the aggregate with every formula named.

## Tests

`tests/experiment/` drives the harness against `fake_claude/claude`, a
stand-in that replays a scenario as stream-json and really mutates the
workspace through the server core, so the runner, driver, per-cluster loop,
audit and metrics are exercised without spending anything. `preflight` is the
exception: its calls are real and are made once by hand.
