"""``ai-rfc init --config recon.yaml``: build the workspace a reconstruction runs in."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from ... import __version__
from ...config import ConfigError, ReconConfig, dump_config, load_config
from .. import LifecycleError
from ..workspace import (
    TEMPLATE_COMMIT,
    TEMPLATE_URL,
    Layout,
    Toolchain,
    acquire,
    require_toolchain,
    scaffold,
    seal_references,
    write_digest,
    write_registers,
)

CONFIG_ENV = "AI_RFC_CONFIG"


def _report(message: str) -> None:
    """Diagnostics to stderr — the ``panther.*`` loggers swallow warnings."""
    print(message, file=sys.stderr)


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


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """The one argument every lifecycle verb shares."""
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"recon.yaml (default: ${CONFIG_ENV}).",
    )


def initialise(
    config: ReconConfig,
    *,
    config_path: Path,
    dest: Path | None = None,
    template: str = TEMPLATE_URL,
    template_commit: str = TEMPLATE_COMMIT,
) -> Path:
    """Create and fill the workspace.

    Args:
        config: The validated configuration.
        config_path: Where it was read from; its digest is recorded.
        dest: Workspace root overriding ``config.workspace`` (campaign pristines).
        template: Internet-Draft template clone source.
        template_commit: The commit the draft scaffold is pinned to.

    Returns:
        The workspace root.

    Raises:
        LifecycleError: If the workspace exists, references are declared without a
            toolchain, or acquisition fails.
    """
    layout = Layout(dest or config.workspace)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise LifecycleError(f"{layout.root} exists; a workspace is initialised once")
    toolchain = require_toolchain(config)
    # Only a root this call brings into being may be removed on failure. An
    # operator may point `init` at a directory that is already theirs — that is
    # what the refusal above is for — and cleaning up must never reach it.
    created = not layout.root.exists()
    layout.root.mkdir(parents=True, exist_ok=True)
    try:
        return _fill(
            config,
            layout,
            config_path=config_path,
            dest=dest,
            toolchain=toolchain,
            template=template,
            template_commit=template_commit,
        )
    # BaseException, not Exception: a Ctrl-C landing mid-clone leaves exactly
    # the half-built tree the next attempt would refuse as already initialised.
    except BaseException:
        if created:
            shutil.rmtree(layout.root, ignore_errors=True)
        raise


def _fill(
    config: ReconConfig,
    layout: Layout,
    *,
    config_path: Path,
    dest: Path | None,
    toolchain: Toolchain | None,
    template: str,
    template_commit: str,
) -> Path:
    """Acquire, scaffold and seal into a workspace root that already exists."""
    acquired = acquire(config, layout)
    draft_head = scaffold(
        config, layout, template=template, template_commit=template_commit
    )
    write_registers(config, layout)
    refcache_sha256, template_home = seal_references(config, layout, toolchain)
    # A campaign pristine is not at the path the config's own `workspace:`
    # names, so sealing the file as written would leave every pristine holding
    # a config that points at a different tree.
    sealed = (
        config if dest is None else dataclasses.replace(config, workspace=layout.root)
    )
    layout.config.write_text(dump_config(sealed))
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": config.name,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "sealed_config_sha256": hashlib.sha256(layout.config.read_bytes()).hexdigest(),
        "source": config.source.repo,
        "pin": config.source.pin,
        "resolved_pin": acquired.resolved_sha,
        "forge_snapshot": acquired.forge_snapshot,
        "window": list(config.window) if config.window else None,
        "draft_head": draft_head,
        "template": template,
        "template_commit": template_commit,
        "references": list(config.references),
        "refcache_sha256": refcache_sha256,
        "toolchain": str(config.toolchain) if config.toolchain else None,
        "toolchain_sha256": (
            hashlib.sha256(toolchain.path.read_bytes()).hexdigest()
            if toolchain is not None
            else None
        ),
        "template_home": template_home,
        "ai_rfc_version": __version__,
    }
    layout.init_record.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    write_digest(layout.root)
    return layout.root


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc init``."""
    parser.description = (
        "Create the workspace: clone at the pin, fetch the forge, scaffold the "
        "draft, seal the config."
    )
    add_config_argument(parser)
    parser.add_argument(
        "--template",
        default=TEMPLATE_URL,
        help="Draft template repository (default: %(default)s).",
    )
    parser.add_argument(
        "--template-commit",
        default=TEMPLATE_COMMIT,
        help="Template commit to pin (default: %(default)s).",
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.init`` uses (its ``cli`` module).

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc init")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc init {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Perform ``init``.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 on any refusal.
    """
    try:
        config_path = config_path_from(args)
        config = load_config(config_path)
        root = initialise(
            config,
            config_path=config_path,
            template=args.template,
            template_commit=args.template_commit,
        )
    except (LifecycleError, ConfigError, OSError) as error:
        _report(f"error: {error}")
        return 1
    print(f"workspace: {root}")
    print(f"next: ai-rfc run --config {config_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.init``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
