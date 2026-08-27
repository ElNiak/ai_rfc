# Spike S0 — isolated-profile hermeticity and arm enforcement

**Verdict: `go: true`** · 2026-08-27 · `claude --version` = **2.1.247 (Claude Code)** ·
model `claude-opus-5` · report `~/arfc-experiments/spike-report.json`

All nine checks pass. D20 (isolated OAuth profile) is **supported**; the `--bare` +
`ANTHROPIC_API_KEY` fallback is not needed. Every plan and the spec were written
against CLI **2.1.246**; everything below is measured on **2.1.247**.

## Results

| Check | Required | Evidence |
|---|---|---|
| `auth` | yes | Isolated profile authenticates; `apiKeySource: none`. |
| `hooks` | yes | Control fired **20** hook events, isolated fired **0**. |
| `claude_md` | yes | A canary `CLAUDE.md` one directory above the cwd does not reach the isolated run. |
| `arm_surface` | yes | A: `Edit,Glob,Grep,Read,Write` + 16 `mcp__arfc__*`, `arfc: connected`, **no Bash**. B and C: Bash, no MCP. Slash commands empty in all three. |
| `draft_commit` | no | Draft committed and revision tagged through the core. |
| `plugin_mcp` | no | `arfc_status` answered `2`; connected **with and without** the env block. |
| `result_fields` | yes | All required and optional result fields present. |
| `denial` | yes | Out-of-family `echo bypass-probe` blocked by the guard; in-family `git --version` still ran. |
| `append_prompt` | yes | `--append-system-prompt-file` reaches the model. |

## The enforcement finding, and what actually works

Spike item 8 asked whether `dontAsk` plus `--allowedTools "Bash(arfc *)"` denies an
out-of-family command. **It does not.** `--allowedTools` does not constrain a
built-in tool that `--tools` has enabled. It is not inert in general — an MCP tool
absent from the allowlist *is* denied under the same mode.

Seven mechanisms were measured against the same probe pair, every verdict read from
the transcript (the model narrates, and sometimes emits literal tool-invocation text,
so an answer-string detector is unreliable). Family under test `ls`; deny probe
`echo bypass-probe`; control `ls`.

| Mechanism | Deny probe | Control | Verdict |
|---|---|---|---|
| `--permission-mode dontAsk` + allowlist (as shipped) | ran | ran | **leaks** |
| `--permission-mode manual` + allowlist | ran | ran | **leaks** |
| `--settings` `permissions.deny: ["Bash(echo *)"]` | blocked | ran | enforces |
| `--disallowedTools "Bash(echo *)"` | blocked | ran | enforces |
| `--settings` `permissions.deny:["Bash"] + allow:["Bash(ls *)"]` | no call | **no call** | over-blocks |
| `PreToolUse` hook returning `permissionDecision: deny` | ran | ran | **leaks** |
| `PreToolUse` hook exiting **2** | blocked | ran | **enforces** |

Three conclusions, each load-bearing:

1. **The permission mode is not the cause.** `manual` leaks exactly as `dontAsk`
   does, so swapping the mode fixes nothing.
2. **Deny rules enforce but cannot express an arm.** They are blacklists; denying
   `Bash` wholesale and re-allowing one family blocks the allowed command too. There
   is no way to say "only `arfc *`" with them.
3. **The `PreToolUse` hook works only through the exit-2 blocking path.** The
   documented `hookSpecificOutput.permissionDecision = "deny"` is silently ignored —
   the hook demonstrably fires (`hook_started` + `hook_response` in the stream) and
   the command runs anyway. This is the trap: the documented shape is the one that
   fails.

## What was built

`experiment/enforcement.py` derives each arm's command families from its existing
`allowed_tools` declaration, so enforcement adds no second source of truth:
A → none, B → `arfc `, C → `python -m panther…a_rfc`, `git `, `sqlite3 `.
`experiment/guard.py` is mounted per arm through `--settings` and exits 2 on
anything outside them.

The guard **fails closed on command substitution** (`$(`, backticks, `<(`, `>(`,
`${`) because a prefix check cannot see what those would run, and it splits on
`&&`, `||`, `;`, `|`, `&` and newlines so every segment must be in family. Verified
live against a real arm C: `git --version` runs, `echo bypass-probe` is blocked, and
`git --version && echo bypass-probe` is blocked — the exact case spec item 8 names.

Hook denials are readable three ways: an errored `tool_result` whose text carries the
guard's message, the result event's `permission_denials`, and the `hook_started` /
`hook_response` pair. The audit stage can use any of them.

**Where the guard lives matters.** The arms pass `--setting-sources project`, so
user-level settings never load — a guard in the profile's `settings.json` would be
inert. It is mounted with `--settings` from the campaign directory, never from
`ARFC_WORKSPACE`, which arms B and C can write.

## Residual threats

- Arms B and C hold unrestricted `Bash` and could edit the campaign's settings file.
  Detection: hash the settings before and after each run, and treat a run whose
  stream carries no `hook_started` events as one that had no guard.
- Redirections inside an in-family segment are allowed (`arfc status > f`). The
  per-run workspace copy contains the blast radius and the digest detects tampering.
- Without `--strict-mcp-config` a run inherits **account-level** connectors
  (Context7, Mermaid, Scholar, Drive) even under an isolated `CLAUDE_CONFIG_DIR`.
  All arm invocations pass it; anything new must too.

## Defects found and fixed along the way

1. **`USER` is required in the subprocess environment.** The first attempt died at
   `auth` with "Not logged in" despite a valid login: `_base_env` passed only
   `HOME`/`PATH`/`LANG`/`CLAUDE_CONFIG_DIR`. Bisected — `USER` fixes it; `LOGNAME`,
   `SHELL`, `TMPDIR`, `XPC_SERVICE_NAME`, `__CF_USER_TEXT_ENCODING` do not. Mirrored
   into the harness plan's runner environment.
2. **The hook detector read a field 2.1.247 does not emit.** It looked for
   `type:hook*` / `hook_event_name`; the CLI emits `type:system` +
   `subtype:hook_started|hook_response` + `hook_event`. The unit fixture had encoded
   the shape the detector expected rather than the one the CLI produces.
3. **The plugin MCP check looked for the wrong names.** Loaded through
   `--plugin-dir` the server is `plugin:ai-rfc:arfc` and its tool is
   `mcp__plugin_ai-rfc_arfc__arfc_status`. Its hardcoded note advising removal of the
   `.mcp.json` `env` block was wrong and is deleted — the server connects either way,
   which also answers spec item 6.
4. **A run can exit 0 with an empty stream.** One `hooks_control` invocation produced
   zero events, failing its own positive control; the same command reproduced fine
   immediately after. Scoring that as evidence made the verdict a coin flip, so an
   empty stream on a clean exit is now retried once.

Superseded runs are preserved at `~/arfc-experiments/spike.failed-auth-1/`,
`spike.pre-guard-2/` and `spike.flaky-control-3/` with their reports.

## Commands

```bash
# one-time, by the user
CLAUDE_CONFIG_DIR=~/arfc-experiments/profile claude auth login

# the spike
cd $R && python -m experiment spike --root ~/arfc-experiments --panther-repo $W
```
