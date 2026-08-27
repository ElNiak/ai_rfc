"""Per-arm enforcement profiles: which surfaces a session may reach.

Enforcement is by removal (arm A has no Bash tool at all) or, for arms B and C,
by a ``PreToolUse`` guard confining Bash to the command families their
``allowed_tools`` declares. The allowlist alone does not confine a built-in on
CLI 2.1.247, which is why the guard exists; see :mod:`experiment.enforcement`.
A blocked call is counted by the audit as a bypass attempt.
The MCP server is mounted only in arm A, through a rendered config with
absolute paths, and ``--strict-mcp-config`` keeps every other server out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ExperimentError

ARMS = ("A", "B", "C")
READ_TOOLS = ("Read", "Edit", "Write", "Grep", "Glob")
RAW_SUBSTRATE = "Bash(python -m panther.plugins.services.testers.a_rfc*)"


@dataclass(frozen=True)
class ArmProfile:
    """One arm's surface: built-in tools, allowlist, and whether MCP mounts."""

    arm: str
    label: str
    tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    uses_mcp: bool


PROFILES: dict[str, ArmProfile] = {
    "A": ArmProfile(
        arm="A",
        label="class 1: structured-typed (arfc MCP tools)",
        tools=READ_TOOLS,
        allowed_tools=READ_TOOLS + ("mcp__arfc",),
        uses_mcp=True,
    ),
    "B": ArmProfile(
        arm="B",
        label="class 2: hybrid shell-via-tool (arfc CLI through Bash)",
        tools=READ_TOOLS + ("Bash",),
        allowed_tools=READ_TOOLS + ("Bash(arfc *)",),
        uses_mcp=False,
    ),
    "C": ArmProfile(
        arm="C",
        label="class 2: hybrid shell-via-tool (raw substrate through Bash)",
        tools=READ_TOOLS + ("Bash",),
        allowed_tools=READ_TOOLS + (RAW_SUBSTRATE, "Bash(git *)", "Bash(sqlite3 *)"),
        uses_mcp=False,
    ),
}


def profile(arm: str) -> ArmProfile:
    """Return the enforcement profile of ``arm``.

    Raises:
        ExperimentError: If ``arm`` is not one of :data:`ARMS`.
    """
    try:
        return PROFILES[arm]
    except KeyError:
        raise ExperimentError(
            f"unknown arm {arm!r}; arms are {', '.join(ARMS)}"
        ) from None


def constant_flags(
    *, model: str, effort: str, budget_usd: float, prompt_file: Path
) -> list[str]:
    """Flags identical across arms — the protocol's "one harness" constant."""
    return [
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--append-system-prompt-file",
        str(prompt_file),
        "--disable-slash-commands",
        "--setting-sources",
        "project",
        "--model",
        model,
        "--effort",
        effort,
        "--permission-mode",
        "dontAsk",
        "--max-budget-usd",
        f"{budget_usd:g}",
    ]


def arm_flags(
    arm_profile: ArmProfile,
    mcp_config_path: Path | None,
    guard_settings: Path | None = None,
) -> list[str]:
    """Flags that differ by arm: built-in tool set, allowlist, MCP mount, guard.

    Tool lists are passed comma-joined as one argument so a following flag
    can never be swallowed as a tool name. The allowlist is normative for MCP
    tools only; a built-in enabled by ``--tools`` is confined by the guard
    mounted through ``guard_settings`` (see :mod:`experiment.enforcement`).

    Raises:
        ExperimentError: If an MCP config is missing for arm A or given for
            an arm that must not mount one.
    """
    if arm_profile.uses_mcp and mcp_config_path is None:
        raise ExperimentError(
            f"arm {arm_profile.arm} mounts the MCP server; a config path is required"
        )
    if not arm_profile.uses_mcp and mcp_config_path is not None:
        raise ExperimentError(f"arm {arm_profile.arm} must not mount an MCP server")
    flags = [
        "--tools",
        ",".join(arm_profile.tools),
        "--allowedTools",
        ",".join(arm_profile.allowed_tools),
        "--strict-mcp-config",
    ]
    if mcp_config_path is not None:
        flags += ["--mcp-config", str(mcp_config_path)]
    if guard_settings is not None:
        flags += ["--settings", str(guard_settings)]
    return flags


def mcp_config(
    *, python: str, server_src: Path, panther_repo: Path, workspace: Path
) -> dict[str, Any]:
    """The rendered MCP config mounting the ``arfc`` server for one run."""
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(server_src)!r}); "
        "from ai_rfc_server.server import main; main()"
    )
    return {
        "mcpServers": {
            "arfc": {
                "command": python,
                "args": ["-c", bootstrap],
                "env": {
                    "PANTHER_REPO": str(panther_repo),
                    "ARFC_WORKSPACE": str(workspace),
                },
            }
        }
    }


def build_argv(
    *,
    claude_bin: str,
    prompt: str,
    arm_profile: ArmProfile,
    mcp_config_path: Path | None,
    model: str,
    effort: str,
    budget_usd: float,
    prompt_file: Path,
    guard_settings: Path | None = None,
) -> list[str]:
    """The complete ``claude -p`` argument vector for one run."""
    return [
        claude_bin,
        "-p",
        prompt,
        *constant_flags(
            model=model, effort=effort, budget_usd=budget_usd, prompt_file=prompt_file
        ),
        *arm_flags(arm_profile, mcp_config_path, guard_settings),
    ]
