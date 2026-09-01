# Spike S0 — isolated-profile hermeticity and arm enforcement

**Verdict: `go: true`** · first measured 2026-08-27 on **2.1.247**, re-verified
2026-08-28 on **2.1.250**, re-armed 2026-08-31 on **2.1.251 (Claude Code)** ·
model `claude-opus-5` · report `~/ai-rfc-experiments/spike-report.json`

All required checks pass on all three versions. D20 (isolated OAuth profile) is
**supported**; the `--bare` + `ANTHROPIC_API_KEY` fallback is not needed. Every plan
and the spec were written against CLI **2.1.246**; the measurements below were taken
on **2.1.247**, re-taken on **2.1.250**, and re-taken again on **2.1.251** — see
[§ Re-verification on 2.1.250](#re-verification-on-21250) and
[§ Re-arm on 2.1.251](#re-arm-on-21251).

**2.1.251 is the version the aioquic pilot actually ran on**, confirmed from each
run's `status.json` (`claude_version: "2.1.251 (Claude Code)"`) rather than from
whatever the CLI reports today. The gate and the spend agree.

## Results

| Check | Required | Evidence |
|---|---|---|
| `auth` | yes | Isolated profile authenticates; `apiKeySource: none`. |
| `hooks` | yes | Positive control fired **2** hook events, isolated fired **0**. |
| `claude_md` | yes | A canary `CLAUDE.md` one directory above the cwd does not reach the isolated run. |
| `arm_surface` | yes | A: `Edit,Glob,Grep,Read,Write` + 16 `mcp__ai_rfc__*`, `ai_rfc: connected`, **no Bash**. B and C: Bash, no MCP. Slash commands empty in all three. |
| `draft_commit` | no | Draft committed and revision tagged through the core. |
| `plugin_mcp` | no | `ai_rfc_status` answered `2`; connected **with and without** the env block. |
| `result_fields` | yes | All required and optional result fields present. |
| `denial` | yes | Out-of-family `echo bypass-probe` blocked by the guard; in-family `git --version` still ran. |
| `append_prompt` | yes | `--append-system-prompt-file` reaches the model. |

## Re-verification on 2.1.250

The CLI auto-updated from 2.1.247 to **2.1.250** before the pilot. Since the arm
separation rests entirely on measured 2.1.247 behaviour — and the whole test suite
runs against a fake `claude` that encodes that behaviour by construction — the spike
was re-run rather than assumed. Verdict `go: true`, all seven required checks passing.

**The `denial` evidence is byte-identical across the two versions**: the same refusal
text, the same two denial sources (`tool_result` and the result event's
`permission_denials`), `guard_hooks: 2`, and the in-family control still running. The
hook event shape is unchanged (`system` / `hook_started` | `hook_response`, each
carrying `hook_event: PreToolUse`). `result_fields` reports nothing missing, required
or optional. Nothing the harness depends on moved.

Two instrument defects surfaced, neither a product regression:

- **The positive control was not hermetic and was flaky.** `hooks_control` was the one
  invocation that read the user's real `~/.claude`, borrowing whatever hooks happened
  to be configured there. Inside the spike it produced nothing — exit 1 on one run,
  exit 0 with an empty stream (and a failed retry) on the next — while the identical
  argv, environment and cwd exited 0 with 26–28 events when run standalone. It now
  mounts an always-allow guard of its own on the isolated profile (`guard-allow.json`,
  family `echo `), so the control is deterministic and no invocation in the spike
  reads the real configuration any more. `hooks` passes with control **2**, isolated **0**.
- **`plugin_mcp` is intermittent** (passed one run of three). This is the known
  `--plugin-dir` MCP startup race; it is a product check, not required, and the arms
  mount the server with `--mcp-config`, which is unaffected.

## Re-arm on 2.1.251

The CLI moved again, from 2.1.250 to **2.1.251**, between the aborted 2026-08-28
launch and the 2026-08-31 relaunch. The standing rule is that the enforcement
mechanism is a measured property of the CLI and not a contract, so the spike was
re-run before any further spend rather than carried over. Ran 2026-08-31T09:11:01Z:
verdict `go: true`, **9 of 9 checks passing** — all seven required, and both
optional ones.

`plugin_mcp` passes here. It is the one check that had *failed* on 2.1.250 (the
known `--plugin-dir` MCP startup race), and it remains non-required precisely
because the arms mount the server with `--mcp-config`, which the race does not
touch. Its passing is therefore a convenience, not a load-bearing change: nothing
in the arm separation depends on it either way.

The superseded 2.1.250 evidence is preserved rather than overwritten, at
`~/ai-rfc-experiments/spike-report.2.1.250-final.json` and
`~/ai-rfc-experiments/spike.2.1.250-final/`. Earlier flaky-control runs from the same
version are kept alongside them under their own suffixes.

## The enforcement finding, and what actually works

Spike item 8 asked whether `dontAsk` plus `--allowedTools "Bash(ai_rfc *)"` denies an
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
   is no way to say "only `ai_rfc *`" with them.
3. **The `PreToolUse` hook works only through the exit-2 blocking path.** The
   documented `hookSpecificOutput.permissionDecision = "deny"` is silently ignored —
   the hook demonstrably fires (`hook_started` + `hook_response` in the stream) and
   the command runs anyway. This is the trap: the documented shape is the one that
   fails.

## What was built

`experiment/enforcement.py` derives each arm's command families from its existing
`allowed_tools` declaration, so enforcement adds no second source of truth:
A → none, B → `ai_rfc `, C → `python -m panther…ai_rfc`, `git `, `sqlite3 `.
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
`AI_RFC_WORKSPACE`, which arms B and C can write.

## Residual threats

- Arms B and C hold unrestricted `Bash` and could edit the campaign's settings file.
  Detection: hash the settings before and after each run, and treat a run whose
  stream carries no `hook_started` events as one that had no guard.
- Redirections inside an in-family segment are allowed (`ai_rfc status > f`). The
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
   `--plugin-dir` the server is `plugin:ai-rfc:ai_rfc` and its tool is
   `mcp__plugin_ai-rfc_ai_rfc__ai_rfc_status`. Its hardcoded note advising removal of the
   `.mcp.json` `env` block was wrong and is deleted — the server connects either way,
   which also answers spec item 6.
4. **A run can exit 0 with an empty stream.** One `hooks_control` invocation produced
   zero events, failing its own positive control; the same command reproduced fine
   immediately after. Scoring that as evidence made the verdict a coin flip, so an
   empty stream on a clean exit is now retried once.

Superseded runs are preserved at `~/ai-rfc-experiments/spike.failed-auth-1/`,
`spike.pre-guard-2/` and `spike.flaky-control-3/` with their reports.

## Commands

```bash
# one-time, by the user
CLAUDE_CONFIG_DIR=~/ai-rfc-experiments/profile claude auth login

# the spike
cd $R && python -m experiment preflight --root ~/ai-rfc-experiments --panther-repo $W
```

The subcommand is `preflight`; it still writes `spike-report.json` under
`~/ai-rfc-experiments/spike/`. Those names are recorded evidence from runs that
already happened, so they keep the word the reports were published under even
though the command no longer says it.
