# aioquic pilot — 2026-08-31

Six runs, three arms, two repeats, one target. All six completed the full task window and
all six passed the arm-integrity audit. The comparison the pilot exists to enable is
therefore **not** about whether the task gets done — every arm did all of it, twice — but
about what each interface costs to get there, and how far each one drifts from the
substrate it was given.

Artifacts backing every number in this document are in
`2026-08-31-pilot-aioquic/`: `aggregate.json`, `report.md`, `campaign.json` and the three
pairwise prompt diffs.

## Setup

| | |
|---|---|
| Campaign id | `pilot-aioquic-w02-11-20260831` |
| Created | 2026-08-31T09:19:11Z |
| Model / effort | `claude-opus-5` / `high` |
| `claude --version` | **2.1.251 (Claude Code)** — from each run's `status.json`, not from the CLI at reading time |
| Spike gate | `go: true`, 9/9 checks, re-armed 2026-08-31T09:11:01Z on that same 2.1.251 |
| git — `ai_rfc` | `fa51cec` |
| git — PANTHER | `v1.1.3-839-g226608938` |
| Target / window | aioquic, cluster ordinals **2–11** (10 clusters) |
| Run order | `B1 A1 C1 A2 C2 B2` (seed `20260826`, preregistered) |
| Repeats | 2 per arm |
| Budget cap / timeout | $25.00 and 7200 s per run |
| Parity — pre-run | **passed**, `7 passed in 3.56s` |
| Parity — post-run | **passed**, `7 passed in 3.77s` — the stop-ship gate holds (but see Threats) |

**Pristine record.** Clone HEAD `6d36838d008c2202c337142fa07e8bf80e96bac8`; draft template
HEAD `d2f1ee9bf9b867a3d04249d26b786a28468cd430`; forge snapshot
`github.com__aiortc__aioquic/snapshot-2026-08-25T15-16-59Z`; `git version 2.55.0`. The
corpus holds **342 clusters**, of which **332 are pre-seeded** with a `harness.json`,
leaving exactly the 10 in-window clusters for the agent.

**Prompt digests** (sha256, frozen at `campaign init`):

| File | Digest |
|---|---|
| `arm-A.md` | `15dd983b7f4bb1cc8df9093e8dbbdcace6c15ff5d7ebf81bd13716606f2552ba` |
| `arm-B.md` | `a3128ee40560543f856f1f6a0e133824be13db0aaa551ecb613db7f65c39d3aa` |
| `arm-C.md` | `967a434bbc02e101343fcbeb9a13368b27c296da17f585217a34e560848ead33` |
| `task.md`  | `05e731d3cb93fd7ffc3ee07c29eceb829ae2a006e18064155a35885592f84539` |

## Results (instrument numbers, not effect claims)

**Per arm.** A = MCP tools, B = `arfc` CLI, C = raw substrate commands.

| arm | completed (mean/min) | artifacts | pass^k | integrity | bypass | errors c1/c2 | hand edits | cost total / mean | cost per completed | tokens→first | AUC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 1.000 / 1.000 | 1.000 | 1.000 | 1.000 | 0 | 8/0 | **0** | $38.08 / $19.04 | $1.90 | 589,373 | 0.595 |
| B | 1.000 / 1.000 | 1.000 | 1.000 | 1.000 | 6 | 0/2 | **1** | $45.47 / $22.73 | $2.27 | 1,345,138 | 0.523 |
| C | 1.000 / 1.000 | 1.000 | 1.000 | 1.000 | 8 | 0/5 | **87** | $47.23 / $23.61 | $2.36 | 3,958,540 | 0.476 |

**Per run.**

| run | arm | exit | completed | artifacts | gates m/c | cost | turns | tokens | duration ms | integrity | bypass | errors c1/c2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | B | 0 | 10/10 | 10 | 0/0 | $21.37 | 220 | 31,488,629 | 1,508,656 | yes | 3 | 0/0 |
| A1 | A | 0 | 10/10 | 10 | 0/0 | $18.34 | 194 | 26,588,444 | 1,318,071 | yes | 0 | 2/0 |
| C1 | C | 0 | 10/10 | 10 | 0/0 | $22.82 | 256 | 36,590,805 | 1,424,243 | yes | 5 | 0/2 |
| A2 | A | 0 | 10/10 | 10 | 0/0 | $19.74 | 199 | 27,642,021 | 1,596,595 | yes | 0 | 6/0 |
| C2 | C | 0 | 10/10 | 10 | 0/0 | $24.41 | 250 | 39,054,027 | 1,510,537 | yes | 3 | 0/3 |
| B2 | B | 0 | 10/10 | 10 | 0/0 | $24.10 | 210 | 37,447,365 | 1,577,752 | yes | 3 | 0/2 |

