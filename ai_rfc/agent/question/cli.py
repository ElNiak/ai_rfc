"""``ai-rfc question draft|export``: the open-question register."""

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
        "Draft the questions only the author can answer, and export them as "
        "one bundle to send."
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    draft = verbs.add_parser("draft", help="Draft an open question.")
    draft.add_argument("question", help="The question; quote the claim verbatim.")
    draft.add_argument(
        "--claim",
        action="append",
        required=True,
        dest="claims",
        help="A claim this question would unblock; repeatable, must exist.",
    )
    draft.add_argument(
        "--id", default=None, help="Explicit id; default takes the next free q-NNN."
    )

    verbs.add_parser("export", help="Markdown bundle of open questions.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.question`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc question")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc question {__version__}"
    )
    configure(parser)
    return parser


def _draft(args: argparse.Namespace, ctx: Context) -> int:
    """Register one question and link it to the claims it would unblock."""
    from ...server.core import questions

    emit(questions.draft_question(ctx, args.question, args.claims, question_id=args.id))
    return 0


def _export(args: argparse.Namespace, ctx: Context) -> int:
    """Print the open-question bundle.

    ``end=""`` rather than a bare ``print()``: the bundle is the core's return
    value and a trailing newline appended here is a byte the tool arm never
    emits. ``3cdeb29`` fixed exactly that at the site this was lifted from, and
    ``test_question_export_parity`` in ``tests/server/test_parity.py`` pins
    this stdout against the tool's own return value, byte for byte.
    """
    from ...server.core import questions

    print(questions.export_open(ctx), end="")
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
    if args.verb == "draft":
        return perform(partial(_draft, args))
    if args.verb == "export":
        return perform(partial(_export, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
