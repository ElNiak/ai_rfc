# aioquic pilot — 2026-08-31

> **SCAFFOLD — NOT A RESULT.** Sections marked _PENDING_ have no data yet. The campaign
> was still running when this file was created; every _PENDING_ marker must be replaced
> with a number copied from `analysis/aggregate.json` or `analysis/report.md` before this
> document is cited for anything. A half-filled report is not a partial result, it is not
> a result. Frozen setup values below are already final and were read from
> `campaign.json`, `pristine.json` and each run's `status.json`.

## Setup

| | |
|---|---|
| Campaign id | `pilot-aioquic-w02-11-20260831` |
| Created | 2026-08-31T09:19:11Z (11:19 CEST) |
| Model / effort | `claude-opus-5` / `high` |
| `claude --version` | **2.1.251 (Claude Code)** — taken from each run's `status.json`, not from whatever the CLI reports at reading time |
| Spike gate | `go: true`, 9/9 checks, re-armed 2026-08-31T09:11:01Z on the same 2.1.251 (`~/arfc-experiments/spike-report.json`) |
| git — `ai_rfc` | `fa51cec` |
| git — PANTHER | `v1.1.3-839-g226608938` |
| Target | aioquic |
| Window | clusters with ordinals **2–11** (10 clusters) |
| Run order | `B1 A1 C1 A2 C2 B2` (seed `20260826`, preregistered) |
| Repeats | 2 per arm |
| Budget cap | $25.00 per run |
| Timeout | 7200 s per run |
| Parity — pre-run | **passed**, `7 passed in 3.56s` |
| Parity — post-run | _PENDING_ — Task 8 Step 5, the stop-ship gate |

**Pristine record.** Clone HEAD `6d36838d008c2202c337142fa07e8bf80e96bac8`; draft template
HEAD `d2f1ee9bf9b867a3d04249d26b786a28468cd430`; forge snapshot
`github.com__aiortc__aioquic/snapshot-2026-08-25T15-16-59Z`; `git version 2.55.0`. The
corpus holds **342 clusters**, of which **332 are pre-seeded** with a `harness.json`,
leaving exactly the 10 in-window clusters for the agent to process.

**Prompt digests** (sha256, frozen at `campaign init`):

| File | Digest |
|---|---|
| `arm-A.md` | `15dd983b7f4bb1cc8df9093e8dbbdcace6c15ff5d7ebf81bd13716606f2552ba` |
| `arm-B.md` | `a3128ee40560543f856f1f6a0e133824be13db0aaa551ecb613db7f65c39d3aa` |
| `arm-C.md` | `967a434bbc02e101343fcbeb9a13368b27c296da17f585217a34e560848ead33` |
| `task.md`  | `05e731d3cb93fd7ffc3ee07c29eceb829ae2a006e18064155a35885592f84539` |

## Results (instrument numbers, not effect claims)

_PENDING_ — three tables, all copied from `analysis/report.md` and `analysis/aggregate.json`:

- Per-arm summary.
- Per-run summary.
- Per-cluster `pass^k`.

**Metric definition in force.** A cluster counts as completed when its checkpoint exists,
its revision entry and tag exist, and both strict gates exit 0 when the harness re-runs
them on the final workspace. The primary metric is completed clusters over the window
size (10), with `pass^k` over repeats. `checked_fraction` is reported only as the
substrate's honesty metric; it moves solely through sign-offs and runtime anchors and is
therefore identically 0.0 in model-only runs, which is why it cannot be the primary
outcome (decision D23).

The design spec previously exempted `normative_change: false` entries from needing a tag.
That line was the only one of four sources to do so — the protocol text, the frozen run
prompt and `metrics.py` all require a tag unconditionally, and the prompt instructs agents
accordingly. It was aligned on 2026-08-31. **Every cluster measured in this pilot carries a
tag, so both readings return identical values and the alignment shifts no number here.**

## Integrity and enforcement

_PENDING_ — from `audit/<id>.json` per run:

