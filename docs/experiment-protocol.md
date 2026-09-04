# Experiment Protocol: AI+MCP versus AI+CLI on the ai_rfc Substrate

**Purpose**: the operational protocol for the Phase-C comparison of `ai_rfc`'s two frontends, distilled from Section 6 of the related-work report (`12-report.md`, Revision 2, 2026-08-25). Intended location: `docs/experiment-protocol.md`. Every design rule below traces to the report's coded evidence base; citations are author-year into the report's reference list. No design element rests on Tier-C evidence alone.

---

## 1. Arms

The comparison uses the binding three-class interface taxonomy: class 1, structured-typed tools (schema-declared operations with typed, validated, per-operation arguments); class 2, hybrid shell-via-tool (a structured wrapper whose payload is a free-form command string); class 3, raw shell (agent text piped to a terminal with no function-calling layer). Bash executed through function calling is class 2 by definition, never CLI.

| Arm | Surface | Class |
|---|---|---|
| A | `ai_rfc_*` MCP tools | 1: structured-typed |
| B | `ai_rfc` CLI invoked through the Bash tool | 2: hybrid shell-via-tool |
| C | Raw substrate `python -m` commands through the Bash tool | 2: hybrid, different command family |

**The class-3 caveat, stated up front.** A true class-3 arm may not exist inside a Claude-Code harness, because every shell interaction there transits the Bash function-calling tool. The cleanest deployed class-3 exemplar, mini-swe-agent, parses actions from model text and runs them via subprocess with no tool-calling layer at all (mini-swe-agent, 2025-2026). Two consequences are binding:

1. Headline claims say "structured-typed versus hybrid shell-via-tool," never "MCP versus raw shell" or "MCP versus CLI" unqualified.
2. A genuine class-3 arm requires a different harness, and harness effects dominate interface effects (5-28x on cost; Scaffolding Matters, 2026), so a class-3 datapoint, if wanted, runs as a separate, explicitly harness-confounded companion measurement and is never pooled with the main design.

The B-versus-C contrast is deliberately kept: it isolates within-class affordance (a curated command vocabulary versus generic module invocation) with the wrapper held constant, making the affordance/capability boundary measurable.

**Parity is the arm-design backbone.** Both frontends drive one shared core. The `ai_rfc` parity test suite is a standing construct check, run before and after the experiment; any capability delta discovered is a protocol stop-ship, and the parity-test evidence ships with the paper. This is the design element every published near-miss lacked: capabilities held "similar" without tests (Xu et al., 2026), arms that were different systems entirely (Terminal Agents Suffice, 2026), or assignment that leaked outright (Scaffolding Matters, 2026).

## 2. Arm-Assignment Enforcement

The controlling finding: "agents frequently ignored the interface they were assigned," so unverified comparisons measure an unknown mixture (Scaffolding Matters, 2026). Three mandatory elements follow.

**Enforce by removal or allowlist, never by denylist or prompt instruction.** String-level denylists are 69.0-98.6% bypassable across 1,709 real-world configurations, including Claude Code's built-in denylist (One goal, many commands [ShellSieve], 2026). Concretely: arm A runs with the Bash tool absent, or with `ai_rfc` and `python -m` invocations denied by allowlist; arms B and C run with the `ai_rfc` MCP server unmounted. The enforcement mechanism itself is disclosed in the paper.

**Audit every transcript.** Tool-call records are structured, so the audit is mechanical: scan each run for out-of-arm invocations. The **assignment-integrity rate is a reported metric per arm-target cell**, and integrity-violated runs are excluded by a pre-registered rule, never silently.

**Count bypass attempts as data.** Denied Bash calls in arm A, and attempts to address absent MCP tools in arms B/C, are counted and taxonomized. This turns enforcement into a measured outcome connected to the two-sided guardrail literature: schema-level enforcement imports an injection attack surface where stronger tool-calling models are more vulnerable (Zhang et al., 2025; MCPSecBench, 2025), while string-level gating is demonstrably fragile (ShellSieve, 2026). Enforcement is measured under identical policies on both sides rather than assumed for either.

## 3. Outcomes and Metrics

One primary metric per hypothesis, designated in the preregistration; everything else is descriptive or multiplicity-corrected.

