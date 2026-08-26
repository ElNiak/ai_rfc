"""The isolated Claude Code profile every experiment session runs under."""

from __future__ import annotations

from pathlib import Path

from .paths import profile_dir

_README = """This directory is a CLAUDE_CONFIG_DIR for the a_rfc experiment harness.
It holds no user settings, plugins or hooks on purpose. Log in once with:

    {login}

and never point an interactive session here.
"""


def login_command(root: Path) -> str:
    """The one-time login the user runs to authenticate the profile."""
    return f"CLAUDE_CONFIG_DIR={profile_dir(root)} claude auth login"


def init_profile(root: Path) -> Path:
    """Create the profile directory (idempotent) and its README.

    Args:
        root: The runs root.

    Returns:
        The profile directory.
    """
    profile = profile_dir(root)
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "README-arfc.txt").write_text(_README.format(login=login_command(root)))
    return profile
