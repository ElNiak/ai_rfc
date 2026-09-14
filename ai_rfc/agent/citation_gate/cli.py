"""``ai-rfc citation-gate``: the draft's revision map against its checkpoints.

The package is spelled ``citation_gate`` and the verb ``citation-gate``: a
package name is an identifier and a verb is what an operator types, and the
registry carries the second rather than deriving it from the first.
"""

from __future__ import annotations

import argparse
from functools import partial

from ... import __version__
from ...server.paths import Context
from .. import emit, perform


def configure(parser: argparse.ArgumentParser) -> None:
    """Add this command's arguments to ``parser``.

    Args:
        parser: Either the root's subparser for this command or the standalone
            parser :func:`build_standalone_parser` builds; both must carry the
            same arguments, so both are configured here.
    """
    parser.description = (
        "Check the draft's prose against the checkpoints its revisions froze. "
        "A linter by default; --strict makes a finding an exit code."
    )
    parser.add_argument(
        "--strict", action="store_true", help="Exit 3 when any finding is reported."
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.citation_gate`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc citation-gate")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc citation-gate {__version__}"
    )
    configure(parser)
    return parser


def _citation_gate(args: argparse.Namespace, ctx: Context) -> int:
    """Run the citation gate and return its own exit code."""
    from ...server.core import gates

    result = gates.citation_gate(ctx, strict=args.strict)
    emit(result)
    return int(result["exit_code"])


def run(args: argparse.Namespace) -> int:
    """Perform the command.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        The gate's own exit code, passed through rather than collapsed: 0
        clean, 1 when the environment or an artifact is refused, 3 when
        ``--strict`` met a finding.
    """
    return perform(partial(_citation_gate, args))


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
