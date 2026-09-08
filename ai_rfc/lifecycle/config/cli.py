"""``ai-rfc config example|reference``."""

from __future__ import annotations

import argparse

from ... import __version__
from ...config import example, reference_markdown


def configure(parser: argparse.ArgumentParser) -> None:
    """Add the ``example`` and ``reference`` verbs.

    Args:
        parser: Either the root's subparser for this command or the standalone
            parser :func:`build_standalone_parser` builds; both must carry the
            same arguments, so both are configured here.
    """
    parser.description = (
        "Print a starter recon.yaml, or the reference for the field table it "
        "is generated from. Both are rendered from that one table, so neither "
        "can drift from what the loader accepts."
    )
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser(
        "example", help="A starter recon.yaml with every field documented."
    )
    verbs.add_parser("reference", help="The field table as Markdown.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.lifecycle.config`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc config")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc config {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Print the requested text.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0; both renderings are pure functions of the field table.
    """
    print(example() if args.verb == "example" else reference_markdown(), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.config``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