**Primary outcome: state-verified task success.** Success is recomputed from the substrate's own gates and checkpoints: per-cluster completion, where a cluster counts as completed when its checkpoint exists, its revision entry and tag exist, and both strict gates exit 0 when the harness re-runs them on the final workspace; the primary metric is completed clusters over the window size, with pass^k over repeats, and gate-exit events are reported as raw counts with uncertainty (they are discrete and possibly rare). The final `checked_fraction` stays reported as the substrate's honesty metric; it moves only through sign-offs and runtime anchors and is therefore identically 0.0 in model-only runs, which is why it cannot serve as the primary outcome (decision D23, 2026-08-26). Agent self-report is never a primary input; the field's convergent practice is execution- or state-verified scoring (Li et al., 2025; Merrill et al., 2026), and self-report unreliability is documented (Scaffolding Matters, 2026). Substrate-state verification also inoculates against reward hacking, found in more than 15% of popular terminal-benchmark tasks (What makes a good terminal-agent benchmark task, 2026).

**Primary cost metric: cache-adjusted billed cost**, per run and per completed unit of work. Raw token counts are reported as secondary only: token reduction is not cost reduction, because cache traffic dominates billed cost (Token reduction is not cost reduction, 2026), and the cost signal in the closest prior ablation lived in cache-adjusted cost while pass rates were interface-invariant (Yang et al., 2026). Report the **failure-cost share** per arm (spend on runs producing no completed work), following the 12.9% versus 2.2% asymmetry observed between MCP and CLI arms (Scaffolding Matters, 2026).

**Reliability: pass^k and consistency.** At least k repeats per arm-target cell; report pass^k and variance across repeats. Consistency is where interface effects concentrate (a 4.7x consistency difference across architectures; Xu et al., 2026). Single-run comparisons are rejected.

**Trajectory metrics, pre-registered only**: final checked_fraction, tokens-to-threshold, and trajectory AUC, all recomputable from checkpoints so reviewers can re-derive every aggregation.

**Error taxonomy, two-sided by construction.** Class-1 channel: invalid or hallucinated tool calls, argument-validation failures, format violations (the BFCL metric vocabulary; Patil et al., 2025). Class-2 channel: command syntax, wrong flags, composition failures. Both feed a process-level attribution of each failure to interface, environment, or reasoning, with failure timing tracked, since failures start early and hide (Beyond final code, 2026). The format-restriction dispute (Tam et al., 2024, versus dottxt, 2024) is resolved locally, not imported: measure format-violation and schema-error rates directly in arm A.

**Guardrail metrics.** Bypass-attempt counts and outcomes per arm under identical policies (Section 2); if any hardening is applied, report its measured price in success and cost.

**Latency and wall time: secondary only**, with API conditions logged; API latency and rate limits confound them.

## 4. Procedure

**Targets.** MARK (GitLab, merge-commit history, 69 clusters) and aioquic (GitHub, squash-heavy, 238 of 342 clusters recoverable only through forge data). The two targets span the forge axis and the merge-strategy axis; they are n=2 and claims stay scoped to these regimes.

**Task sampling.** Tasks are drawn from the substrate's cluster inventory per target. Sample clusters to cover the provenance classes the linkage literature shows are recovery-sensitive: API-visible merges, forge-rescued squash clusters, and orphan-adjacent history (squash dominated the one industrial distribution measured, at 41.5%; Kononenko et al., 2018; forge records are the only deterministic squash linkage; GitLab, 2026). Fix the sampled task list before any run and reuse it identically across arms.

**Run matrix.** Arms {A, B, C} x targets {MARK, aioquic} x k repeats, k >= the preregistered minimum per cell. Run order is randomized and interleaved across arms to neutralize time-varying API conditions.

**Constants.** One harness for all arms (the single strongest lesson of the harness-confound literature; Scaffolding Matters, 2026). Model version and sampling parameters pinned and named. System prompts and skills identical modulo minimal invocation syntax; the pair is diffed and the diff published. Versioned prompt freeze: no mid-experiment edits. Full harness disclosure in the paper.

**Per-run pipeline.** (1) Reset substrate state; (2) launch the arm's surface with enforcement per Section 2; (3) capture the full transcript and all checkpoints; (4) recompute outcomes from substrate state; (5) run the assignment-integrity audit; (6) log cache accounting and API conditions.

## 5. Threats to Validity

