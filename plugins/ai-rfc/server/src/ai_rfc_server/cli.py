"""The ``arfc`` CLI — the AI+CLI arm's frontend over the shared core.

Every verb maps 1:1 onto a core function; results go to stdout as JSON,
diagnostics to stderr, and gate exit codes pass through untouched (0 clean,
1 unusable inputs, 2 strict findings).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .core import CoreError
from .paths import EnvError, resolve_context


def _report(message: str) -> None:
    print(message, file=sys.stderr)


def _emit(payload: Any) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def _parse_fields(pairs: list[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CoreError(f"--field expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        fields[key] = value
    return fields


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arfc",
        description=(
            "Drive a reconstruction workspace (ARFC_WORKSPACE) against a "
            "PANTHER checkout (PANTHER_REPO)."
        ),
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    verbs.add_parser("status", help="Composite workspace status.")

    query = verbs.add_parser("corpus-query", help="One SELECT over the index.")
    query.add_argument("sql")

    get = verbs.add_parser("cluster-get", help="One cluster's evidence.")
    get.add_argument("cluster_id")
    get.add_argument("--patch", action="store_true", help="Include a patch slice.")
    get.add_argument("--patch-offset", type=int, default=0)
    get.add_argument("--patch-limit", type=int, default=20000)

    verbs.add_parser("cluster-next", help="Next unprocessed cluster.")

    upsert = verbs.add_parser(
        "claim-upsert", help="Add or update a claim (status is never accepted)."
    )
    upsert.add_argument("claim_id")
    upsert.add_argument("--text")
    upsert.add_argument("--section")
    upsert.add_argument("--level")
    upsert.add_argument("--layer")
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
            '"commit": "<sha>", "line": 42}\'; repeatable, replaces the '
            "claim's anchors when given."
        ),
    )

    verbs.add_parser(
        "claim-adjudicate", help="Stored vs supported status for every claim."
    )

    record = verbs.add_parser(
        "claim-record-status",
        help="Set stored statuses to exactly what adjudication supports.",
    )
    record.add_argument("claim_ids", nargs="*", help="Default: every claim.")

    question = verbs.add_parser("question-draft", help="Draft an open question.")
    question.add_argument("question")
    question.add_argument("--claim", action="append", required=True, dest="claims")
    question.add_argument("--id", default=None)

    verbs.add_parser("question-export", help="Markdown bundle of open questions.")

    answer = verbs.add_parser(
        "answer-record", help="Ingest one answer from a saved transcript."
    )
    answer.add_argument("question_id")
    answer.add_argument("--answer", required=True)
    answer.add_argument("--by", required=True)
    answer.add_argument("--transcript", required=True)
    answer.add_argument("--quote", required=True)
    answer.add_argument(
        "--exact-wording-confirmed",
        action="store_true",
        help="The author confirmed the exact claim wording; grants sign-off.",
    )

    revision = verbs.add_parser("revision-record", help="Record a revision entry.")
    revision.add_argument("tag")
    revision.add_argument("--cluster", required=True)
    normative = revision.add_mutually_exclusive_group(required=True)
    normative.add_argument(
        "--normative", action="store_true", dest="normative_change"
    )
    normative.add_argument(
        "--no-normative", action="store_false", dest="normative_change"
    )
    revision.add_argument("--note", required=True)

    checkpoint = verbs.add_parser(
        "checkpoint", help="Freeze the manifest against one cluster."
    )
    checkpoint.add_argument("cluster_id")

    gate = verbs.add_parser("gate", help="Manifest gate (linter by default).")
    gate.add_argument("--strict", action="store_true")

    citation = verbs.add_parser("citation-gate", help="Draft citation gate.")
    citation.add_argument("--strict", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one verb against the workspace named by the environment.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        0 on success, 1 when inputs or guardrails refuse the operation, and
        the gate verbs pass their own exit codes through (2 = strict
        findings).
    """
    args = _parser().parse_args(argv)

    try:
        ctx = resolve_context()
    except EnvError as error:
        _report(f"error: {error}")
        return 1

    from .core import claims, gates, queries, questions, revisions

    try:
        if args.verb == "status":
            _emit(queries.status(ctx))
        elif args.verb == "corpus-query":
            _emit(queries.corpus_query(ctx, args.sql))
        elif args.verb == "cluster-get":
            _emit(
                queries.cluster_get(
                    ctx,
                    args.cluster_id,
                    include_patch=args.patch,
                    patch_offset=args.patch_offset,
                    patch_limit=args.patch_limit,
                )
            )
        elif args.verb == "cluster-next":
            _emit(queries.cluster_next(ctx))
        elif args.verb == "claim-upsert":
            fields = _parse_fields(args.field)
            for name in ("text", "section", "level", "layer"):
                value = getattr(args, name)
                if value is not None:
                    fields[name] = value
            if args.anchor:
                fields["anchors"] = [json.loads(anchor) for anchor in args.anchor]
            _emit(claims.upsert_claim(ctx, args.claim_id, fields))
        elif args.verb == "claim-adjudicate":
            _emit(claims.adjudicate_preview(ctx))
        elif args.verb == "claim-record-status":
            _emit(claims.record_statuses(ctx, args.claim_ids or None))
        elif args.verb == "question-draft":
            _emit(
                questions.draft_question(
                    ctx, args.question, args.claims, question_id=args.id
                )
            )
        elif args.verb == "question-export":
            print(questions.export_open(ctx))
        elif args.verb == "answer-record":
            _emit(
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
        elif args.verb == "revision-record":
            _emit(
                revisions.record_revision(
                    ctx, args.tag, args.cluster, args.normative_change, args.note
                )
            )
        elif args.verb == "checkpoint":
            result = gates.write_checkpoint(ctx, args.cluster_id)
            _emit(result)
            return 0 if result["exit_code"] == 0 else 1
        elif args.verb == "gate":
            result = gates.manifest_gate(ctx, strict=args.strict)
            _emit(result)
            return result["exit_code"]
        elif args.verb == "citation-gate":
            result = gates.citation_gate(ctx, strict=args.strict)
            _emit(result)
            return result["exit_code"]
    except CoreError as error:
        _report(f"error: {error}")
        return 1
    except Exception as error:  # noqa: BLE001 - substrate errors surface verbatim
        _report(f"error: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