- Integrity rate per arm; any `integrity: false` explained from its `executed_out_of_arm` entries.
- Bypass attempts by surface, with 2–3 verbatim examples (tool name + input).
- Denial mechanism observed (`tool_result` text and the result event's `permission_denials`).
- Hand-edit asymmetry counts.

**Guard integrity.** Each run digests its `guard.json` at mount time and the audit
re-hashes and compares. Independently, the live guard *implementation* was digested by hand
at 2026-08-31 11:45:59 CEST, because the harness instruments the settings pointer but not
the code it points at (see Threats observed):

```
guard.py        fd0323846f157bec9210c911e6a36b0e0f6cdb624a9685211a2b047cbcb1cfc1
enforcement.py  e65bca91816c423e6af86522964b7059c4e65990d9739c2cd763bda955ad851e
```

Re-digest result at campaign end: _PENDING_.

## Errors

_PENDING_ — class-1 and class-2 counts per arm; first-failure indices; the three most
common error texts per channel, verbatim.

## Cost and time

_PENDING_ for the full table. Per run: cost, tokens by class, turns, duration, wall time.
Then projected main-run cost = mean cost per run × (arms × targets × k), for k = 3 and
k = 5; and cache share of tokens.

Observed so far, read from each run's `status.json` and `result.json` (to be superseded by
the aggregate, which is the citable source):

| Run | Arm | Wall time (UTC) | Duration | Turns | Cost | Exit | Timed out | Budget hit |
|---|---|---|---|---|---|---|---|---|
| B1 | B | 09:20:11→09:45:20 | 25 min 09 s | 220 | $21.37 | 0 | no | no |
| A1 | A | 09:45:21→10:07:20 | 21 min 58 s | 194 | $18.34 | 0 | no | no |
| C1 | C | 10:07:21→10:31:05 | 23 min 44 s | 256 | $22.82 | 0 | no | no |
| A2 | A | _PENDING_ | _PENDING_ | _PENDING_ | _PENDING_ | _PENDING_ | | |
| C2 | C | _PENDING_ | _PENDING_ | _PENDING_ | _PENDING_ | _PENDING_ | | |
| B2 | B | _PENDING_ | _PENDING_ | _PENDING_ | _PENDING_ | _PENDING_ | | |

Three runs completed for **$62.53**, mean $20.84. No run has hit the $25 cap, though C1
came within $2.18 of it. On that mean, six runs project to roughly $125.

## What broke

_PENDING_ for anything arising during the runs. Known before the report is written:

- **Three guard/audit defects fixed before this launch** (the 2026-08-28 attempt aborted on
  run B1). The guard split commands on operators inside quoted arguments (`695c51b`); the
  audit shared that blindness in the *measurement* path, so once the fixed guard admitted
  `arfc cluster-get … 2>&1 | head -c 20000` the audit would have classified it `bash:mixed`
  and reported a **false `integrity: false`** (same commit's twin); and no per-run guard
  tamper evidence existed at all (`d36a772`, `fa51cec`).
- **`artifacts()` spec divergence**, found by review on 2026-08-31 and resolved by aligning
  the spec. Latent only — no measured cluster triggers it. See Results.
- **Unfinished runs report $0.00 cost.** `runner.py` writes `result.json` as `null` when no
  terminal result event was captured, and `metrics.py`'s `or 0.0` then reports the cost as
  zero rather than unknown, which would systematically understate `failure_cost_share`.
  Whether this touched any run here: _PENDING_ (it bites only a run with a null
  `result.json`).

## Decisions for the main run

_PENDING_ — k; budget cap; timeout; task window(s); exclusion rule for integrity-violated
runs; any prompt or template change (which forces a re-freeze); whether arm C stays.

## Threats observed

_PENDING_ for run-derived threats (rate limits, API errors, time-of-day effects, workspace
copy time). Known from review before the runs completed:

- **The guard tamper evidence has a hole.** `runner.py:214` digests `guard.json` and
  `audit.py:324` re-hashes it, but `runner.py:35` points the hook at the live `guard.py`,
  which is never digested — the settings *pointer* is instrumented, the enforcement
  *implementation* is not. Since `--allowedTools` does not confine a built-in tool (this
  project's own measured finding, which is why the guard exists), `Edit`/`Write` are not
  path-confined and the file is reachable by absolute path. Closed retroactively for this
  campaign by the hand-recorded digests above; it needs a real fix in the harness before
  the main run.
- **The parity suite asserts far less than it appears to.** Its completeness check is a
  markdown name-grep, and only 5 of the 16 rows in `parity.md` carry a cross-arm
  behavioural assertion. A green post-run parity result is therefore weak evidence for the
  stop-ship gate, not strong evidence.
- **Arms can disagree on a cluster's normative status.** B1 judged `c0004` normative; A1
  judged the same cluster non-normative. Legitimate per-run judgement variance, but it
  bears on cross-arm comparability and should be quantified once all six runs land.