**Per cluster `pass^k`.** All ten in-window clusters are ✓ in all three arms. The matrix is
uniform, so it is stated rather than tabulated.

**The primary outcome is saturated.** `completed_fraction` is 1.000 everywhere, with zero
variance across arms and repeats. This is the single most consequential result for the main
run: at this window and this model, per-cluster completion cannot discriminate between
interfaces, because none of them fail. Every difference the pilot did find lives in the cost
and process metrics.

**Metric definition in force.** A cluster counts as completed when its checkpoint exists,
its revision entry and tag exist, and both strict gates exit 0 when the harness re-runs them
on the final workspace. `checked_fraction` is the substrate's honesty metric only; it moves
solely through sign-offs and runtime anchors and is identically 0.0 in model-only runs,
which is why it cannot be the primary outcome (D23). The design spec previously exempted
`normative_change: false` entries from needing a tag; it was the only one of four sources to
do so, and was aligned on 2026-08-31. Every cluster in this pilot carries a tag, so both
readings return identical values and the alignment shifts no number here.

## Integrity and enforcement

**Integrity rate is 1.000 in all three arms — after a corrected audit defect.** This must be
read with the disclosure below, not without it.

| | before fix | after fix |
|---|---|---|
| A1, A2, B1, B2, C2 | integrity `true` | integrity `true` (unchanged) |
| **C1** | integrity **`false`**, 1 executed out-of-arm call | integrity **`true`**, 0 |

The flagged call was, verbatim from `audit/C1.json`:

```
sqlite3 -version; git -C <run>/workspace/clone …
```

Arm C's entitlements are `python -m panther…a_rfc*`, `git *` and `sqlite3 *`
(`experiment/arms.py:55`), so **both halves of that line are surfaces arm C holds**, and the
live `PreToolUse` guard admitted it. The audit disagreed because it judged arm membership
from a collapsed surface label: `bash_family` returns a single family, or `bash:mixed` when
a line reaches more than one, and `bash:mixed` is not in the arm's allowed set. The guard
checks each command group independently; the audit checked one summarising label. Two
readers of the same string, disagreeing.

The fix makes the audit's Bash decision call `enforcement.is_allowed` — the same function
the live guard runs — so the two cannot drift again, and keeps the label purely as reporting
detail. Re-running the audit changed **exactly two fields on exactly one run**: C1's
`integrity` and its `executed_out_of_arm`. Every other value in all six audits is
byte-identical, verified by diff. The superseded audit is preserved at
`campaigns/…/audit.pre-mixed-family-fix/`.

Why this is a correction and not a result-improving edit: the guard — the thing that
actually enforced arm separation during the run — never permitted a breach; entitlement is
settled by a literal list in `arms.py` rather than by judgement; and `bash_family`'s own
docstring already warned that classifying an entitled call as `bash:mixed` "would report an
integrity violation for a call the arm was entitled to make". The code documented the bug
and then committed it. Both values are reported above so a reader can weigh this without
taking it on trust.

**Guard evidence.** All six runs: `digest_recorded: true`, `unmodified: true`, and
`fired_for_every_bash_call: true` (C1: 120 `PreToolUse` hook starts against 120 Bash calls).
Arm A mounts no Bash surface at all, as `expected_no_bash` records. Independently, the live
guard *implementation* was hand-digested at 2026-08-31 11:45:59 CEST, because the harness
instruments the settings pointer but not the code it points at:

```
guard.py        fd0323846f157bec9210c911e6a36b0e0f6cdb624a9685211a2b047cbcb1cfc1
enforcement.py  e65bca91816c423e6af86522964b7059c4e65990d9739c2cd763bda955ad851e
```

Re-digested unchanged after the campaign.

**Bypass attempts: 0 (A), 6 (B), 8 (C)** — all denied, all `bash:other` or `bash:mixed`,
and all of one kind: environment orientation at session start. Verbatim:

```
echo "PANTHER_REPO=$PANTHER_REPO"; echo "ARFC_WORKSPACE=$ARFC_WORKSPACE"; which arfc; ls …
ls /Users/elniak/arfc-experiments/profile/projects/…
cat /Users/elniak/arfc-experiments/profile/projects/…
```

No agent attempted to reach the substrate through a forbidden route; they tried to find out
where they were. Arm A recorded zero because it has no Bash surface to try from.

