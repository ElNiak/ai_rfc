"""The ``arfc`` CLI — the AI+CLI arm's frontend over the shared core.

Every verb maps 1:1 onto a core function; results go to stdout as JSON,
diagnostics to stderr, and gate exit codes pass through untouched (0 clean,
1 unusable inputs, 2 a usage error from argparse itself, 3 strict findings).
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
    query.add_argument("sql", help="A single SELECT; at most 200 rows come back.")

    get = verbs.add_parser("cluster-get", help="One cluster's evidence.")
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

    verbs.add_parser("cluster-next", help="Next unprocessed cluster.")

    upsert = verbs.add_parser(
        "claim-upsert", help="Add or update a claim (status is never accepted)."
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
    question.add_argument(
        "question", help="The question; quote the claim wording verbatim."
    )
    question.add_argument(
        "--claim",
        action="append",
        required=True,
        dest="claims",
        help="A claim this question would unblock; repeatable, must exist.",
    )
    question.add_argument(
        "--id", default=None, help="Explicit id; default takes the next free q-NNN."
    )

    verbs.add_parser("question-export", help="Markdown bundle of open questions.")

    answer = verbs.add_parser(
        "answer-record", help="Ingest one answer from a saved transcript."
    )
    answer.add_argument("question_id", help="The register entry being answered.")
    answer.add_argument(
        "--answer", required=True, help="The author's answer, in their words."
    )
    answer.add_argument("--by", required=True, help="Who answered.")
    answer.add_argument(
        "--transcript",
        required=True,
        help="Transcript filename under interviews/ (e.g. int-001.md); must "
        "already be saved.",
    )
    answer.add_argument(
        "--quote",
        required=True,
        help="A verbatim span that must appear in the transcript — the evidence "
        "the answer actually happened.",
    )
    answer.add_argument(
        "--exact-wording-confirmed",
        action="store_true",
        help="The author confirmed the exact claim wording; grants sign-off.",
    )

    revision = verbs.add_parser("revision-record", help="Record a revision entry.")
    revision.add_argument("tag", help="Revision tag, e.g. draft-<name>-01.")
    revision.add_argument(
        "--cluster", required=True, help="The cluster this revision freezes."
    )
    normative = revision.add_mutually_exclusive_group(required=True)
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
    revision.add_argument("--note", required=True, help="What this revision did.")

    checkpoint = verbs.add_parser(
        "checkpoint", help="Freeze the manifest against one cluster."
    )
    checkpoint.add_argument(
        "cluster_id", help="Cluster to freeze against; checkpoints are write-once."
    )

    gate = verbs.add_parser("gate", help="Manifest gate (linter by default).")
    gate.add_argument(
        "--strict", action="store_true", help="Exit 3 when any finding is reported."
    )

    citation = verbs.add_parser("citation-gate", help="Draft citation gate.")
    citation.add_argument(
        "--strict", action="store_true", help="Exit 3 when any finding is reported."
    )

    draft_commit = verbs.add_parser(
        "draft-commit", help="Commit every change in draft/ (clean tree is an error)."
    )
    draft_commit.add_argument(
        "-m", "--message", required=True, help="Commit message."
    )

    revision_tag = verbs.add_parser(
        "revision-tag",
        help="Create the annotated revision tag once both strict gates accept it.",
    )
    revision_tag.add_argument("tag", help="Tag to create, e.g. draft-<name>-01.")
    revision_tag.add_argument(
        "-m", "--message", required=True, help="Annotation for the tag."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one verb against the workspace named by the environment.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        0 on success, 1 when inputs or guardrails refuse the operation, and
        the gate verbs pass their own exit codes through (3 = strict findings;
        2 is argparse's, and means the invocation was malformed).
    """
    args = _parser().parse_args(argv)

    try:
        ctx = resolve_context()
    except EnvError as error:
        _report(f"error: {error}")
        return 1

    from .core import claims, draft, gates, queries, questions, revisions

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
        elif args.verb == "draft-commit":
            _emit(draft.commit_draft(ctx, args.message))
        elif args.verb == "revision-tag":
            result = draft.tag_revision(ctx, args.tag, args.message)
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
