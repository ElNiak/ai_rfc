"""``ai-rfc revision record|tag``: the revision map and its annotated tags."""

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
        "Record what each draft revision froze, and tag it once both strict "
        "gates accept it."
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    record = verbs.add_parser("record", help="Record a revision entry.")
    record.add_argument("tag", help="Revision tag, e.g. draft-<name>-01.")
    record.add_argument(
        "--cluster", required=True, help="The cluster this revision freezes."
    )
    normative = record.add_mutually_exclusive_group(required=True)
    normative.add_argument(
        "--normative",
        action="store_true",
        dest="normative_change",
        help="This revision changed what the draft requires.",
    )
    normative.add_argument(
        "--no-normative",
        action="store_false",
        dest="normative_change",
        help="Editorial only; its cited claim set must not change.",
    )
    record.add_argument("--note", required=True, help="What this revision did.")
    record.add_argument(
        "--kind",
        choices=("cluster", "consolidation"),
        default="cluster",
        help="A cluster round (default) or a consolidation.",
    )
    record.add_argument(
        "--checkpoint",
        default=None,
        help="A consolidation's checkpoint, workspace-relative (consolidations/NN).",
    )

    tag = verbs.add_parser(
        "tag",
        help="Create the annotated revision tag once both strict gates accept it.",
    )
    tag.add_argument("tag", help="Tag to create, e.g. draft-<name>-01.")
    tag.add_argument("-m", "--message", required=True, help="Annotation for the tag.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.revision`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc revision")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc revision {__version__}"
    )
    configure(parser)
    return parser


def _record(args: argparse.Namespace, ctx: Context) -> int:
    """Write one entry into the revision map."""
    from ...server.core import revisions

    emit(
        revisions.record_revision(
            ctx,
            args.tag,
            args.cluster,
            args.normative_change,
            args.note,
            args.kind,
            args.checkpoint,
        )
    )
    return 0


def _tag(args: argparse.Namespace, ctx: Context) -> int:
    """Create the annotated tag, and return the gate's own exit code."""
    from ...server.core import draft

    result = draft.tag_revision(ctx, args.tag, args.message)
    emit(result)
    return int(result["exit_code"])


def run(args: argparse.Namespace) -> int:
    """Perform the parsed verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success and 1 when an input or a guardrail refuses the operation;
        ``tag`` passes the gate's own code through, so 3 means the strict gates
        reported findings.

    Raises:
        AssertionError: If ``args.verb`` names no branch. argparse admits only
            the verbs :func:`configure` declares, so this is unreachable by
            argv; it replaces the fallthrough that would otherwise run the last
            branch for a verb nobody wrote one for.
    """
    if args.verb == "record":
        return perform(partial(_record, args))
    if args.verb == "tag":
        return perform(partial(_tag, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
