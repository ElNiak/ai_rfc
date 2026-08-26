"""Where the harness keeps its state: a runs root outside every CLAUDE.md ancestry."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ROOT = "~/arfc-experiments"


def default_root() -> Path:
    """The runs root: ``ARFC_EXPERIMENTS_ROOT`` or ``~/arfc-experiments``."""
    return Path(os.environ.get("ARFC_EXPERIMENTS_ROOT", DEFAULT_ROOT)).expanduser()


def profile_dir(root: Path) -> Path:
    """The isolated Claude Code config directory under ``root``."""
    return root / "profile"