**Denial mechanism observed.** The hook exits 2, which surfaces to the agent as an errored
`tool_result` and lands in the result event's `permission_denials` — the same two-sided
shape the S0 spike measured.

**Hand-edit asymmetry — the sharpest signal in the pilot.** Direct edits to the registers,
bypassing the substrate that is supposed to maintain them:

| arm | manifest.yaml | questions.yaml | revisions.yaml | total |
|---|---|---|---|---|
| A (A1+A2) | 0 | 0 | 0 | **0** |
| B (B1+B2) | 0 | 0 | 1 | **1** |
| C (C1+C2) | 63 | 4 | 20 | **87** |

Arms A and B touched a register by hand once between them, across four runs. Arm C did it 87
times. The raw-substrate arm reaches the same completion score while routing most of its
register writes around the tools whose validation produces that score's meaning.

## Errors

Counts are `class1 / class2` — typed tool errors and shell errors, the protocol's two-sided
taxonomy.

| run | class1 | class2 | first failure index | tool calls |
|---|---|---|---|---|
| B1 | 0 | 0 | — | 219 |
| A1 | 2 | 0 | 59 | 193 |
| C1 | 0 | 2 | 174 | 255 |
| A2 | 6 | 0 | 232 | 198 |
| C2 | 0 | 3 | 132 | 249 |
| B2 | 0 | 2 | 108 | 209 |

The split is total: arm A produces only class-1 errors, arms B and C only class-2. That is
the taxonomy behaving as designed rather than a finding in itself.

**The finding is that the same mistake appears in both channels.** Arm A, class 1:

```
Error executing tool arfc_claim_upsert: aioquic:pkg.1: intent is 'deliberate';
  permitted values are intended, accidental, unknown
Error executing tool arfc_claim_upsert: aioquic:pkg.1: req_class is 'packaging';
  permitted values are protocol-behavioral, data-model, algorithmic
```

Arm B, class 2, same claim id and same invented value, one channel down:

```
Exit code 1 error: SchemaError: aioquic:pkg.1: intent is 'deliberate';
  permitted values are intended, accidental, unknown
```

Most errors in every arm are the substrate's closed vocabularies refusing invented
`req_class` and `intent` values (`packaging`, `loss-recovery`, `transport-parameters`,
`deliberate`). The remaining class-2 errors are arm-C specific and are about operating the
raw surface rather than about the task: `No module named …`, `fatal: not a git repository`,
and `Read` refusing files of 340,212 and 205,681 tokens.

Also seen once: `error: revision draft-elniak-aioquic-reconstructed-11 is already recorded`
(B2) — the substrate refusing a duplicate revision, correctly.

## Cost and time

| run | arm | wall time (UTC) | duration | turns | cost | tokens | cache share |
|---|---|---|---|---|---|---|---|
| B1 | B | 09:20:11→09:45:20 | 25 min 09 s | 220 | $21.37 | 31,488,629 | 0.9967 |
| A1 | A | 09:45:21→10:07:20 | 21 min 59 s | 194 | $18.34 | 26,588,444 | 0.9965 |
| C1 | C | 10:07:21→10:31:05 | 23 min 44 s | 256 | $22.82 | 36,590,805 | 0.9975 |
| A2 | A | 10:31:06→10:57:44 | 26 min 38 s | 199 | $19.74 | 27,642,021 | 0.9956 |
| C2 | C | 10:57:44→11:22:55 | 25 min 11 s | 250 | $24.41 | 39,054,027 | 0.9975 |
| B2 | B | 11:22:56→11:49:14 | 26 min 18 s | 210 | $24.10 | 37,447,365 | 0.9972 |

Token counts are the `claude-opus-5` totals, matching `report.md`'s column: input + output +
cache creation + cache read. A negligible `claude-haiku-4-5` contribution (1,108–1,540 tokens
per run, under $0.002) is excluded there; including it moves no cache share by more than
0.0001. `trajectory.total_tokens` in `aggregate.json` is a third, smaller aggregation summed
from per-event usage, and is not the basis used here.

**Total $130.78** over 2 h 29 m of wall time, mean **$21.80** per run. No run hit the $25
cap, though C2 ($24.41) and B2 ($24.10) came close, and no run timed out against the 7200 s
limit — the slowest used 22% of it. `failure_cost_share` is 0.000 in every arm because no
run produced zero completed clusters.

**Cache dominates: 99.68%** of 198.8 M tokens across the campaign are cache reads and cache
creation. Raw token counts are therefore near-useless as a cost proxy here, which is exactly
why the protocol designates cache-adjusted billed cost as primary.