| Class | Threat | Mitigation |
|---|---|---|
| Construct | Arm difference is capability, not affordance | Parity by construction; parity suite before/after; capability delta is a stop-ship |
| Construct | "CLI arm" misread as raw shell | Three-class taxonomy disclosed; claims scoped to structured versus hybrid; class-3 caveat stated (Section 1) |
| Construct | Success gameable by self-report or reward hacking | State-verified outcomes recomputed from checkpoints (Li et al., 2025; What makes a good terminal-agent benchmark task, 2026) |
| Internal | Arm-assignment leakage | Removal/allowlist enforcement; transcript audits; integrity rate reported (Scaffolding Matters, 2026; ShellSieve, 2026) |
| Internal | Prompt/skill asymmetry | Identical-modulo-syntax prompts; published diff; versioned freeze |
| Internal | Time-varying API conditions; cache asymmetry | Interleaved randomized order; cache-adjusted cost primary (Yang et al., 2026; Token reduction is not cost reduction, 2026) |
| External | n = 2 targets | Characterize both on forge richness, linkage recoverability, history shape; scope claims; substrate determinism makes third-party replication cheap; never generalize to all repositories |
| External | Model churn | Interface-level framing; model pinned and named; snapshot-dated grounding |
| Conclusion | Run-to-run variance | >= k repeats; non-parametric tests plus effect sizes; pass^k |
| Conclusion | Metric multiplicity | One primary metric per hypothesis; rest descriptive or corrected |
| Conclusion | Rare, discrete gate exits | Raw counts with uncertainty, never rates alone |
| Conclusion | Post-hoc trajectory aggregation | Aggregations preregistered; recomputable from checkpoints by reviewers |

Expectation-setting, so the headline is honest before the first run: the coded evidence predicts interface effects on success near zero at current model strength, with the live effects in cost, consistency, and error taxonomy (Xu et al., 2026; Yang et al., 2026). If the document-domain mechanisms do not materialize, the contribution is instrument quality and domain transfer, not effect discovery.

## 6. Preregistration Checklist (OSF, before any run)

- [ ] Hypotheses, each with exactly one designated primary metric.
- [ ] Trajectory aggregations fixed: final checked_fraction, tokens-to-threshold, trajectory AUC.
- [ ] k (repeats per cell) and the run matrix.
- [ ] Task list per target, with provenance-class coverage stated.
- [ ] Exclusion rule for integrity-violated runs.
- [ ] Enforcement mechanism per arm (allowlist/removal configuration).
- [ ] Model id, version, sampling parameters; harness identity and version.
- [ ] Prompt/skill pair and its published diff; freeze declaration.
- [ ] Statistical plan: non-parametric tests, effect sizes, multiplicity handling.
- [ ] Cache-accounting method and cost-computation formula.
- [ ] Parity-suite results attached (pre-run); commitment to re-run post-experiment.

### Pilot-derived defaults (2026-08-31)

From the aioquic pilot, `pilot-aioquic-w02-11-20260831`; full report at
`docs/experiments/2026-08-31-pilot-aioquic.md`. Each entry is marked **from the pilot** or
**unchanged from the protocol**.

- **k (repeats per cell)**: 3 — *from the pilot*, provisionally. At k = 2 the primary
  outcome had zero variance (see the task window entry), so k buys precision only on the
  cost metrics until the primary metric is reconsidered.
- **Per-run budget cap**: raise above $25 — *from the pilot*. Mean spend was $21.80 and two
  of six runs landed within $1 of the cap without hitting it.
- **Timeout**: 7200 s — *unchanged from the protocol*. The slowest run used 22% of it.
- **Task window(s)**: cluster ordinals 2–11 on aioquic was **too easy to discriminate arms**
  — *from the pilot*. All three arms completed 10/10 in both repeats, so
  `completed_fraction` was 1.000 with zero variance. The main run must widen the window,
  pick a denser target, or promote a metric that did vary (`cost_per_completed_cluster`,
  `tokens_to_first_completion`, AUC, hand-edit count) to primary.
- **Exclusion rule for integrity-violated runs**: *unchanged from the protocol*, with one
  addition *from the pilot* — a run is excluded only after the guard's verdict and the
  audit's verdict are confirmed to agree on the offending call. The pilot's one apparent
  violation was an instrument defect, not a run defect.
