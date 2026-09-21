"""``ai-rfc claim upsert|check|record-status``: the manifest's claims.

``check`` is what the parity CLI called ``claim-adjudicate``. The rename is
§6's, and it is the substrate's own vocabulary: what the verb reports is the
gap between a claim's stored status and the status its evidence supports, which
is the check ``ai-rfc check`` performs over anchors.
"""

from __future__ import annotations

import argparse
import json
from functools import partial
from typing import TYPE_CHECKING, Any

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
    parser.description = "Add, update and adjudicate the manifest's requirement claims."
    verbs = parser.add_subparsers(dest="verb", required=True)

    upsert = verbs.add_parser(
        "upsert", help="Add or update a claim (status is never accepted)."
    )
    upsert.add_argument("claim_id", help="Claim id, e.g. 'mark:alg.1'.")
    upsert.add_argument("--text", help="The requirement, as one normative sentence.")
    upsert.add_argument("--section", help="Draft section number, e.g. '3.1'.")
    upsert.add_argument("--level", help="RFC 2119 keyword: MUST, SHOULD or MAY.")
    upsert.add_argument("--layer", help="Architectural layer the claim belongs to.")
    upsert.add_argument(
        "--field",
        action="append",
        default=[],
        help="Extra field as key=value (req_class, intent, question-id, testable).",
    )
    upsert.add_argument(
        "--anchor",
        action="append",
        default=[],
        help=(
            "Anchor as JSON, e.g. "
            '\'{"evidence_class": "code", "locator": "src/a.py", '
            '"commit": "<sha>", "line": 42}\' or a decision record, '
            '\'{"evidence_class": "adr", "locator": "<sha>"}\'; repeatable, '
            "replaces the claim's anchors when given."
        ),
    )

    verbs.add_parser("check", help="Stored vs supported status for every claim.")

    record = verbs.add_parser(
        "record-status",
        help="Set stored statuses to exactly what adjudication supports.",
    )
    record.add_argument("claim_ids", nargs="*", help="Default: every claim.")


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.agent.claim`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc claim")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc claim {__version__}"
    )
    configure(parser)
    return parser


def _parse_fields(pairs: list[str]) -> dict[str, Any]:
    """Read ``--field key=value`` pairs into the mapping the core takes.

    Args:
        pairs: The raw ``key=value`` strings, in the order they were given.

    Returns:
        The fields, later keys winning.

    Raises:
        CoreError: If a pair carries no ``=``. The offending pair is
            interpolated through ``repr``, which is a total encoder over
            ``str`` rather than a filter: a newline in it arrives as ``\\n``
            and cannot forge a second diagnostic line.
    """
    from ...server.core import CoreError

    fields: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CoreError(f"--field expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        fields[key] = value
    return fields


def _upsert(args: argparse.Namespace, ctx: Context) -> int:
    """Write one claim's fields and anchors into the manifest."""
    from ...server.core import claims

    fields = _parse_fields(args.field)
    for name in ("text", "section", "level", "layer"):
        value = getattr(args, name)
        if value is not None:
            fields[name] = value
    if args.anchor:
        fields["anchors"] = [json.loads(anchor) for anchor in args.anchor]
    emit(claims.upsert_claim(ctx, args.claim_id, fields))
    return 0


def _check(args: argparse.Namespace, ctx: Context) -> int:
    """Print each claim's stored status beside the one its evidence supports."""
    from ...server.core import claims

    emit(claims.adjudicate_preview(ctx))
    return 0


def _record_status(args: argparse.Namespace, ctx: Context) -> int:
    """Write the supported statuses back into the manifest."""
    from ...server.core import claims

    emit(claims.record_statuses(ctx, args.claim_ids or None))
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
    if args.verb == "upsert":
        return perform(partial(_upsert, args))
    if args.verb == "check":
        return perform(partial(_check, args))
    if args.verb == "record-status":
        return perform(partial(_record_status, args))
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
