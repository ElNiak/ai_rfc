"""``ai-rfc checkpoint``: freeze the manifest against one cluster.

One verb rather than a group, because the operation has no siblings: a
checkpoint is written against a cluster or, with ``--consolidation``, against a
run of them, and both are this one command.
"""

from __future__ import annotations

import argparse
from functools import partial
from typing import TYPE_CHECKING

from ... import __version__
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
        "Freeze the manifest against one cluster. Checkpoints are write-once: "
        "what a revision cited stays readable after the manifest moves on."
    )
    parser.add_argument(
        "cluster_id", help="Cluster to freeze against; checkpoints are write-once."
    )
    parser.add_argument(
        "--consolidation",
        type=int,
        default=None,
        help="Write consolidation NN under consolidations/ instead of a cluster "
        "checkpoint.",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="The cluster checkpoint the consolidation follows, workspace-relative; "
        "required with --consolidation.",
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.checkpoint`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc checkpoint")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc checkpoint {__version__}"
    )
    configure(parser)
    return parser


def _checkpoint(args: argparse.Namespace, ctx: Context) -> int:
    """Write the checkpoint and return the freeze's own exit code."""
    from ...server.core import gates

    result = gates.write_checkpoint(ctx, args.cluster_id, args.consolidation, args.base)
    emit(result)
    return int(result["exit_code"])


def run(args: argparse.Namespace) -> int:
    """Perform the command.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        The freeze's own exit code, passed through rather than collapsed: 0
        clean, 1 when the environment or an input is refused, 3 when the freeze
        reported findings.
    """
    return perform(partial(_checkpoint, args))


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