- **Enforcement configuration per arm**: *unchanged from the protocol*. A: read tools plus
  16 `mcp__ai_rfc__*`, no Bash. B: read tools plus `Bash(ai_rfc *)`. C: read tools plus
  `Bash(python -m ai_rfc*)`, `Bash(git *)`, `Bash(sqlite3 *)`. Enforced by a
  `PreToolUse` hook, because `--allowedTools` does not confine a built-in tool.
- **Model and harness**: `claude-opus-5`, effort `high`, `claude --version`
  **2.1.251 (Claude Code)** — *from the pilot*. Re-run the S0 spike whenever the CLI moves;
  the enforcement mechanism is a measured property of the CLI, not a contract.
- **Prompt digests** (sha256): the pilot froze `arm-A.md` `15dd983b…2552ba`, `arm-B.md`
  `a3128ee4…9d3aa`, `arm-C.md` `967a434b…8ead33`, `task.md` `05e731d3…f84539` — *from the
  pilot*, **superseded**. Those files are campaign artefacts that `campaign init` renders
  from `ai_rfc/experiment/prompts/loop.tmpl.md` and the two arm-neutral skill texts; the
  MCP tools were renamed `arfc_*` → `ai_rfc_*` after the pilot (visible in the pilot's
  `diff-A-C.patch`, kept unedited as the record), so today's rendering cannot reproduce
  the pilot digests. Re-freeze before the main run from a fresh `campaign init`, whose
  `campaign.json` records the digest of every rendered prompt file.
- **Parity pre/post**: passed both — `7 passed in 3.56s` before, `7 passed in 3.77s` after —
  *from the pilot*. Treat as weak evidence pending better coverage: the suite's completeness
  check is a name-grep and only 5 of 16 documented rows carry a cross-arm behavioural
  assertion.
- **Cache accounting**: cache reads and creation were **99.68%** of 198.8 M tokens — *from
  the pilot*. Confirms cache-adjusted billed cost as the primary cost metric and raw token
  counts as secondary.
- [ ] Reporting commitments: assignment-integrity rate per cell, failure-cost share per arm, bypass-attempt taxonomy, raw gate-exit counts with uncertainty, API-condition log.

---

*Distilled from `12-report.md` Section 6 (Revision 2). The report carries the full evidence base, tier gradings, and the coverage limits under which every cited finding holds.*

### 2026-09-03 — draft quality v2, SP7a

- **Tool surface**: 18 tools (`ai_rfc_draft_build` and `ai_rfc_draft_lint`
  added to the 16 above); `docs/parity.md` is the table.
- **Arm C stays frozen** at the pre-v2, 16-tool surface (spec D42); the
  parity table's third column reads "not available in arm C" for the two
  new rows, so a v2 campaign compares arms A and B only.
- **Every revision tag compiles**: the server's `revision_tag` (the MCP tool
  and its `ai_rfc revision-tag` CLI form share one core) runs `draft build`
  before creating the tag whenever `AI_RFC_TOOLCHAIN` is set, refusing the
  tag on any build finding, and `campaign init` refuses to start without a
  verified toolchain — so in a v2 campaign (arms A and B only, per above)
  every tag that exists compiles.
- **The campaign record freezes `task.tmpl.md`**, and per-cluster sessions
  render their task prompt from it. The `task.md` digest frozen above
  (2026-08-31) described a prompt no per-cluster session actually ran.
- **`pristine.json` seals `references.yaml` and `refcache/`**, alongside the
  clone, corpus and timeline it already sealed.
- **`draft build` runs idnits in `submission` mode**, not the template's own
  `normal` default (deviation D32): `normal` flags `INVALID_REFERENCES_NAME`
  on the combined Normative/Informative references wrapper that kramdown-rfc
  and xml2rfc auto-generate whenever a draft has both kinds — which every
  draft citing the BCP 14 boilerplate plus any informative reference has;
  the datatracker's own `submission` mode does not.
- **The lint's narration and stub counts are line-based, not match-based**:
  the `introduction: narrates …` finding counts distinct Introduction lines
  carrying at least one narration tell, not the number of pattern matches
  (a line naming both an ordinal cluster and an added/withdrawn count is one
  line, not two); the abstract stub-marker check ignores `{::comment}`
  blocks, so a comment that quotes the marker to explain it does not itself
  trip the finding.
