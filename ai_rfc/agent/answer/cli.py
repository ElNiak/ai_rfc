"""``ai-rfc answer record``: ingest one answer from a saved transcript."""

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
        "Record what the author answered, anchored to the transcript it was said in."
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    record = verbs.add_parser(
        "record", help="Ingest one answer from a saved transcript."
    )
    record.add_argument("question_id", help="The register entry being answered.")
    record.add_argument(
        "--answer", required=True, help="The author's answer, in their words."
    )
    record.add_argument("--by", required=True, help="Who answered.")
    record.add_argument(
        "--transcript",
        required=True,
        help="Transcript filename under interviews/ (e.g. int-001.md); must "
        "already be saved.",
    )
    record.add_argument(
        "--quote",
        required=True,
        help="A verbatim span that must appear in the transcript — the evidence "
        "the answer actually happened.",
    )
    record.add_argument(
        "--exact-wording-confirmed",
        action="store_true",
        help="The author confirmed the exact claim wording; grants sign-off.",
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.answer`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc answer")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc answer {__version__}"
    )
    configure(parser)
    return parser


def _record(args: argparse.Namespace, ctx: Context) -> int:
    """Write the answer, its interview anchor and any sign-off it earns."""
    from ...server.core import questions

    emit(
        questions.record_answer(
            ctx,
            args.question_id,
            args.answer,
            args.by,
            args.transcript,
            args.quote,
            author_confirmed_exact_text=args.exact_wording_confirmed,
        )
    )
    return 0


def run(args: argparse.Namespace) -> int:
    """Perform the parsed verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 when the environment, an input or a guardrail refuses
        the operation.

    Raises:
        AssertionError: If ``args.verb`` names no branch. argparse admits only
            the verbs :func:`configure` declares, so this is unreachable by
            argv; it replaces the fallthrough that would otherwise run the last
            branch for a verb nobody wrote one for.
    """
    if args.verb == "record":
        return perform(partial(_record, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
