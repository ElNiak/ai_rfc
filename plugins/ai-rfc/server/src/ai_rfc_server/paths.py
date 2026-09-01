"""Resolve the two environment handles everything here depends on.

``PANTHER_REPO`` names a PANTHER checkout (the deterministic substrate) and
``AI_RFC_WORKSPACE`` one reconstruction workspace. Both are required; nothing
guesses, because a tool quietly operating on the wrong workspace is the
kind of failure that looks like success.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


class EnvError(RuntimeError):
    """Raised when the environment contract is not met."""


@dataclass(frozen=True)
class Context:
    """The resolved handles every core operation receives."""

    panther_repo: Path
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
        The resolved context; ``PANTHER_REPO`` is placed at the front of
        ``sys.path`` so the substrate imports as a library.

    Raises:
        EnvError: If a variable is missing or does not name the directory
            it must.
    """
    repo = os.environ.get("PANTHER_REPO")
    workspace = os.environ.get("AI_RFC_WORKSPACE")
    if not repo or not workspace:
        raise EnvError(
            "PANTHER_REPO and AI_RFC_WORKSPACE must both be set; refusing to "
            "guess which checkout or workspace to operate on"
        )
    repo_path = Path(repo).resolve()
    workspace_path = Path(workspace).resolve()
    if not (repo_path / "panther" / "plugins").is_dir():
        raise EnvError(f"PANTHER_REPO={repo_path} is not a PANTHER checkout")
    if not workspace_path.is_dir():
        raise EnvError(f"AI_RFC_WORKSPACE={workspace_path} is not a directory")
    if str(repo_path) not in sys.path:
        sys.path.insert(0, str(repo_path))
    return Context(panther_repo=repo_path, workspace=workspace_path)
