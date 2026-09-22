# 1. Threat model for the agent under test

Date: 2026-09-15
Status: Accepted 2026-09-15 · landed 2026-09-22
Amended: 2026-09-15 (see *Amendment* below — decision 2 only)

## Context

`ai_rfc` reconstructs an RFC specification from a source repository by running Claude model sessions
against it. Those sessions write files — `revisions.yaml`, cluster ids, checkpoint records, claim
text — which the tooling then reads back, renders into Markdown reports, prints as diagnostics, and
turns into verdicts, exit codes and experiment metrics. **The session is therefore both the subject
of a measurement and an author of the evidence that measurement reads.**

A whole-branch review of the one-door CLI roadmap (2026-09-15) probed three forgeries end to end and
found a fourth class already fixed mid-roadmap:

- A **fabricated checkpoint** is indistinguishable from an earned one. `draft/checkpoint.py:153`
  hashes `manifest.yaml` against a digest stored in the **same directory**, so the check is
  self-certifying; `server/core/revisions.py:66` checks only that the file exists. A cluster counts
  as done, inflating `completed_fraction` and `pass^k`.
- A **forged report section**: an agent-written manifest produces a clean
  `## Promotion violations / _None._` in `report.md`. Claim ids are the one id type with no pattern
  constraint.
- A **forged diagnostic**: a cluster id carrying a line break produced a standalone
  `note: gate clean` while findings were non-empty and the exit code was 3 — an inversion.

All are reachable from **all three experiment arms, arm A included**, which has no Bash at all,
because `READ_TOOLS` grants unscoped `Write`/`Edit` to every profile. Validation on the *instructed
route* does not protect the *file*.

**A threat model already existed, unwritten, in the audit layer.** `metrics.py:41` defines
`integrity_rate` as "runs whose audit found no executed out-of-arm call / runs"; `audit.py:365`
counts `bypass_attempts` including permission denials; and the comment beside `register_edits`
states that "a score that treats hand-writing the register as fatal has to read a number that covers
all of it." The audit layer had already decided the session may bypass and may hand-write its
register. **The artifact layer never shared that stance.** This ADR states the model and makes the
two consistent.

> Every file-and-line citation in this section records the tree as it stood on **2026-09-15**. Later
> work moved some of them; the observations are kept at the state they were made in rather than
> renumbered, and what landed since is named under *Consequences*.

## Decision

**ai_rfc treats a model session as trusted for its work and untrusted for its self-report.**

1. The session's reconstruction output is taken at face value; its claims about what it completed
   are not.
2. The **audit transcript is authoritative** when it and the workspace disagree. It records tool
   calls and sits outside the session's write reach. **(Amended 2026-09-15 — this clause is a claim
   about layout, and it does not hold in production. Do not quote it flatly; read the *Amendment*
   section below first.)**
3. A checkpoint **no transcript covers is *unverified*, not invalid.** It cannot count toward a
   completion metric; it is not presumed forged.
4. The response is **detection, not prevention**.
5. **One model** covers production `ai-rfc run` and the three-arm instrument alike.
6. Scope extends past machine-read state to **every artifact a human reads as a verdict** —
   rendered reports and operator-facing diagnostics included.
7. The model applies **retroactively**: the fourteen archived real-session runs get reconciled.

## Amendment — 2026-09-15: "authoritative" is a claim about layout

*Found while resolving the decision map's ticket "What shape the transcript fallback takes"; recorded
first on that map's ticket 1, which is gitignored at
`.superpowers/sdd/2026-09-15-arfc-post-review-rulings/`, and carried here as the promoted record.*

**One clause of this ruling holds in a campaign and is structurally false in production.** Decision 2
presumes the transcript sits outside what the session can write. **It does in a campaign; it does
not in production.**

- **Campaign layout** — the transcript and `sessions.jsonl` live at `run_dir` while the session works
  in `run_dir/workspace` (`experiment/runner.py:49-51`). Two of the three artifacts the coverage
  predicate reads are **outside** the workspace copy.
