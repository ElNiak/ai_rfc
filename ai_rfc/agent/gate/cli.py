"""``ai-rfc gate``: the manifest gate (linter by default, strict on request)."""

from __future__ import annotations

import argparse
from functools import partial
from typing import TYPE_CHECKING

from ... import __version__
from ...parser import Parser
from .. import emit, perform

# Annotation-only, and deliberately not at module scope: ``build_parser``
# imports every registered module to call its ``configure``, so a runtime
# import of the core here would be paid by ``ai-rfc --help`` too. The
# runtime imports live in :func:`ai_rfc.agent.perform` and in each verb.
if TYPE_CHECKING:
    from ...server.paths import Context


def configure(parser: argparse.ArgumentParser) -> None:
    """Add this command's arguments to ``parser``.

    Args:
        parser: Either the root's subparser for this command or the standalone
            parser :func:`build_standalone_parser` builds; both must carry the
            same arguments, so both are configured here.
    """
    parser.description = (
        "Weigh every claim against the evidence its anchors point at. A linter "
        "by default; --strict makes a finding an exit code."
    )
    parser.add_argument(
        "--strict", action="store_true", help="Exit 3 when any finding is reported."
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.gate`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = Parser(prog="ai-rfc gate")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc gate {__version__}"
    )
    configure(parser)
    return parser


def _gate(args: argparse.Namespace, ctx: Context) -> int:
    """Run the manifest gate and return its own exit code."""
    from ...server.core import gates

    result = gates.manifest_gate(ctx, strict=args.strict)
    emit(result)
    return int(result["exit_code"])


def run(args: argparse.Namespace) -> int:
    """Perform the command.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        The gate's own exit code, passed through rather than collapsed: 0
        clean, 1 when the environment or the manifest is refused, 3 when
        ``--strict`` met a finding.
    """
    return perform(partial(_gate, args))


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
