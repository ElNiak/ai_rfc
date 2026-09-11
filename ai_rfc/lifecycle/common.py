"""Plumbing the lifecycle verbs share: the config argument, and the config pair.

Every verb reads the config the operator passed and the copy ``init`` sealed,
and compares them. What they do about a refused identity field is where they
part: ``run`` refuses to touch a workspace whose identity moved, while
``status`` and ``verify`` exist precisely to report that it did. So the load is
:func:`load_pair` and the refusal is :func:`load_sealed` on top of it, rather
than one function with a flag.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..config import ReconConfig, drift, load_config
from ..driver import printable
from . import LifecycleError
from .workspace import Layout

CONFIG_ENV = "AI_RFC_CONFIG"


def report(message: str) -> None:
    """Diagnostics to stderr, as one printable line.

    The ``panther.*`` loggers swallow warnings, so these go straight to the
    stream the operator is watching.

    **Escaped here rather than at each of the twenty-six call sites**, for the
    reason :func:`ai_rfc.driver.sweep.report` gives for the other stderr
    boundary: it is a boundary rather than a rule each caller remembers. Every
    lifecycle verb interpolates operator- or agent-controlled values into
    these lines — a config path, a cluster id, a ``--until`` bound, the text
    of a caught error — and a value carrying a newline forges a second line
    beneath the first. That matters most where the real artifact is itself a
    line an operator copies: a forged ``resume: ai-rfc …`` is a fabricated
    instruction, which is exactly what a ``--until cluster:<id>`` refusal
    produced before this became a boundary.

    No caller loses anything: no lifecycle diagnostic is deliberately
    multi-line, and each is one record by construction.

    Args:
        message: The line to print.
    """
    print(printable(message), file=sys.stderr)


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


def load_pair(
    config_path: Path,
) -> tuple[ReconConfig, ReconConfig, Layout, list[str], list[str]]:
    """Load the config as given and as sealed, and compare them.

    The layout comes from the config as given, never from the sealed copy: a
    campaign pristine is sealed with its own root written into ``workspace:``,
    so reading the layout back out of the seal would send every verb to the
    tree the pristine was copied from.

    Args:
        config_path: The file the operator passed.

    Returns:
        ``(given, sealed, layout, refused, noted)`` — ``refused`` lists the
        identity fields that moved (D57) and ``noted`` every other change,
        both as ``path: old -> new`` lines. Judging them is the caller's.

    Raises:
        LifecycleError: If the workspace was never initialised.
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
    return given, sealed, layout, refused, noted


def load_sealed(
    config_path: Path,
) -> tuple[ReconConfig, ReconConfig, Layout, list[str]]:
    """Load the config pair as :func:`load_pair` does, refusing identity drift.

    For the verbs that act on a workspace rather than report on one: a
    reconstruction whose pin, window or draft name moved is a different
    reconstruction, so there is nothing safe to perform against it.

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
    given, sealed, layout, refused, noted = load_pair(config_path)
    if refused:
        raise LifecycleError(
            "config drift refused (re-run ai-rfc init into a new workspace to "
            "change these): " + "; ".join(refused)
        )
    return given, sealed, layout, noted
