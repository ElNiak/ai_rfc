# Spike S0 — isolated-profile hermeticity

**Run:** 2026-08-27 · `claude --version` = **2.1.247 (Claude Code)** · model `claude-opus-5`
**Verdict recorded in `~/arfc-experiments/spike-report.json`:** `go: false`
**Verdict as interpreted here:** D20 (isolated OAuth profile) is **supported**; the
`go: false` comes from two broken checks and one real capability finding, none of
which is a hermeticity failure. The `--bare` + `ANTHROPIC_API_KEY` fallback is **not**
indicated.

Every plan and the spec were written against CLI **2.1.246**; this ran on **2.1.247**.
The flag surface below is therefore re-validated on 247, not inherited.

## Results

| Check | Required | Result | Evidence |
|---|---|---|---|
| `auth` | yes | **PASS** | Isolated profile authenticates; `apiKeySource: none`, exit 0. |
| `hooks` | yes | FAIL (instrument) | Control fired 20 hook events, isolated fired 0 — the desired outcome. The detector cannot see them; see defect 2. |
| `claude_md` | yes | **PASS** | Canary `CLAUDE.md` one directory above the cwd did not reach the isolated run. |
| `arm_surface` | yes | **PASS** | Arm A: exactly `Edit, Glob, Grep, Read, Write` + 15 `mcp__arfc__*`, `arfc: connected`, **no Bash**. Slash commands empty in all three arms. |
| `draft_commit` | no | **PASS** | Draft committed and revision tagged through the core. |
| `plugin_mcp` | no | FAIL (instrument) | The plugin server **did** connect; the check looked for the wrong server and tool names. See defect 3. |
| `result_fields` | yes | **PASS** | All required and optional result fields present, `permission_denials` among them. |
| `denial` | yes | FAIL (real) | `echo bypass-probe` executed with zero denials. See defect 1. |
| `append_prompt` | yes | **PASS** | `--append-system-prompt-file` reached the model. |

## Defect 1 — `--allowedTools` does not gate built-in Bash (real, load-bearing)

The spike asked arm B's configuration to run `echo bypass-probe`, which is outside
its `Bash(arfc *)` allowance. It ran, `permission_denials` was empty.

A three-way probe pins the cause and refutes the obvious explanation. Under
`--permission-mode dontAsk` with `--tools Bash`:

| `--allowedTools` | Result |
|---|---|
| `Bash(arfc *)` | `echo` ran — leaked |
| `Bash(arfc:*)` | `echo` ran — leaked |
| `Read` (Bash absent from the allowlist entirely) | `echo` ran — leaked |

So this is not a specifier-parsing problem: **`--allowedTools` does not constrain a
built-in tool that `--tools` has enabled.** It is not inert in general — in the
`plugin_mcp` run an MCP tool absent from the allowlist *was* denied under the same
permission mode, with an explicit `system/permission_denied` event.

Consequences for the design:

- Arm separation via `--tools` is **sound**: arm A demonstrably has no Bash.
- Sub-command restriction *within* Bash is **not enforceable this way**, so arm B
  cannot be held to `arfc *`, and D28's `Bash(sqlite3 *)` for arm C will not hold
  either. Both arms effectively get unrestricted Bash.
- The 2026-08-26 note that "`--allowedTools` auto-denies in `-p`" holds for MCP
  tools on 2.1.247, but not for built-ins.

**Decision (2026-08-27):** enforce with a **`PreToolUse` hook** in the campaign
profile, matching each Bash command against its arm's allowance and denying the
rest. The arms stay exactly as §2 and D28 define them. Two consequences follow:
the §2 enforcement table's `--allowedTools` column becomes advisory for built-ins
and normative only for MCP tools, and this document's hermeticity criterion changes
from "the isolated profile fires no hooks" to "the isolated profile fires no
*inherited* hooks besides the campaign's own enforcement hook". Hook activity is
visible in the stream (defect 2), so the audit stage can count bypass attempts from
it. Designing and building that hook is not part of this spike.

## Defect 2 — the hook detector reads a field that 2.1.247 does not emit

`_hook_events` counts events whose `type` starts with `hook` or that carry
`hook_event_name`. On 2.1.247 hook activity arrives as `type: system` with
`subtype: hook_started` / `hook_response`, carrying `hook_event`, `hook_id`,
`hook_name`. The counter therefore returns 0 for both sides, and the check fails
its own positive control.

