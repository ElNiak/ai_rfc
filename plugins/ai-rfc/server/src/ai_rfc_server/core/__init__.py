"""The single implementation of every plugin operation.

Both frontends (MCP tools and the ``arfc`` CLI) call these functions and
nothing else, which is what makes the AI+MCP vs AI+CLI comparison an
experiment rather than an aspiration.
"""

from __future__ import annotations


class CoreError(RuntimeError):
    """Raised when an operation cannot be performed as asked."""


class GuardrailError(CoreError):
    """Raised when an operation would break an evidence-honesty rule."""