- **Production layout** — `driver/record.py:3-9` puts all four run artifacts under
  **`<workspace>/runs/<run id>/`**, and `checkpoints/*/harness.json` is inside the workspace as well.
  With `READ_TOOLS` granting unscoped `Write`/`Edit`, **every artifact the predicate reads sits
  inside the area the session can write**, and a later session in the same workspace can reach an
  earlier run's transcript.

**Campaign coverage is therefore structurally stronger than production coverage** — the inverse of
what the design assumes.

**What stands unchanged**: detection over prevention, untrusted for self-report, retroactivity, and
the ruling that the tool grant is not this effort's lever. **What is amended**: "authoritative" is a
claim about **layout**, not about the artifact, and it is true of the campaign transcript. Whether
production's run directory belongs outside the workspace is a design question this amendment does
not settle — it is named here so the clause is not quoted flatly again.

The severity of the fabricated-checkpoint finding does not move on this: the detector is
absent-provenance detection ("unverified, never forged") rather than tamper-evidence, so what the
amendment bounds is this ADR's authoritative-transcript clause, not the detector's own claim.
Evidence class **READ** — from the layout code and the review's `READ_TOOLS` finding; no write was
probed.

## Consequences

**Detection over prevention** is the load-bearing choice. Prevention cannot be complete while the
unscoped `Write` grant stands, and that grant may not be ai_rfc's to remove. Detection is also the
only response that reaches work already done, which (7) requires. The cost is that a determined
session can still forge; the system's claim is that it will be *visible*, not that it is impossible.

**"Unverified, not invalid" (3) is deliberately weak**, and the reason is empirical: a never-run
pristine fixture, `interview-fixture/checkpoints/c0002-pr-e7b7c5e309bd`, already ships a checkpoint
with no `harness.json` marker. **Absence of a marker does not imply agent authorship.** A stricter
reading would misclassify honest fixtures, and would also invalidate real work for a bookkeeping
reason — transcript attribution for runs A7–A14 was lost to a directory rename.

**Scope (6) covers the gap (2) leaves.** A forged Markdown section is not a tool call, so no audit
will ever see it. Without (6), the authority ruling would silently exempt the entire rendered-report
class.

**MARK A1's headline figure may change.** It is the only real run with zero `harness.json` markers —
37 checkpoints, all unmarked, because its window covered all 69 ordinals. Under (3) and (7) its
"37 done" may restate as "37 recorded, N verified".

**The detection this model asked for has landed.** *Added 2026-09-22.* `ai_rfc/driver/coverage.py`
decides whether a checkpoint is covered by a record the session cannot have forged — a
`sessions.jsonl` row carrying the cluster id, the harness's own pre-seed marker, or a `tool_result`
receipt whose invoking `tool_use` is itself a checkpoint-write call — and reports **unverified**
otherwise, exactly the weak reading (3) requires. Its operator door is the `coverage` check of
`ai-rfc verify` (`ai_rfc/lifecycle/verify/cli.py:181-192`), which enumerates the candidates for the
production layout while the campaign audit supplies the campaign's siblings. Landed at `439af29`
(the predicate) and `49666a8` (the verify-side finding and the frozen route record). The amendment
above is the reason the detector is framed as absent-provenance detection rather than as
tamper-evidence: in production its own inputs are inside the session's write reach.

## Alternatives considered

- **Trusted but fallible** — forgeries as robustness nits. Rejected: it contradicts the audit layer,
  which already scores bypass attempts and hand edits. Measuring a behaviour only makes sense if it
  is expected.
- **Fully untrusted**, including the work product itself. Rejected as a much larger programme than
  the evidence requires; it would put the reconstruction output in question, not only the completion
  claims.
- **The workspace as authority.** Rejected: that is precisely the surface shown to be forgeable.
- **Two models**, stricter for the instrument than for production. Rejected: production drives
  unattended sessions with the same grant, so the exposure is the same, and two stances is one more
  than anyone will remember.
- **Prevention at the write path.** Rejected as incomplete while the grant stands, and because it
  does nothing for the fourteen runs already archived.
