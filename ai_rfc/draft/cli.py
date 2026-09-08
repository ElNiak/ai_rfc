"""Command-line entry point for checkpoints and the citation gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ai_rfc import __version__

from ..schema import SchemaError, load
from .build import (
    BUILD_DIR,
    DEFAULT_TARGETS,
    REPORT_FILE,
    TOOLCHAIN_ENV,
    BuildError,
    build,
    load_toolchain,
    probe_toolchain,
)
from .checkpoint import (
    CheckpointError,
    write_checkpoint,
    write_consolidation_checkpoint,
)
from .completeness import CompletenessError
from .completeness import build as build_completeness
from .completeness import findings as completeness_findings
from .completeness import to_json as completeness_json
from .gate import GateError, draft_text, run_gate
from .lint import REPORT_FILE as LINT_REPORT_FILE
from .lint import lint
from .structures import STRUCTURES_FILE, render_all


def _report(message: str) -> None:
    """Write a diagnostic to stderr.

    Deliberately not the ``logging`` module. Every ``panther.*`` logger is
    configured with ``propagate=False`` and a handler admitting only ``ERROR``,
    so a logged warning here is discarded before anyone sees it.
    """
    print(message, file=sys.stderr)


def configure(parser: argparse.ArgumentParser) -> None:
    """Add this command's arguments to ``parser``.

    Args:
        parser: Either the root's subparser for this command or the standalone
            parser :func:`build_standalone_parser` builds; both must carry the
            same arguments, so both are configured here.
    """
    parser.description = (
        "Freeze manifest checkpoints against timeline clusters, gate a "
        "prose draft's revision map against them, and measure how much of "
        "the timeline the reconstruction has actually specified."
    )
    # `run` reports one cross-argument requirement argparse cannot express, and
    # only the parser that owns the arguments can print the usage line beneath
    # it — which differs between the two doors, so it is carried, not rebuilt.
    parser.set_defaults(_parser=parser)
    verbs = parser.add_subparsers(dest="verb", required=True)

    checkpoint = verbs.add_parser(
        "checkpoint", help="Freeze a manifest against one timeline cluster."
    )
    checkpoint.add_argument("manifest", type=Path, help="Manifest to freeze.")
    checkpoint.add_argument(
        "--timeline", type=Path, required=True, help="Timeline directory."
    )
    checkpoint.add_argument(
        "--cluster", required=True, help="Cluster id the manifest state belongs to."
    )
    checkpoint.add_argument(
        "--out", type=Path, required=True, help="Checkpoints root directory."
    )
    checkpoint.add_argument(
        "--consolidation",
        type=int,
        default=None,
        help="Write a consolidation checkpoint with this ordinal instead of a "
        "cluster one.",
    )
    checkpoint.add_argument(
        "--base",
        type=Path,
        default=None,
        help="The cluster checkpoint a consolidation follows. Required with "
        "--consolidation.",
    )

    render = verbs.add_parser(
        "render", help="Render a manifest's structures as kramdown blocks."
    )
    render.add_argument("manifest", type=Path, help="Manifest to render.")
    render.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Also write structures.md into this directory.",
    )

    gate = verbs.add_parser(
        "gate", help="Run the deterministic citation gate over a draft."
    )
    gate.add_argument("draftrepo", type=Path, help="The nested draft repository.")
    gate.add_argument(
        "--timeline", type=Path, required=True, help="Timeline directory."
    )
    gate.add_argument(
        "--checkpoints", type=Path, required=True, help="Checkpoints root."
    )
    gate.add_argument(
        "--questions", type=Path, required=True, help="Question register."
    )
    gate.add_argument("--revisions", type=Path, required=True, help="Revision map.")
    gate.add_argument(
        "--out", type=Path, required=True, help="Directory for gate-report.json."
    )
    gate.add_argument(
        "--strict",
        action="store_true",
        help="Exit 3 when any finding is reported.",
    )
    gate.add_argument(
        "--consolidations",
        type=Path,
        default=None,
        help="Consolidation checkpoint root. Default: the sibling of --checkpoints.",
    )

    complete = verbs.add_parser(
        "completeness",
        help="Report which clusters produced no claim and which claims no "
        "prose cites.",
    )
    complete.add_argument(
        "workspace",
        type=Path,
        help="Workspace root holding timeline/, checkpoints/, draft/, "
        "manifest.yaml and revisions.yaml.",
    )
    complete.add_argument(
        "--out", type=Path, required=True, help="Directory for completeness.json."
    )
    complete.add_argument(
        "--strict",
        action="store_true",
        help="Exit 3 when any finding is reported.",
    )

    build_verb = verbs.add_parser(
        "build",
        help="Compile a draft revision with the template toolchain, offline.",
    )
    build_verb.add_argument("draftrepo", type=Path, help="The nested draft repository.")
    build_verb.add_argument(
        "--out", type=Path, required=True, help="Directory receiving build/."
    )
    build_verb.add_argument(
        "--ref", default="HEAD", help="Tag, branch or commit to build (default: HEAD)."
    )
    build_verb.add_argument(
        "--toolchain",
        type=Path,
        default=None,
        help=f"toolchain.json (default: ${TOOLCHAIN_ENV}).",
    )
    build_verb.add_argument(
        "--refcache",
        type=Path,
        default=None,
        help="Reference cache overriding the toolchain's (a sealed workspace cache).",
    )
    build_verb.add_argument(
        "--targets",
        default=",".join(DEFAULT_TARGETS),
        help="Comma-separated make targets (default: %(default)s).",
    )
    build_verb.add_argument(
        "--date", default=None, help="xml2rfc -D date; default: the ref's commit date."
    )
    build_verb.add_argument(
        "--strict", action="store_true", help="Exit 3 when the build has findings."
    )

    lint_verb = verbs.add_parser("lint", help="Measure a draft revision's quality.")
    lint_verb.add_argument("draftrepo", type=Path, help="The nested draft repository.")
    lint_verb.add_argument(
        "--out", type=Path, required=True, help="Directory for lint-report.json."
    )
    which = lint_verb.add_mutually_exclusive_group()
    which.add_argument("--ref", default="HEAD", help="Tag, branch or commit to read.")
    which.add_argument(
        "--worktree",
        action="store_true",
        help="Read the uncommitted draft file instead of a ref.",
    )
    lint_verb.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Manifest for citation coverage (unloadable → a finding, not an error).",
    )
    lint_verb.add_argument(
        "--strict", action="store_true", help="Exit 3 when any finding is reported."
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.draft`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc draft")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc draft {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Perform the parsed verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 if an input could not be read or interpreted, and 3
        when ``gate --strict``, ``completeness --strict``, ``build --strict``
        or ``lint --strict`` reported findings.
    """
    if args.verb == "checkpoint":
        if args.consolidation is not None and args.base is None:
            args._parser.error("--consolidation requires --base")
        if args.base is not None and args.consolidation is None:
            args._parser.error("--base requires --consolidation")
        try:
            if args.consolidation is not None:
                checkpoint_dir = write_consolidation_checkpoint(
                    args.manifest,
                    args.consolidation,
                    args.base,
                    args.cluster,
                    args.out,
                )
            else:
                checkpoint_dir = write_checkpoint(
                    args.manifest, args.timeline, args.cluster, args.out
                )
        except (CheckpointError, SchemaError, OSError) as error:
            _report(f"error: {error}")
            return 1
        _report(f"note: checkpoint written to {checkpoint_dir}")
        return 0

    if args.verb == "render":
        try:
            text = render_all(load(args.manifest))
        except (ValueError, OSError) as error:
            _report(f"error: {error}")
            return 1
        if text:
            print(text, end="")
        if args.out is not None:
            args.out.mkdir(parents=True, exist_ok=True)
            # The checkpoint writers freeze these bytes and the gate compares
            # them; `write_text` would translate the newlines and the two
            # renderings of one manifest would differ off POSIX.
            (args.out / STRUCTURES_FILE).write_bytes(text.encode())
        return 0

    if args.verb == "completeness":
        workspace = args.workspace
        try:
            report = build_completeness(
                workspace / "timeline",
                workspace / "checkpoints",
                workspace / "manifest.yaml",
                workspace / "revisions.yaml",
                workspace / "draft",
            )
        except (CompletenessError, OSError) as error:
            _report(f"error: {error}")
            return 1

        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "completeness.json").write_text(completeness_json(report))

        found = completeness_findings(report)
        for finding in found:
            _report(f"finding: {finding}")
        if not found:
            _report("note: reconstruction complete")
        if found and args.strict:
            return 3
        return 0

    if args.verb == "build":
        toolchain_path = args.toolchain or (
            Path(os.environ[TOOLCHAIN_ENV]) if os.environ.get(TOOLCHAIN_ENV) else None
        )
        if toolchain_path is None:
            _report(
                f"error: no toolchain; pass --toolchain or set {TOOLCHAIN_ENV} "
                "(ai-rfc toolchain provision writes it)"
            )
            return 1
        try:
            toolchain = load_toolchain(toolchain_path)
            missing = probe_toolchain(toolchain)
            if missing:
                raise BuildError("toolchain incomplete: " + "; ".join(missing))
            report = build(
                args.draftrepo,
                toolchain=toolchain,
                out=args.out,
                ref=args.ref,
                targets=tuple(t for t in args.targets.split(",") if t),
                date=args.date,
                refcache=args.refcache,
            )
        except (BuildError, OSError) as error:
            _report(f"error: {error}")
            return 1
        for finding in report.findings:
            _report(f"finding: {finding}")
        _report(
            f"note: build of {report.commit[:12]} exited {report.exit_code}; "
            f"report at {args.out / BUILD_DIR / REPORT_FILE}"
        )
        if report.findings and args.strict:
            return 3
        return 0

    if args.verb == "lint":
        try:
            if args.worktree:
                candidates = sorted(
                    p
                    for p in args.draftrepo.iterdir()
                    if p.name.startswith("draft-") and p.suffix == ".md"
                )
                if len(candidates) != 1:
                    raise GateError(
                        f"{args.draftrepo}: expected exactly one draft-*.md, "
                        f"found {len(candidates)}"
                    )
                text, ref = candidates[0].read_text(), "worktree"
            else:
                _, text = draft_text(args.draftrepo, args.ref)
                ref = args.ref
            manifest = None
            manifest_error = None
            if args.manifest is not None:
                try:
                    manifest = load(args.manifest)
                except (SchemaError, OSError) as error:
                    manifest_error = str(error)
            # `lint` renders the manifest itself, so it raises whatever the
            # renderer does; a manifest the loader accepted can still refuse to
            # render, and that must read as an error rather than a traceback.
            report = lint(
                text,
                manifest=manifest,
                manifest_error=manifest_error,
                source={"path": str(args.draftrepo), "ref": ref},
            )
        except (ValueError, OSError) as error:
            _report(f"error: {error}")
            return 1
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / LINT_REPORT_FILE).write_text(report.to_json())
        for finding in report.findings:
            _report(f"finding: {finding}")
        _report(f"note: lint report at {args.out / LINT_REPORT_FILE}")
        if report.findings and args.strict:
            return 3
        return 0

    if args.verb == "gate":
        try:
            findings = run_gate(
                args.draftrepo,
                args.timeline,
                args.checkpoints,
                args.questions,
                args.revisions,
                consolidations_dir=args.consolidations,
            )
        except (GateError, OSError) as error:
            _report(f"error: {error}")
            return 1

        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "gate-report.json").write_text(
            json.dumps({"findings": list(findings)}, sort_keys=True, indent=2) + "\n"
        )

        for finding in findings:
            _report(f"finding: {finding}")
        if findings and args.strict:
            return 3
        if not findings:
            _report("note: gate clean")
        return 0

    # argparse admits only the verbs above, so this is unreachable by argv. It
    # replaces the fallthrough that used to run `gate` for any verb missing a
    # branch, silently and with the wrong arguments.
    raise AssertionError(f"unhandled verb {args.verb!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the requested verb from the command line (``python -m ai_rfc.draft``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The verb's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
