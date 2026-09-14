"""``ai-rfc structure upsert``: declare or replace one structure."""

from __future__ import annotations

import argparse
import json
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
    parser.description = "Declare the data structures the draft renders as blocks."
    verbs = parser.add_subparsers(dest="verb", required=True)

    upsert = verbs.add_parser("upsert", help="Declare or replace one structure.")
    upsert.add_argument(
        "structure_id", help="Structure id, e.g. 'header'; replaced when it exists."
    )
    upsert.add_argument("--json", required=True, help="The structure body as JSON.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.structure`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc structure")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc structure {__version__}"
    )
    configure(parser)
    return parser


def _upsert(args: argparse.Namespace, ctx: Context) -> int:
    """Decode the body and write the structure into the manifest.

    The decode runs inside the guard :func:`ai_rfc.agent.perform` opened, so a
    ``--json`` that is not JSON is reported as an error and exit 1 rather than
    a traceback.
    """
    from ...server.core import structures

    emit(structures.upsert_structure(ctx, args.structure_id, json.loads(args.json)))
    return 0


def run(args: argparse.Namespace) -> int:
    """Perform the parsed verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 when the environment, the JSON body or a guardrail
        refuses the operation.

    Raises:
        AssertionError: If ``args.verb`` names no branch. argparse admits only
            the verbs :func:`configure` declares, so this is unreachable by
            argv; it replaces the fallthrough that would otherwise run the last
            branch for a verb nobody wrote one for.
    """
    if args.verb == "upsert":
        return perform(partial(_upsert, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
