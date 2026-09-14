"""``ai-rfc cluster get|next``: one cluster's evidence, and the next one to do."""

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
    parser.description = "Read the timeline's clusters, one at a time."
    verbs = parser.add_subparsers(dest="verb", required=True)

    get = verbs.add_parser("get", help="One cluster's evidence.")
    get.add_argument("cluster_id", help="Cluster id, e.g. c0049-pr-ba8ca432c304.")
    get.add_argument("--patch", action="store_true", help="Include a patch slice.")
    get.add_argument(
        "--patch-offset",
        type=int,
        default=0,
        help="First byte of the patch slice (default: %(default)s).",
    )
    get.add_argument(
        "--patch-limit",
        type=int,
        default=20000,
        help="Bytes of patch to return (default: %(default)s).",
    )

    verbs.add_parser("next", help="Next unprocessed cluster.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.cluster`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc cluster")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc cluster {__version__}"
    )
    configure(parser)
    return parser


def _get(args: argparse.Namespace, ctx: Context) -> int:
    """Print one cluster's view, evidence and optional patch slice."""
    from ...server.core import queries

    emit(
        queries.cluster_get(
            ctx,
            args.cluster_id,
            include_patch=args.patch,
            patch_offset=args.patch_offset,
            patch_limit=args.patch_limit,
        )
    )
    return 0


def _next(args: argparse.Namespace, ctx: Context) -> int:
    """Print the lowest-ordinal unfinished cluster, or ``null``."""
    from ...server.core import queries

    emit(queries.cluster_next(ctx))
    return 0


def run(args: argparse.Namespace) -> int:
    """Perform the parsed verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 when the environment or the cluster id is refused.

    Raises:
        AssertionError: If ``args.verb`` names no branch. argparse admits only
            the verbs :func:`configure` declares, so this is unreachable by
            argv; it replaces the fallthrough that would otherwise run the last
            branch for a verb nobody wrote one for.
    """
    if args.verb == "get":
        return perform(partial(_get, args))
    if args.verb == "next":
        return perform(partial(_next, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
