"""Resolve the one environment handle everything here depends on.

``AI_RFC_WORKSPACE`` names one reconstruction workspace. It is required;
nothing guesses, because a tool quietly operating on the wrong workspace is
the kind of failure that looks like success. The substrate is an installed
package, so no checkout has to be located or placed on ``sys.path``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class EnvError(RuntimeError):
    """Raised when the environment contract is not met."""


@dataclass(frozen=True)
class Context:
    """The resolved handle every core operation receives."""

    workspace: Path

    @property
    def manifest(self) -> Path:
        """The workspace manifest."""
        return self.workspace / "manifest.yaml"

    @property
    def questions(self) -> Path:
        """The workspace question register."""
        return self.workspace / "questions.yaml"

    @property
    def revisions(self) -> Path:
        """The workspace revision map."""
        return self.workspace / "revisions.yaml"


def resolve_context() -> Context:
    """Read and validate the environment contract.

    Returns:
        The resolved context.

    Raises:
        EnvError: If ``AI_RFC_WORKSPACE`` is missing or does not name a
            directory.
    """
    workspace = os.environ.get("AI_RFC_WORKSPACE")
    if not workspace:
        raise EnvError(
            "AI_RFC_WORKSPACE must be set; refusing to guess which workspace "
            "to operate on"
        )
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        raise EnvError(f"AI_RFC_WORKSPACE={workspace_path} is not a directory")
    return Context(workspace=workspace_path)
