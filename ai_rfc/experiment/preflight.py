"""Preflight S0: prove the isolated profile is hermetic before any product code.

Fourteen real ``claude -p`` calls with a one-dollar budget each feed nine
checks. The report is the go/no-go for D20: if auth, hooks or CLAUDE.md
isolation fail, the documented fallback is ``--bare`` with an API key.
Nothing here runs under pytest; the pure parts are tested, the calls are
made once by hand.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import DEFAULT_MODEL, ExperimentError
from .arms import ARMS, MCP_FILE, arm_flags, arm_profile, mcp_config
from .enforcement import bash_prefixes, render_settings
from .paths import profile_dir
from .stream import (
    assistant_text,
    denials,
    hook_events,
    init_event,
    parse_stream,
    result_event,
    tool_results,
    usage_series,
)
from ai_rfc.server.testing import build_workspace

CHECKS = (
    "auth",
    "hooks",
    "claude_md",
    "arm_surface",
    "draft_commit",
    "plugin_mcp",
    "result_fields",
    "denial",
    "append_prompt",
)
REQUIRED = frozenset(CHECKS) - {"draft_commit", "plugin_mcp"}
CLAUDE_MD_CANARY = "AI_RFC-CANARY-7731"
APPEND_PROMPT_CANARY = "PASS-4412"
GUARD_SETTINGS = "guard-C.json"
ALLOW_SETTINGS = "guard-allow.json"
APPEND_PROMPT = "append.md"
_LIST_TOOLS = (
    "List the names of the tools available to you, one per line, then reply "
    "DONE. Do not call any tool."
)
_ECHO = "Use the Bash tool to run exactly: echo hook-probe . Then reply DONE."
_CODEWORD = "What is the secret codeword? Reply with just the codeword, or NONE."
_STATUS = "Call the ai_rfc_status tool and reply with only the value of clusters_total."
_BYPASS = "Use the Bash tool to run exactly: echo bypass-probe . Then reply DONE."
_IN_FAMILY = "Use the Bash tool to run exactly: git --version . Then reply DONE."


@dataclass(frozen=True)
class Invocation:
    """One ``claude -p`` call: what to run, where, and in which environment."""

    name: str
    argv: tuple[str, ...]
    env: dict[str, str]
    cwd: Path


def _scratch(root: Path) -> Path:
    return root / "spike"


def _base_env(profile_path: Path) -> dict[str, str]:
    # Measured on Claude Code 2.1.247 / macOS: drop USER and the CLI cannot reach
    # its stored credentials, answering "Not logged in" however valid the profile.
    return {
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "CLAUDE_CONFIG_DIR": str(profile_path),
    }


def _spike_flags(model: str, *extra: str) -> tuple[str, ...]:
    return (
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--max-budget-usd",
        "1",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "project",
        *extra,
    )


def build_invocations(
    *,
    root: Path,
    plugin_dir: Path,
    workspace: Path,
    claude_bin: str = "claude",
    model: str = DEFAULT_MODEL,
) -> list[Invocation]:
    """Every call the spike makes, in order. Pure: nothing runs here."""
    scratch = _scratch(root)
    cwd = scratch / "cwd"
    isolated = _base_env(profile_dir(root))
    plugin_env = {**isolated, "AI_RFC_WORKSPACE": str(workspace)}

    def call(
        name: str,
        prompt: str,
        *flags: str,
        where: Path,
        env: dict[str, str] = isolated,
    ) -> Invocation:
        return Invocation(
            name, (claude_bin, "-p", prompt, *_spike_flags(model, *flags)), env, where
        )

    def surface_call(arm: str) -> Invocation:
        this_arm = arm_profile(arm)
        mcp_path = scratch / MCP_FILE if this_arm.uses_mcp else None
        return call(
            f"arm_surface_{arm}",
            _LIST_TOOLS,
            "--disable-slash-commands",
            *arm_flags(this_arm, mcp_path),
            where=workspace,
        )

    no_tools = ("--tools", "")
    hook_flags = (
        "--include-hook-events",
        "--tools",
        "Bash",
        "--allowedTools",
        "Bash(echo *)",
    )
    # --plugin-dir namespaces the server, so its tools arrive as
    # mcp__plugin_<plugin>_ai_rfc__*, not the bare mcp__ai_rfc__* of --mcp-config.
    plugin_flags = (
        "--plugin-dir",
        str(plugin_dir),
        *no_tools,
        "--allowedTools",
        f"mcp__plugin_{plugin_dir.name}_ai_rfc",
    )
    guard_flags = (
        "--include-hook-events",
        *arm_flags(arm_profile("C"), None, scratch / GUARD_SETTINGS),
    )
    return [
        call("auth", "Reply with exactly: AI_RFC-OK", *no_tools, where=cwd),
        call("hooks_isolated", _ECHO, *hook_flags, where=cwd),
        # The positive control mounts a hook of our own that allows the probe,
        # rather than borrowing whatever the user happens to have configured.
        # Reading the real ~/.claude made this the one non-hermetic invocation
        # in the spike, and it failed intermittently (empty stream, exit 1)
        # while the identical command succeeded standalone.
        call(
            "hooks_control",
            _ECHO,
            *hook_flags,
            "--settings",
            str(scratch / ALLOW_SETTINGS),
            where=cwd,
        ),
        call(
            "claude_md_control",
            _CODEWORD,
            *no_tools,
            where=scratch / "canary" / "sub",
        ),
        call("claude_md_isolated", _CODEWORD, *no_tools, where=cwd),
        *(surface_call(arm) for arm in ARMS),
        call(
            "draft_commit",
            f"Append the line 'probe' to {workspace}/draft/draft-test-spec.md using the "
            f"Edit tool. Then use the Bash tool to run exactly: git -C {workspace}/draft "
            f"add -A && git -C {workspace}/draft commit -m probe . Then reply DONE.",
            *arm_flags(arm_profile("C"), None),
            where=workspace,
        ),
        call("plugin_mcp_env", _STATUS, *plugin_flags, where=workspace, env=plugin_env),
        call("plugin_mcp_noenv", _STATUS, *plugin_flags, where=workspace),
        call("denial", _BYPASS, *guard_flags, where=cwd),
        call("denial_control", _IN_FAMILY, *guard_flags, where=cwd),
        call(
            "append_prompt",
            "What is the passphrase? Reply with just the passphrase.",
            *no_tools,
            "--append-system-prompt-file",
            str(scratch / APPEND_PROMPT),
            where=cwd,
        ),
    ]


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def prepare_scratch(*, root: Path, python: str) -> Path:
    """Create the scratch tree the invocations refer to; returns the workspace."""
    scratch = _scratch(root)
    if scratch.exists():
        raise ExperimentError(f"{scratch} exists; remove it to re-run the spike")
    (scratch / "cwd").mkdir(parents=True)
    canary = scratch / "canary"
    (canary / "sub").mkdir(parents=True)
    (canary / "CLAUDE.md").write_text(f"The secret codeword is {CLAUDE_MD_CANARY}.\n")
    (scratch / APPEND_PROMPT).write_text(
        f"When asked for the passphrase, answer {APPEND_PROMPT_CANARY}.\n"
    )
    guard = Path(__file__).resolve().parent / "guard.py"
    _write_json(
        scratch / GUARD_SETTINGS,
        render_settings(
            python=python, guard=guard, prefixes=bash_prefixes(arm_profile("C"))
        ),
    )
    _write_json(
        scratch / ALLOW_SETTINGS,
        render_settings(python=python, guard=guard, prefixes=("echo ",)),
    )
    workspace = build_workspace(scratch / "ws")
    _write_json(
        scratch / MCP_FILE,
        mcp_config(python=python, workspace=workspace),
    )
    return workspace


def _decoded(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _parsed(stdout: str) -> list[dict[str, Any]]:
    try:
        return parse_stream(stdout)
    except ExperimentError:
        return []


def run_invocation(invocation: Invocation, timeout_s: int) -> dict[str, Any]:
    """Run one invocation; never raises on a non-zero exit."""
    try:
        completed = subprocess.run(
            list(invocation.argv),
            cwd=invocation.cwd,
            env=invocation.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as expired:
        return {
            "exit_code": None,
            "events": _parsed(_decoded(expired.stdout)),
            "stderr": _decoded(expired.stderr),
            "timed_out": True,
        }
    except FileNotFoundError as missing:
        raise ExperimentError(f"cannot run {invocation.argv[0]}: {missing}") from None
    return {
        "exit_code": completed.returncode,
        "events": _parsed(completed.stdout),
        "stderr": completed.stderr,
        "timed_out": False,
    }


def _answer(outcome: dict[str, Any]) -> str:
    events = outcome.get("events") or []
    final = result_event(events) or {}
    return str(final.get("result") or assistant_text(events))


def _mcp_status(events: list[dict[str, Any]]) -> dict[str, str]:
    init = init_event(events) or {}
    servers = init.get("mcp_servers") or []
    status = {}
    for server in servers:
        if isinstance(server, dict):
            status[str(server.get("name"))] = str(server.get("status"))
    return status


def _ai_rfc_connected(events: list[dict[str, Any]]) -> bool:
    """Whether the ai_rfc server is connected, under either loading path.

    Args:
        events: One invocation's stream-json events.

    Returns:
        True when a connected server is named ``ai_rfc`` (``--mcp-config``) or
        ``plugin:<plugin>:ai_rfc`` (``--plugin-dir``).
    """
    return any(
        (name == "ai_rfc" or name.endswith(":ai_rfc")) and status == "connected"
        for name, status in _mcp_status(events).items()
    )


def _tools(events: list[dict[str, Any]]) -> list[str]:
    return [str(t) for t in ((init_event(events) or {}).get("tools") or [])]


#: One check's outcome: whether it passed, and the evidence the report carries.
CheckResult = tuple[bool, dict[str, Any]]


def _auth_check(auth: dict[str, Any]) -> CheckResult:
    final = result_event(auth["events"]) or {}
    passed = (
        auth["exit_code"] == 0
        and "AI_RFC-OK" in _answer(auth)
        and not final.get("is_error", True)
    )
    return passed, {
        "exit_code": auth["exit_code"],
        "api_key_source": (init_event(auth["events"]) or {}).get("apiKeySource"),
        "stderr_tail": auth["stderr"][-300:],
    }


def _hooks_check(isolated: dict[str, Any], control: dict[str, Any]) -> CheckResult:
    isolated_hooks = len(hook_events(isolated["events"]))
    control_hooks = len(hook_events(control["events"]))
    passed = isolated["exit_code"] == 0 and isolated_hooks == 0 and control_hooks > 0
    return passed, {
        "isolated_hook_events": isolated_hooks,
        "control_hook_events": control_hooks,
    }


def _claude_md_check(isolated: dict[str, Any], control: dict[str, Any]) -> CheckResult:
    answer = _answer(isolated)
    control_leaked = CLAUDE_MD_CANARY in _answer(control)
    passed = (
        isolated["exit_code"] == 0 and CLAUDE_MD_CANARY not in answer and control_leaked
    )
    return passed, {"control_leaked": control_leaked, "isolated_answer": answer[:80]}


def _arm_surface_check(surfaces: dict[str, dict[str, Any]]) -> CheckResult:
    tools = {arm: _tools(o["events"]) for arm, o in surfaces.items()}
    mcp = {arm: _mcp_status(o["events"]) for arm, o in surfaces.items()}
    slash = {
        arm: (init_event(o["events"]) or {}).get("slash_commands") or []
        for arm, o in surfaces.items()
    }
    passed = (
        all(outcome["exit_code"] == 0 for outcome in surfaces.values())
        and "Bash" not in tools["A"]
        and "Bash" in tools["B"]
        and "Bash" in tools["C"]
        and mcp["A"].get("ai_rfc") == "connected"
        and not mcp["B"]
        and not mcp["C"]
        and not any(slash.values())
    )
    return passed, {"tools": tools, "mcp_servers": mcp, "slash_commands": slash}


def _draft_commit_check(workspace: Path, outcome: dict[str, Any]) -> CheckResult:
    committed = subprocess.run(
        ["git", "-C", str(workspace / "draft"), "log", "--oneline", "-1"],
        capture_output=True,
        text=True,
    )
    passed = committed.returncode == 0 and "probe" in committed.stdout
    return passed, {
        "head": committed.stdout.strip(),
        "exit_code": outcome["exit_code"],
    }


def _plugin_mcp_check(
    with_env: dict[str, Any], without_env: dict[str, Any]
) -> CheckResult:
    env_connected = _ai_rfc_connected(with_env["events"])
    answer = _answer(with_env)
    return env_connected and "2" in answer, {
        "env_connected": env_connected,
        "noenv_connected": _ai_rfc_connected(without_env["events"]),
        "answer": answer[:40],
    }


def _result_fields_check(auth: dict[str, Any]) -> CheckResult:
    required_keys = ("total_cost_usd", "usage", "num_turns", "duration_ms")
    optional_keys = (
        "modelUsage",
        "duration_api_ms",
        "permission_denials",
        "session_id",
    )
    final = result_event(auth["events"]) or {}
    series = usage_series(auth["events"])
    return all(key in final for key in required_keys), {
        "present": sorted(k for k in final),
        "missing_required": [k for k in required_keys if k not in final],
        "missing_optional": [k for k in optional_keys if k not in final],
        "series_total": series[-1]["total"] if series else None,
        "result_usage": final.get("usage"),
    }


def _denial_check(denial: dict[str, Any], control: dict[str, Any]) -> CheckResult:
    leaked = any(
        not r["is_error"] and "bypass-probe" in r["text"]
        for r in tool_results(denial["events"]).values()
    )
    found = denials(denial["events"])
    in_family_ran = any(
        not r["is_error"] for r in tool_results(control["events"]).values()
    )
    passed = denial["exit_code"] == 0 and not leaked and bool(found) and in_family_ran
    return passed, {
        "leaked": leaked,
        # Recorded key: spike-report.json keeps the word the evidence was
        # written under, though the code now says "prefix" everywhere else.
        "in_family_ran": in_family_ran,
        "guard_hooks": len(hook_events(denial["events"])),
        "denials": found[:3],
    }


def _append_prompt_check(appended: dict[str, Any]) -> CheckResult:
    answer = _answer(appended)
    passed = appended["exit_code"] == 0 and APPEND_PROMPT_CANARY in answer
    return passed, {"answer": answer[:40]}


def evaluate(
    outcomes: dict[str, dict[str, Any]], workspace: Path
) -> list[dict[str, Any]]:
    """Turn raw outcomes into the nine check verdicts.

    Args:
        outcomes: Each invocation's result keyed by name; a name with no
            outcome scores as a failure rather than an error.
        workspace: The spike workspace, whose draft repository the
            ``draft_commit`` check reads with ``git log``.

    Returns:
        One record per name in :data:`CHECKS`, in that order, each carrying
        ``check``, ``passed``, ``required`` and ``evidence``.
    """

    def got(name: str) -> dict[str, Any]:
        return outcomes.get(name) or {
            "exit_code": None,
            "events": [],
            "stderr": "missing",
            "timed_out": False,
        }

    verdicts = {
        "auth": _auth_check(got("auth")),
        "hooks": _hooks_check(got("hooks_isolated"), got("hooks_control")),
        "claude_md": _claude_md_check(
            got("claude_md_isolated"), got("claude_md_control")
        ),
        "arm_surface": _arm_surface_check(
            {arm: got(f"arm_surface_{arm}") for arm in ARMS}
        ),
        "draft_commit": _draft_commit_check(workspace, got("draft_commit")),
        "plugin_mcp": _plugin_mcp_check(got("plugin_mcp_env"), got("plugin_mcp_noenv")),
        "result_fields": _result_fields_check(got("auth")),
        "denial": _denial_check(got("denial"), got("denial_control")),
        "append_prompt": _append_prompt_check(got("append_prompt")),
    }
    return [
        {
            "check": check,
            "passed": bool(verdicts[check][0]),
            "required": check in REQUIRED,
            "evidence": verdicts[check][1],
        }
        for check in CHECKS
    ]


def _run_all(
    invocations: list[Invocation], *, scratch: Path, timeout_s: int
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Make every call in order, writing each transcript into the scratch tree.

    Args:
        invocations: The calls to make, in order.
        scratch: Where each ``<name>.jsonl`` transcript lands.
        timeout_s: Wall-clock cap on one call.

    Returns:
        The outcomes keyed by invocation name, and the per-call log the report
        carries. A failed ``auth`` stops the run, so both may be short.
    """
    outcomes: dict[str, dict[str, Any]] = {}
    log: list[dict[str, Any]] = []
    total = len(invocations)
    for index, invocation in enumerate(invocations, start=1):
        # Each of these launches a real session and may sit for the whole
        # timeout. Announced before it runs rather than after, because a line
        # that appears once the call returns says nothing while it is the one
        # you are waiting on — and a silent quarter of an hour reads as a hang.
        print(
            f"preflight {index}/{total}: {invocation.name} " f"(up to {timeout_s}s)",
            file=sys.stderr,
        )
        outcome = run_invocation(invocation, timeout_s)
        if outcome["exit_code"] == 0 and not outcome["events"]:
            # A run can exit 0 having written nothing at all; that is a harness
            # failure, and scoring it as evidence makes the verdict a coin flip.
            print(
                f"note: {invocation.name}: empty stream, retrying once", file=sys.stderr
            )
            outcome = run_invocation(invocation, timeout_s)
        outcomes[invocation.name] = outcome
        log.append(
            {
                "name": invocation.name,
                "argv": list(invocation.argv),
                "cwd": str(invocation.cwd),
                "exit_code": outcome["exit_code"],
                "timed_out": outcome["timed_out"],
                "events": len(outcome["events"]),
                "stderr_tail": outcome["stderr"][-500:],
            }
        )
        (scratch / f"{invocation.name}.jsonl").write_text(
            "\n".join(json.dumps(e, sort_keys=True) for e in outcome["events"]) + "\n"
        )
        if invocation.name == "auth" and outcome["exit_code"] != 0:
            break
    return outcomes, log


def run_preflight(
    *,
    root: Path,
    plugin_dir: Path,
    claude_bin: str = "claude",
    model: str = DEFAULT_MODEL,
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Prepare the scratch tree, make every call, evaluate, write the report.

    Returns:
        The report; ``report["go"]`` is True when every required check passed.
    """
    workspace = prepare_scratch(root=root, python=sys.executable)
    invocations = build_invocations(
        root=root,
        plugin_dir=plugin_dir,
        workspace=workspace,
        claude_bin=claude_bin,
        model=model,
    )
    outcomes, log = _run_all(invocations, scratch=_scratch(root), timeout_s=timeout_s)
    checks = evaluate(outcomes, workspace)
    version = subprocess.run([claude_bin, "--version"], capture_output=True, text=True)
    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "claude_version": version.stdout.strip(),
        "model": model,
        "go": all(c["passed"] for c in checks if c["required"]),
        "checks": checks,
        "invocations": log,
    }
    (root / "spike-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return report
