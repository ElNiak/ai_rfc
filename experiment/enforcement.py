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
#: Programs an in-family command may pipe into. They only read and trim their
#: input, so they cannot reach a surface the arm does not already have.
PAGERS = ("head", "tail", "wc", "cut")
#: A backslash-newline continues one command; it does not start a second.
_CONTINUATION = re.compile(r"\\\r?\n")
#: Operators that separate whole commands. ``&`` is one of them only when it is
#: not part of a redirection: ``2>&1`` and ``&>log`` redirect, they do not run
#: anything. ``|`` is absent on purpose — pipes are handled per group below.
_GROUP_OPERATORS = re.compile(r"\|\||&&|(?<!>)&(?!>)|[;\n]")
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


def command_groups(command: str) -> list[list[str]]:
    """Split a shell command into command groups, each a list of pipe stages.

    Line continuations are joined first, so a command written across several
    lines stays one command. Redirections are not operators.

    Args:
        command: The raw ``tool_input.command`` string.

    Returns:
        One list of stripped, non-empty pipe stages per command group.
    """
    joined = _CONTINUATION.sub(" ", command)
    groups = []
    for part in _GROUP_OPERATORS.split(joined):
        stages = [stage.strip() for stage in part.split("|") if stage.strip()]
        if stages:
            groups.append(stages)
    return groups


def is_allowed(command: str, families: Sequence[str]) -> bool:
    """Whether every command in ``command`` falls inside ``families``.

    Fails closed on command substitution: a prefix check cannot see what
    ``$(...)`` or a backtick would run, so such a command is never allowed.

    Each command group must *begin* with an in-family command. A group may
    then pipe into :data:`PAGERS` and nothing else, so an arm can page long
    output without gaining a way to run something it may not.

    Args:
        command: The raw ``tool_input.command`` string.
        families: Allowed command prefixes, from :func:`bash_families`.

    Returns:
        True only when substitution is absent, every group starts in family,
        and every later pipe stage is a permitted pager.
    """
    if not families:
        return False
    if any(token in command for token in SUBSTITUTION):
        return False
    groups = command_groups(command)
    if not groups:
        return False
    for stages in groups:
        if not any(stages[0].startswith(family) for family in families):
            return False
        if any(stage.split()[0] not in PAGERS for stage in stages[1:]):
            return False
    return True


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
