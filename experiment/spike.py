"""Spike S0: prove the isolated profile is hermetic before any product code.

Thirteen real ``claude -p`` calls with a one-dollar budget each feed nine
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

from . import ExperimentError
from .arms import arm_flags, mcp_config, profile
from .paths import profile_dir
from .stream import (
    assistant_text,
    denials,
    init_event,
    parse_stream,
    result_event,
    tool_results,
    usage_series,
)

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
CANARY = "ARFC-CANARY-7731"
PASSPHRASE = "PASS-4412"
_LIST_TOOLS = (
    "List the names of the tools available to you, one per line, then reply "
    "DONE. Do not call any tool."
)
_ECHO = "Use the Bash tool to run exactly: echo hook-probe . Then reply DONE."
_CODEWORD = "What is the secret codeword? Reply with just the codeword, or NONE."
_STATUS = "Call the arfc_status tool and reply with only the value of clusters_total."


@dataclass(frozen=True)
class Invocation:
    """One ``claude -p`` call: what to run, where, and in which environment."""

    name: str
    argv: tuple[str, ...]
    env: dict[str, str]
    cwd: Path


def _base_env(profile_path: Path | None) -> dict[str, str]:
    # Measured on Claude Code 2.1.247 / macOS: drop USER and the CLI cannot reach
    # its stored credentials, answering "Not logged in" however valid the profile.
    env = {
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if profile_path is not None:
        env["CLAUDE_CONFIG_DIR"] = str(profile_path)
    return env


def _common(model: str, *extra: str) -> tuple[str, ...]:
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
        *extra,
    )


def build_invocations(
    *,
    root: Path,
    panther_repo: Path,
    plugin_dir: Path,
    workspace: Path,
    claude_bin: str = "claude",
    model: str = "claude-opus-5",
) -> list[Invocation]:
    """Every call the spike makes, in order. Pure: nothing runs here."""
    scratch = root / "spike"
    cwd = scratch / "cwd"
    canary_sub = scratch / "canary" / "sub"
    isolated = _base_env(profile_dir(root))
    inherited = _base_env(None)
    plugin_env = {
        **isolated,
        "PANTHER_REPO": str(panther_repo),
        "ARFC_WORKSPACE": str(workspace),
    }
    mcp_path = scratch / "arfc.json"
    prompt_file = scratch / "append.md"
    project = ("--setting-sources", "project")
    no_tools = ("--tools", "")
    # --plugin-dir namespaces the server, so its tools arrive as
    # mcp__plugin_<plugin>_arfc__*, not the bare mcp__arfc__* of --mcp-config.
    plugin_tool_prefix = f"mcp__plugin_{plugin_dir.name}_arfc"

    def call(name: str, prompt: str, *flags: str, env: dict[str, str], where: Path):
        return Invocation(
            name, (claude_bin, "-p", prompt, *_common(model, *flags)), env, where
        )

    hook_flags = (
        "--include-hook-events",
        "--tools",
        "Bash",
        "--allowedTools",
        "Bash(echo *)",
    )
    surface = ("--disable-slash-commands",)
    return [
        call(
            "auth",
            "Reply with exactly: ARFC-OK",
            *project,
            *no_tools,
            env=isolated,
            where=cwd,
        ),
        call("hooks_isolated", _ECHO, *project, *hook_flags, env=isolated, where=cwd),
        call("hooks_control", _ECHO, *hook_flags, env=inherited, where=panther_repo),
        call(
            "claude_md_control",
            _CODEWORD,
            *project,
            *no_tools,
            env=isolated,
            where=canary_sub,
        ),
        call(
            "claude_md_isolated",
            _CODEWORD,
            *project,
            *no_tools,
            env=isolated,
            where=cwd,
        ),
        call(
            "arm_surface_A",
            _LIST_TOOLS,
            *project,
            *surface,
            *arm_flags(profile("A"), mcp_path),
            env=isolated,
            where=workspace,
        ),
        call(
            "arm_surface_B",
            _LIST_TOOLS,
            *project,
            *surface,
            *arm_flags(profile("B"), None),
            env=isolated,
            where=workspace,
        ),
        call(
            "arm_surface_C",
            _LIST_TOOLS,
            *project,
            *surface,
            *arm_flags(profile("C"), None),
            env=isolated,
            where=workspace,
        ),
        call(
            "draft_commit",
            f"Append the line 'probe' to {workspace}/draft/draft-test-spec.md using the "
            f"Edit tool. Then use the Bash tool to run exactly: git -C {workspace}/draft "
            f"add -A && git -C {workspace}/draft commit -m probe . Then reply DONE.",
            *project,
            *arm_flags(profile("C"), None),
            env=isolated,
            where=workspace,
        ),
        call(
            "plugin_mcp_env",
            _STATUS,
            *project,
            "--plugin-dir",
            str(plugin_dir),
            *no_tools,
            "--allowedTools",
            plugin_tool_prefix,
            env=plugin_env,
            where=workspace,
        ),
        call(
            "plugin_mcp_noenv",
            _STATUS,
            *project,
            "--plugin-dir",
            str(plugin_dir),
            *no_tools,
            "--allowedTools",
            plugin_tool_prefix,
            env=isolated,
            where=workspace,
        ),
        call(
            "denial",
            "Use the Bash tool to run exactly: echo bypass-probe . If the call is "
            "denied, reply DENIED; otherwise reply with the command output.",
            *project,
            *arm_flags(profile("B"), None),
            env=isolated,
            where=cwd,
        ),
        call(
            "append_prompt",
            "What is the passphrase? Reply with just the passphrase.",
            *project,
            *no_tools,
            "--append-system-prompt-file",
            str(prompt_file),
            env=isolated,
            where=cwd,
        ),
    ]


def prepare_scratch(
    *, root: Path, panther_repo: Path, server_src: Path, python: str
) -> Path:
    """Create the scratch tree the invocations refer to; returns the workspace."""
    scratch = root / "spike"
    if scratch.exists():
        raise ExperimentError(f"{scratch} exists; remove it to re-run the spike")
    (scratch / "cwd").mkdir(parents=True)
    canary = scratch / "canary"
    (canary / "sub").mkdir(parents=True)
    (canary / "CLAUDE.md").write_text(f"The secret codeword is {CANARY}.\n")
    (scratch / "append.md").write_text(
        f"When asked for the passphrase, answer {PASSPHRASE}.\n"
    )
    for entry in (str(server_src), str(panther_repo)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from ai_rfc_server.testing import build_workspace

    workspace = build_workspace(scratch / "ws")
    (scratch / "arfc.json").write_text(
        json.dumps(
            mcp_config(
                python=python,
                server_src=server_src,
                panther_repo=panther_repo,
                workspace=workspace,
            ),
            indent=2,
        )
        + "\n"
    )
    return workspace


def run_claude(invocation: Invocation, timeout_s: int) -> dict[str, Any]:
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
        stdout = expired.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        stderr = expired.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        try:
            events = parse_stream(stdout) if stdout else []
        except ExperimentError:
            events = []
        return {
            "exit_code": None,
            "events": events,
            "stderr": stderr or "",
            "timed_out": True,
        }
    except FileNotFoundError as missing:
        raise ExperimentError(f"cannot run {invocation.argv[0]}: {missing}") from None
    try:
        events = parse_stream(completed.stdout)
    except ExperimentError:
        events = []
    return {
        "exit_code": completed.returncode,
        "events": events,
        "stderr": completed.stderr,
        "timed_out": False,
    }


def _answer(outcome: dict[str, Any]) -> str:
    events = outcome.get("events") or []
    final = result_event(events) or {}
    return str(final.get("result") or assistant_text(events))


def _hook_events(events: list[dict[str, Any]]) -> int:
    # 2.1.247 reports hook activity as system events with subtype hook_started /
    # hook_response carrying hook_event, never as a dedicated top-level type.
    return sum(
        1
        for event in events
        if str(event.get("subtype", "")).startswith("hook_") or "hook_event" in event
    )


def _mcp_status(events: list[dict[str, Any]]) -> dict[str, str]:
    init = init_event(events) or {}
    servers = init.get("mcp_servers") or []
    status = {}
    for server in servers:
        if isinstance(server, dict):
            status[str(server.get("name"))] = str(server.get("status"))
    return status


def _arfc_connected(events: list[dict[str, Any]]) -> bool:
    """Whether the arfc server is connected, under either loading path.

    Args:
        events: One invocation's stream-json events.

    Returns:
        True when a connected server is named ``arfc`` (``--mcp-config``) or
        ``plugin:<plugin>:arfc`` (``--plugin-dir``).
    """
    return any(
        (name == "arfc" or name.endswith(":arfc")) and status == "connected"
        for name, status in _mcp_status(events).items()
    )


def _tools(events: list[dict[str, Any]]) -> list[str]:
    return [str(t) for t in ((init_event(events) or {}).get("tools") or [])]


def evaluate(
    outcomes: dict[str, dict[str, Any]], workspace: Path
) -> list[dict[str, Any]]:
    """Turn raw outcomes into the nine check verdicts (pure)."""

    def got(name: str) -> dict[str, Any]:
        return outcomes.get(name) or {
            "exit_code": None,
            "events": [],
            "stderr": "missing",
            "timed_out": False,
        }

    checks: list[dict[str, Any]] = []

    def add(check: str, passed: bool, evidence: dict[str, Any]) -> None:
        checks.append(
            {
                "check": check,
                "passed": bool(passed),
                "required": check in REQUIRED,
                "evidence": evidence,
            }
        )

    auth = got("auth")
    auth_ok = (
        auth["exit_code"] == 0
        and "ARFC-OK" in _answer(auth)
        and not (result_event(auth["events"]) or {}).get("is_error", True)
    )
    add(
        "auth",
        auth_ok,
        {
            "exit_code": auth["exit_code"],
            "api_key_source": (init_event(auth["events"]) or {}).get("apiKeySource"),
            "stderr_tail": auth["stderr"][-300:],
        },
    )

    isolated, control = got("hooks_isolated"), got("hooks_control")
    add(
        "hooks",
        isolated["exit_code"] == 0
        and _hook_events(isolated["events"]) == 0
        and _hook_events(control["events"]) > 0,
        {
            "isolated_hook_events": _hook_events(isolated["events"]),
            "control_hook_events": _hook_events(control["events"]),
        },
    )

    md_control, md_isolated = got("claude_md_control"), got("claude_md_isolated")
    add(
        "claude_md",
        md_isolated["exit_code"] == 0
        and CANARY not in _answer(md_isolated)
        and CANARY in _answer(md_control),
        {
            "control_leaked": CANARY in _answer(md_control),
            "isolated_answer": _answer(md_isolated)[:80],
        },
    )

    surfaces = {arm: got(f"arm_surface_{arm}") for arm in ("A", "B", "C")}
    tools = {arm: _tools(o["events"]) for arm, o in surfaces.items()}
    mcp = {arm: _mcp_status(o["events"]) for arm, o in surfaces.items()}
    slash = {
        arm: (init_event(o["events"]) or {}).get("slash_commands") or []
        for arm, o in surfaces.items()
    }
    surface_ok = (
        all(surfaces[arm]["exit_code"] == 0 for arm in surfaces)
        and "Bash" not in tools["A"]
        and "Bash" in tools["B"]
        and "Bash" in tools["C"]
        and mcp["A"].get("arfc") == "connected"
        and not mcp["B"]
        and not mcp["C"]
        and not any(slash.values())
    )
    add(
        "arm_surface",
        surface_ok,
        {"tools": tools, "mcp_servers": mcp, "slash_commands": slash},
    )

    committed = subprocess.run(
        ["git", "-C", str(workspace / "draft"), "log", "--oneline", "-1"],
        capture_output=True,
        text=True,
    )
    add(
        "draft_commit",
        committed.returncode == 0 and "probe" in committed.stdout,
        {
            "head": committed.stdout.strip(),
            "exit_code": got("draft_commit")["exit_code"],
        },
    )

    env_run, noenv_run = got("plugin_mcp_env"), got("plugin_mcp_noenv")
    env_connected = _arfc_connected(env_run["events"])
    noenv_connected = _arfc_connected(noenv_run["events"])
    add(
        "plugin_mcp",
        env_connected and "2" in _answer(env_run),
        {
            "env_connected": env_connected,
            "noenv_connected": noenv_connected,
            "answer": _answer(env_run)[:40],
        },
    )

    final = result_event(auth["events"]) or {}
    required_keys = ("total_cost_usd", "usage", "num_turns", "duration_ms")
    optional_keys = (
        "modelUsage",
        "duration_api_ms",
        "permission_denials",
        "session_id",
    )
    series = usage_series(auth["events"])
    add(
        "result_fields",
        all(key in final for key in required_keys),
        {
            "present": sorted(k for k in final),
            "missing_required": [k for k in required_keys if k not in final],
            "missing_optional": [k for k in optional_keys if k not in final],
            "series_total": series[-1]["total"] if series else None,
            "result_usage": final.get("usage"),
        },
    )

    denial = got("denial")
    leaked = any(
        not r["is_error"] and "bypass-probe" in r["text"]
        for r in tool_results(denial["events"]).values()
    )
    found = denials(denial["events"])
    add(
        "denial",
        denial["exit_code"] == 0 and not leaked and bool(found),
        {"leaked": leaked, "denials": found[:3], "answer": _answer(denial)[:40]},
    )

    appended = got("append_prompt")
    add(
        "append_prompt",
        appended["exit_code"] == 0 and PASSPHRASE in _answer(appended),
        {"answer": _answer(appended)[:40]},
    )

    return checks


def run_spike(
    *,
    root: Path,
    panther_repo: Path,
    plugin_dir: Path,
    claude_bin: str = "claude",
    model: str = "claude-opus-5",
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Prepare the scratch tree, make every call, evaluate, write the report.

    Returns:
        The report; ``report["go"]`` is True when every required check passed.
    """
    server_src = plugin_dir / "server" / "src"
    workspace = prepare_scratch(
        root=root,
        panther_repo=panther_repo,
        server_src=server_src,
        python=sys.executable,
    )
    invocations = build_invocations(
        root=root,
        panther_repo=panther_repo,
        plugin_dir=plugin_dir,
        workspace=workspace,
        claude_bin=claude_bin,
        model=model,
    )
    outcomes: dict[str, dict[str, Any]] = {}
    log: list[dict[str, Any]] = []
    for invocation in invocations:
        outcome = run_claude(invocation, timeout_s)
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
        (root / "spike" / f"{invocation.name}.jsonl").write_text(
            "\n".join(json.dumps(e, sort_keys=True) for e in outcome["events"]) + "\n"
        )
        if invocation.name == "auth" and outcome["exit_code"] != 0:
            break
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
