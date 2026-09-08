"""What every lifecycle verb does first.

Read the config, find the workspace it names, and refuse identity drift.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..config import ReconConfig, drift, load_config
from . import LifecycleError
from .workspace import Layout

CONFIG_ENV = "AI_RFC_CONFIG"


def report(message: str) -> None:
    """Diagnostics to stderr — the ``panther.*`` loggers swallow warnings."""
    print(message, file=sys.stderr)


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """The one argument every lifecycle verb shares."""
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"recon.yaml (default: ${CONFIG_ENV}).",
    )


def config_path_from(args: argparse.Namespace) -> Path:
    """``--config`` or ``$AI_RFC_CONFIG``; nothing is guessed.

    Args:
        args: The parsed arguments of any lifecycle verb.

    Returns:
        The configuration file to read.

    Raises:
        LifecycleError: If neither the flag nor the variable names one.
    """
    if args.config is not None:
        return args.config
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV])
    raise LifecycleError(f"no config: pass --config or set {CONFIG_ENV}")


def load_sealed(
    config_path: Path,
) -> tuple[ReconConfig, ReconConfig, Layout, list[str]]:
    """Load the config as given and as sealed, refusing identity drift.

    The layout comes from the config as given, never from the sealed copy: a
    campaign pristine is sealed with its own root written into ``workspace:``,
    so reading the layout back out of the seal would send every verb to the
    tree the pristine was copied from.

    Args:
        config_path: The file the operator passed.

    Returns:
        ``(given, sealed, layout, noted)`` — ``noted`` lists non-identity
        drift lines.

    Raises:
        LifecycleError: If the workspace was never initialised or an identity
            field drifted.
        ConfigError: If either file does not validate.
    """
    given = load_config(config_path)
    layout = Layout(given.workspace)
    if not layout.init_record.exists() or not layout.config.exists():
        raise LifecycleError(
            f"{layout.root} is not an initialised workspace; "
            f"run: ai-rfc init --config {config_path}"
        )
    sealed = load_config(layout.config)
    refused, noted = drift(sealed, given)
    if refused:
        raise LifecycleError(
            "config drift refused (re-run ai-rfc init into a new workspace to "
            "change these): " + "; ".join(refused)
        )
    return given, sealed, layout, noted
