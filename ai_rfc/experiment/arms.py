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
#: How arm C reaches the substrate. The audit classifier matches transcripts
#: against this exact string, so a second copy that drifted would silently
#: reclassify a legitimate call as an integrity violation rather than error.
RAW_PREFIX = "python -m ai_rfc"
RAW_SUBSTRATE = f"Bash({RAW_PREFIX}*)"
#: The per-run MCP config the runner writes and preflight reads.
MCP_FILE = "ai_rfc.json"


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
        label="class 1: structured-typed (ai_rfc MCP tools)",
        tools=READ_TOOLS,
        allowed_tools=READ_TOOLS + ("mcp__ai_rfc",),
        uses_mcp=True,
    ),
    "B": ArmProfile(
        arm="B",
        label="class 2: hybrid shell-via-tool (ai_rfc CLI through Bash)",
        tools=READ_TOOLS + ("Bash",),
        allowed_tools=READ_TOOLS + ("Bash(ai_rfc *)",),
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


def arm_profile(arm: str) -> ArmProfile:
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


def shared_flags(
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
    this_arm: ArmProfile,
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
    if this_arm.uses_mcp and mcp_config_path is None:
        raise ExperimentError(
            f"arm {this_arm.arm} mounts the MCP server; a config path is required"
        )
    if not this_arm.uses_mcp and mcp_config_path is not None:
        raise ExperimentError(f"arm {this_arm.arm} must not mount an MCP server")
    flags = [
        "--tools",
        ",".join(this_arm.tools),
        "--allowedTools",
        ",".join(this_arm.allowed_tools),
        "--strict-mcp-config",
    ]
    if mcp_config_path is not None:
        flags += ["--mcp-config", str(mcp_config_path)]
    if guard_settings is not None:
        flags += ["--settings", str(guard_settings)]
    return flags


def mcp_config(*, python: str, workspace: Path) -> dict[str, Any]:
    """The rendered MCP config mounting the ``ai_rfc`` server for one run.

    The server is a module of the installed package, so the config names the
    interpreter and the module and nothing else; there is no checkout to
    locate and no path to bootstrap.
    """
    return {
        "mcpServers": {
            "ai_rfc": {
                "command": python,
                "args": ["-m", "ai_rfc.server"],
                "env": {"AI_RFC_WORKSPACE": str(workspace)},
            }
        }
    }


def claude_argv(
    *,
    claude_bin: str,
    prompt: str,
    this_arm: ArmProfile,
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
        *shared_flags(
            model=model, effort=effort, budget_usd=budget_usd, prompt_file=prompt_file
        ),
        *arm_flags(this_arm, mcp_config_path, guard_settings),
    ]
