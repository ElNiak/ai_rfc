"""The AI+MCP vs AI+CLI experiment harness over the ai_rfc plugin.

Model-driven orchestration lives here, outside PANTHER's framework
boundary: preparing pristine workspaces, launching hermetic ``claude -p``
sessions per arm, auditing their transcripts and recomputing outcomes from
workspace state. Nothing here is imported by the plugin or the substrate.
"""

from __future__ import annotations

#: The model every spike and campaign launches against unless ``--model`` says
#: otherwise. It lives here rather than in each parser because the default is
#: part of the reproducibility record: a campaign freezes whatever it resolved
#: to, so copies of the string drifting apart would put two different models in
#: one comparison.
DEFAULT_MODEL = "claude-opus-5"

#: Reasoning-effort levels the harness accepts. Validated at parse time so a
#: typo costs an argparse error rather than a campaign-init round trip that
#: only fails after the parity suite has run.
EFFORTS = ("low", "medium", "high", "xhigh")


class ExperimentError(RuntimeError):
    """Raised when the harness cannot proceed as asked."""
