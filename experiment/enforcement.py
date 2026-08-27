"""Confine each arm's Bash tool to its declared command families.

Measured on Claude Code 2.1.247: ``--allowedTools`` does not constrain a
built-in tool that ``--tools`` has enabled, and permission deny rules cannot
express "only this family" — denying ``Bash`` and re-allowing one family blocks
the allowed command too. A ``PreToolUse`` hook can express it, but only through
the **exit-2 blocking path**; returning the documented
``hookSpecificOutput.permissionDecision = "deny"`` is silently ignored and the
command runs.

The families come from each arm's existing ``allowed_tools`` declaration, so
this module adds enforcement without adding a second source of truth.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from .arms import ArmProfile

SUBSTITUTION = ("$(", "`", "<(", ">(", "${")
_OPERATORS = re.compile(r"\|\||&&|[;|&\n]")
_BASH_ENTRY = re.compile(r"^Bash\((?P<family>.*?)\*?\)$")


def bash_families(arm_profile: ArmProfile) -> tuple[str, ...]:
    """The command prefixes an arm may run.

    Args:
        arm_profile: The arm whose ``allowed_tools`` declares its families.

    Returns:
        One prefix per ``Bash(...)`` entry, trailing ``*`` stripped, in
        declaration order. Empty when the arm has no Bash tool at all.
    """
    families = []
    for entry in arm_profile.allowed_tools:
        match = _BASH_ENTRY.match(entry)
        if match:
            families.append(match.group("family"))
    return tuple(families)


def command_segments(command: str) -> list[str]:
    """Split a shell command into the parts that each execute something.

    Args:
        command: The raw ``tool_input.command`` string.

    Returns:
        Stripped, non-empty segments separated by shell control operators.
    """
    return [part.strip() for part in _OPERATORS.split(command) if part.strip()]


def is_allowed(command: str, families: Sequence[str]) -> bool:
    """Whether every segment of ``command`` falls inside ``families``.

    Fails closed on command substitution: a prefix check cannot see what
    ``$(...)`` or a backtick would run, so such a command is never allowed.

    Args:
        command: The raw ``tool_input.command`` string.
        families: Allowed command prefixes, from :func:`bash_families`.

    Returns:
        True only when substitution is absent and every segment starts with
        one of the families.
    """
    if not families:
        return False
    if any(token in command for token in SUBSTITUTION):
        return False
    segments = command_segments(command)
    if not segments:
        return False
    return all(
        any(segment.startswith(family) for family in families) for segment in segments
    )


def render_settings(
    *, python: str, guard: Path, families: Sequence[str]
) -> dict[str, Any]:
    """The settings document mounting the guard for one arm.

    The shape is the nested settings form (``{"hooks": {"PreToolUse": [...]}}``),
    not the flat plugin form.

    Args:
        python: Interpreter that runs the guard.
        guard: Absolute path to ``guard.py``.
        families: The arm's allowed command prefixes.

    Returns:
        A document to write beside the campaign and pass via ``--settings``.
    """
    argv = " ".join([python, str(guard), *(f"{family!r}" for family in families)])
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": argv}]}
            ]
        }
    }
