"""``ai-rfc corpus query``: one SELECT over the commit index."""

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
    parser.description = "Read the commit corpus this workspace was built from."
    verbs = parser.add_subparsers(dest="verb", required=True)

    query = verbs.add_parser("query", help="One SELECT over the index.")
    query.add_argument("sql", help="A single SELECT; at most 200 rows come back.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.corpus`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = Parser(prog="ai-rfc corpus")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc corpus {__version__}"
    )
    configure(parser)
    return parser


def _query(args: argparse.Namespace, ctx: Context) -> int:
    """Run the SELECT and print its rows."""
    from ...server.core import queries

    emit(queries.corpus_query(ctx, args.sql))
    return 0


def run(args: argparse.Namespace) -> int:
    """Perform the parsed verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 when the environment or the query is refused.

    Raises:
        AssertionError: If ``args.verb`` names no branch. argparse admits only
            the verbs :func:`configure` declares, so this is unreachable by
            argv; it replaces the fallthrough that would otherwise run the last
            branch for a verb nobody wrote one for.
    """
    if args.verb == "query":
        return perform(partial(_query, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
