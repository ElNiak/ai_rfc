# ai_rfc — the model-driven layer over PANTHER's a_rfc substrate

The `a_rfc` package in PANTHER is deterministic: it extracts a repository's
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
| `PANTHER_REPO` | Root of a PANTHER checkout (the `a_rfc` substrate) |
| `ARFC_WORKSPACE` | One reconstruction workspace (corpus, timeline, clusters, checkpoints, manifest, questions, revisions, draft) |

Missing either fails loudly; nothing guesses.

## Layout

- `plugins/ai-rfc/skills/` — `arfc-reconstruction-loop` (the driver),
  `arfc-rfc-style` (I-D prose + claim-citation discipline),
  `arfc-interviewing` (question register round-trip),
  `arfc-evidence-hygiene` (the promotion rule as working intuition).
- `plugins/ai-rfc/commands/` — `/arfc-init`, `/arfc-next-cluster`,
  `/arfc-interview-import`, `/arfc-release-revision`, `/arfc-status`.
- `plugins/ai-rfc/server/` — the `arfc` MCP server and its parity CLI:
  one core, two frontends, so the AI+MCP and AI+CLI experiment arms are
  capability-identical by construction.
- `docs/parity.md` — the tool ↔ CLI parity table (the experiment
  instrument).

## Guardrails the tools enforce

- `arfc_claim_upsert` rejects any `status` input — a claim's standing is
  adjudicated from its evidence, never asserted.
- `arfc_answer_record` grants `signed_off_by` only when the author
  confirmed the exact claim wording; a paraphrase earns an interview
  anchor, not a sign-off.
- Every write is atomic (temp + rename); every gate failure is surfaced
  verbatim, never worked around.
