"""The isolated Claude Code profile every experiment session runs under."""

from __future__ import annotations

import os
from pathlib import Path

from .paths import profile_dir

_README = """This directory is a CLAUDE_CONFIG_DIR for the ai_rfc experiment harness.
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
    (profile / "README-ai_rfc.txt").write_text(
        _README.format(login=login_command(root))
    )
    return profile


def profile_env(profile: Path) -> dict[str, str]:
    """The complete environment of a one-shot ``claude -p`` on ``profile``.

    Args:
        profile: The ``CLAUDE_CONFIG_DIR`` the call authenticates through.

    Returns:
        Five variables and nothing else inherited, so no credential the shell
        holds can reach the call.
    """
    # Measured on Claude Code 2.1.247 / macOS: drop USER and the CLI cannot
    # reach its stored credentials, answering "Not logged in" however valid
    # the profile. Spike S0 failed on exactly this before it was added.
    return {
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "CLAUDE_CONFIG_DIR": str(profile),
    }
