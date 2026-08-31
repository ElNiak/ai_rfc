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


def _redirects(text: str, index: int) -> bool:
    """Whether the ``&`` at ``index`` redirects rather than separates commands.

    Args:
        text: The command being scanned.
        index: Offset of the ``&``.

    Returns:
        True for the redirection forms ``2>&1`` and ``&>log``.
    """
    before = text[index - 1] if index else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return before == ">" or after == ">"


def command_groups(command: str) -> list[list[str]]:
    """Split a shell command into command groups, each a list of pipe stages.

    The scan is quote-aware: ``;``, ``|``, ``&&`` and ``||`` inside a quoted
    argument belong to that argument and separate nothing. An arm holding a SQL
    surface writes both routinely — ``SELECT a || b`` concatenates and
    ``SELECT 1; SELECT 2`` terminates — so splitting on them would refuse a
    command that runs one in-family program.

    Line continuations are joined first, so a command written across several
    lines stays one command. Redirections are not operators.

    Args:
        command: The raw ``tool_input.command`` string.

    Returns:
        One list of stripped, non-empty pipe stages per command group.

    Raises:
        ValueError: The command ends inside an unterminated quote, so what it
            would run cannot be read off it.
    """
    text = _CONTINUATION.sub(" ", command)
    groups: list[list[str]] = []
    stages: list[str] = []
    token: list[str] = []

    def end_stage() -> None:
        stage = "".join(token).strip()
        token.clear()
        if stage:
            stages.append(stage)

    def end_group() -> None:
        end_stage()
        if stages:
            groups.append(list(stages))
            stages.clear()

    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            # Only double quotes honour a backslash escape; inside single
            # quotes a backslash is an ordinary character.
            if char == "\\" and quote == '"' and index + 1 < len(text):
                token.append(char)
                token.append(text[index + 1])
                index += 2
                continue
            token.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
            token.append(char)
        elif char == "\\" and index + 1 < len(text):
            token.append(char)
            token.append(text[index + 1])
            index += 1
        elif text[index : index + 2] in ("||", "&&"):
            end_group()
            index += 1
        elif char in ";\n":
            end_group()
        elif char == "&" and not _redirects(text, index):
            end_group()
        elif char == "|":
            end_stage()
        else:
            token.append(char)
        index += 1

    if quote:
        raise ValueError(f"unterminated {quote} quote")
    end_group()
    return groups


def is_allowed(command: str, families: Sequence[str]) -> bool:
    """Whether every command in ``command`` falls inside ``families``.

    Fails closed on command substitution: a prefix check cannot see what
    ``$(...)`` or a backtick would run, so such a command is never allowed. It
    fails closed on an unterminated quote for the same reason.

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
    try:
        groups = command_groups(command)
    except ValueError:
        return False
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