The underlying measurement is unambiguous and favourable: the control run produced
10 `hook_started` + 10 `hook_response` events; the isolated run produced **no event
carrying any hook key at all**. Hermeticity holds; only the detector is blind.

**Fixed 2026-08-27**: the counter now reads `subtype` and `hook_event`. Replayed
against the captured transcripts it returns 20 for the control and 0 for the
isolated run, so the check passes on this run's own data. The unit test's synthetic
fixture was corrected to the real event shape at the same time — it had encoded the
shape the detector expected rather than the one the CLI emits.

## Defect 3 — the plugin MCP check looks for the wrong names

`plugin_mcp` reported `env_connected: false`, and the report's generated note advises
dropping the `env` block from `plugins/ai-rfc/.mcp.json`. **Do not act on that note.**
The init event shows `plugin:ai-rfc:arfc` with status `connected`, so
`${PANTHER_REPO}` expansion works and the `env` block is fine. Two naming mismatches
cause the false negative:

- `_mcp_status` keys servers by `arfc`; loaded through `--plugin-dir` the server is
  named `plugin:ai-rfc:arfc`.
- The invocation passes `--allowedTools mcp__arfc`, but the tool is exposed as
  `mcp__plugin_ai-rfc_arfc__arfc_status`, so it was denied under `dontAsk` — the one
  place the allowlist did bite.

The note's remedy is also self-contradicted by its own data: `noenv_connected` is
false too, so removing the `env` block would change nothing.

**Fixed 2026-08-27**: server detection now accepts `arfc` or any `…:arfc`, the
allowlist is derived as `mcp__plugin_<plugin-dir>_arfc`, and the hardcoded note —
a conclusion presented as evidence, wrong in exactly the case it fired — is gone.
Replayed against the captured transcripts, detection returns True for both the
plugin-dir and the `--mcp-config` paths.

## The denial reader, and the fixture that cannot happen

`stream.denials()` was replayed against the captured transcripts. It reads the real
2.1.247 denial correctly, returning two entries for the `plugin_mcp_env` run (one
from the `tool_result`, one from the `result` event) and zero for the leaked
`denial` run. It does **not** read the `system/permission_denied` event, which is
present and is the most authoritative of the three; adding it would make the audit
stage's count independent of the assistant's own transcript.

The committed fixture `experiment/tests/fixtures/stream/denied-bash.jsonl` is a
hand-written approximation carrying a Bash denial with the detail *"Permission
denied: Bash(echo bypass-probe) is not in the allowed tools"* and no
`system/permission_denied` event. Defect 1 shows that denial never occurs on
2.1.247. The fixture is therefore left in place rather than refreshed: the run that
should have produced a real Bash denial produced none, and the only genuine denial
captured is an MCP one with a different tool name and shape. Refresh it from a real
Bash denial once the enforcement hook of defect 1 can produce one.

## Incidental hermeticity observation

The two `plugin_mcp` runs, which do **not** pass `--strict-mcp-config`, inherited
account-level connectors (Context7, Mermaid Chart, Scholar Gateway, Google Drive)
even under the isolated `CLAUDE_CONFIG_DIR`. These arrive with the account, not the
config directory. The arm invocations all pass `--strict-mcp-config` and were clean,
so the arms are unaffected — but any future launch that omits that flag will not be
hermetic.

## Fix applied before this run

The first attempt failed at `auth` with "Not logged in · Please run /login" despite a
valid login. `_base_env` passed only `HOME`, `PATH`, `LANG` and `CLAUDE_CONFIG_DIR`.
Measured: adding `USER` makes authentication succeed; adding any of `LOGNAME`,
`SHELL`, `TMPDIR`, `XPC_SERVICE_NAME` or `__CF_USER_TEXT_ENCODING` instead does not.
Deterministic across two runs. Fixed in `fix: pass USER so the isolated profile can
read its credentials`, and mirrored into the harness plan's runner environment.

The failed first run is preserved at `~/arfc-experiments/spike.failed-auth-1/` with
`spike-report.failed-auth-1.json`.

## Commands

```bash
# one-time, by the user
CLAUDE_CONFIG_DIR=~/arfc-experiments/profile claude auth login

# the spike
cd $R && python -m experiment spike --root ~/arfc-experiments --panther-repo $W
```