**Projected main-run cost** at $21.80 mean, 3 arms × 1 target:

| k | runs | projected |
|---|---|---|
| 3 | 9 | ~$196 |
| 5 | 15 | ~$327 |

Multiply by the number of targets. The budget cap needs raising above $25 if any target is
harder than aioquic — two runs here landed within $1 of it.

## What broke

- **The audit reported a false integrity violation** (C1). Fixed, disclosed above, with a
  regression test pinning the real command and the guard/audit agreement invariant now
  asserted across all three arms rather than arm B alone. The old test could not have caught
  this: arm B has one family, so no arm-B command can span two.
- **Three guard/audit defects were fixed before this launch**, after the 2026-08-28 attempt
  aborted on its first run. The guard split commands on operators inside quoted arguments
  (`695c51b`); the audit shared that blindness in the measurement path, so a paged,
  redirected in-family command would have produced a false `integrity: false`; and no
  per-run guard tamper evidence existed (`d36a772`, `fa51cec`).
- **A spec/code divergence in the completion formula**, found by review during the run and
  resolved by aligning the spec. Latent — no measured cluster triggers it.
- **`checked_fraction` is 0.0 throughout**, as designed for model-only runs. Not a break;
  recorded so nobody reads it as one.
- Nothing else. No timeouts, no budget hits, no nonzero exits, no compaction events in any
  run, and no interrupted runs.

## Decisions for the main run

- **The task window must get harder, or the primary outcome must change.** `completed_fraction`
  is 1.000 with zero variance; a ceiling that flat cannot discriminate interfaces. Options:
  widen the window well beyond 10 clusters, choose a target with a denser normative surface
  than aioquic, or promote a metric that did vary — `cost_per_completed_cluster`,
  `tokens_to_first_completion`, AUC, or the hand-edit count — to primary. This decision
  should be made before k is chosen, because it changes what k buys.
- **k**: with zero variance in the primary outcome, k = 3 adds nothing on that metric; it
  still buys variance estimates on the cost metrics, which is where the signal is. Recommend
  k = 3 pending the metric decision.
- **Budget cap**: raise from $25. Two of six runs came within $1.
- **Timeout**: 7200 s is generous; the slowest run used 22%. Leave it.
- **Exclusion rule for integrity-violated runs**: none was needed. Keep the rule as written,
  and note that the one apparent violation was an instrument defect, not a run defect —
  which argues for requiring a guard-vs-audit agreement check before any run is excluded.
- **Arm C stays.** It is the arm that produced the pilot's clearest differential signal (87
  hand edits, the widest token spread, the most class-2 noise). Dropping it would remove the
  contrast the experiment is built to measure.
- **No prompt or template change** is indicated, so no re-freeze is required.

## Threats observed

- **The guard tamper evidence has a hole.** `runner.py:214` digests `guard.json` and
  `audit.py:324` re-hashes it, but `runner.py:35` points the hook at the live `guard.py`,
  which is never digested — the settings pointer is instrumented, the enforcement
  implementation is not. Since `--allowedTools` does not confine a built-in tool (this
  project's own measured finding, and the reason the guard exists), `Edit`/`Write` are not
  path-confined and the file is reachable by absolute path. Closed retroactively here by the
  hand-recorded digests; needs a real fix before the main run.
- **The parity suite asserts much less than it appears to.** Its completeness check is a
  markdown name-grep, and only 5 of the 16 rows in `parity.md` carry a cross-arm behavioural
  assertion. Both parity results above are therefore weak evidence for a stop-ship gate, not
  strong evidence. This should be closed before the main run, since a genuine arm-capability
  divergence is the one thing that would void the comparison outright.
- **Arms disagree on a cluster's normative status.** Non-normative counts ran 0, 1, 2, 2, 3,
  1 across B1, A1, C1, A2, C2, B2 — including B1 and A1 disagreeing about `c0004`. This is
  legitimate judgement variance, but it means "normative change" is not a stable property of
  a cluster, and any metric that leans on it inherits that variance.
- **Single target, single model, single day.** No claim here generalises past aioquic on
  `claude-opus-5` at effort `high`, run sequentially over one 2.5-hour window. Time-of-day
  and rate-limit effects cannot be separated from arm effects in a sequential design with
  n = 2.
- **The run order is confounded with time.** `B1 A1 C1 A2 C2 B2` was preregistered from a
  seed, but each arm's two repeats sit at different points in the sequence, so any drift over
  the session is not balanced out at n = 2.
