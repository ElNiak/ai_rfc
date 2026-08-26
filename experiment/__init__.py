"""The AI+MCP vs AI+CLI experiment harness over the ai_rfc plugin.

Model-driven orchestration lives here, outside PANTHER's framework
boundary: preparing pristine workspaces, launching hermetic ``claude -p``
sessions per arm, auditing their transcripts and recomputing outcomes from
workspace state. Nothing here is imported by the plugin or the substrate.
"""

from __future__ import annotations


class ExperimentError(RuntimeError):
    """Raised when the harness cannot proceed as asked."""
