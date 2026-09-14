"""The single implementation of every plugin operation.

Both frontends (MCP tools and the ``ai_rfc`` CLI) call these functions and
nothing else, which is what makes the AI+MCP vs AI+CLI comparison an
experiment rather than an aspiration.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable


class CoreError(RuntimeError):
    """Raised when an operation cannot be performed as asked."""


class GuardrailError(CoreError):
    """Raised when an operation would break an evidence-honesty rule."""


def diagnostics(*messages: str) -> list[str]:
    """Render a substrate verb's diagnostics as its reader has always seen them.

    The substrate CLIs write one ``print`` per diagnostic. The core used to run
    them as subprocesses and read the pipe back with ``str.splitlines()``,
    dropping the blank lines, so the list it returned carried one element per
    *line* and not one per message. Calling the same code in process removes
    the pipe but not that contract: every caller, and every session shown the
    ``stderr`` key, was written against the split shape.

    The split is reproduced rather than repaired deliberately. A cluster id an
    agent wrote into ``revisions.yaml`` reaches a finding unescaped, so a line
    break in it — ``\\n``, or the U+2028 that ``str.splitlines`` also breaks on
    and YAML emits as the innocuous-looking ``\\L`` — forges an element that
    reads as the CLI's own ``note:`` verdict. Joining the lines here would hide
    that forgery without fixing it, and would change the shape this move
    promised to preserve; the fix belongs where the message is composed.

    Args:
        *messages: The diagnostics, in the order the CLI prints them.

    Returns:
        One element per non-blank line, across all the messages in order.
    """
    return [
        line for message in messages for line in message.splitlines() if line.strip()
    ]


def reported(verb: Callable[[], tuple[int, list[str]]]) -> tuple[int, list[str]]:
    """Give an in-process substrate verb the boundary its subprocess had.

    Each verb catches the exception family its own CLI branch names, and only
    that family, because "this diagnostic is mine" is a property of the raise
    site rather than of the call. Everything else used to be the child
    process's to die of: the traceback reached its stderr and the core read
    back exit 1. In process the same exception would leave the core, cross the
    MCP server and end the tool call instead, so the boundary is rebuilt here.

    Args:
        verb: The verb body, already carrying its own narrow family.

    Returns:
        What ``verb`` returned, or exit 1 and the formatted traceback — the
        same pair the subprocess produced for an unexpected failure.
    """
    try:
        return verb()
    except Exception:
        return 1, diagnostics(traceback.format_exc())
