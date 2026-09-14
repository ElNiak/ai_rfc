"""Command-line entry point for checkpoints, the citation gate and the draft.

``draft`` carries two kinds of verb, because it is the one verb name that was
already taken when spec D56's agent groups were mounted. ``checkpoint``,
``gate`` and ``completeness`` are the leaf's own: they name every input on the
command line and are what arm C and an operator type. ``commit`` is an agent
verb like the ten under :mod:`ai_rfc.agent`: it takes the workspace from the
environment and calls :mod:`ai_rfc.server.core`.

``build``, ``lint`` and ``render`` are both, and **the positional selects
which**. Named a draft repository or a manifest, they run the substrate
directly and report findings on stderr; given nothing, they resolve the
workspace and return the core's JSON, which is what the MCP arm returns and
what ``tests/server/test_parity.py`` compares against. A flag belonging to the
form the operator did not choose is a usage error rather than a silent no-op:
``--strict`` ignored would be a gate an author believed they had run.
"""

from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from ai_rfc import __version__

from ..agent import emit, perform
from ..schema import SchemaError, load
from .build import (
    BUILD_DIR,
    DEFAULT_TARGETS,
    REPORT_FILE,
    TOOLCHAIN_ENV,
    BuildError,
    build,
    resolve_toolchain,
)
from .checkpoint import (
    CheckpointError,
    write_checkpoint,
    write_consolidation_checkpoint,
)
from .completeness import CompletenessError
from .completeness import build as build_completeness
from .completeness import findings as completeness_findings
from .completeness import write_completeness_report
from .gate import GateError, draft_text, run_gate, write_gate_report
from .lint import lint, write_lint_report
from .structures import STRUCTURES_FILE, render_all

# Annotation-only, and deliberately not at module scope: ``build_parser``
# imports this module to call ``configure``, so a runtime import of
# ``server.paths`` here would be paid by ``ai-rfc --help`` too (D13). The
# runtime imports live in :func:`ai_rfc.agent.perform` and in each verb below.
if TYPE_CHECKING:
    from ..server.paths import Context


def _refuse_other_form(
    args: argparse.Namespace, passed: list[tuple[str, bool]], reason: str
) -> None:
    """Exit 2 when a flag names the form the operator did not choose.

    Reported rather than ignored, and through the parser rather than as a
    diagnostic: a flag that changes nothing is indistinguishable from one that
    worked, and the worst of them is ``--strict``, whose whole content is that
    a finding should have refused the run.

    Args:
        args: The parsed namespace; ``args._parser`` is the parser that owns
            these arguments and so the one whose usage line belongs under the
            message, which differs between the two doors.
        passed: ``(flag, was it given)`` for every flag of the other form.
        reason: What the named flags belong to, completing the message.
    """
    named = [flag for flag, given in passed if given]
    if named:
        belongs = "belongs" if len(named) == 1 else "belong"
        args._parser.error(f"{', '.join(named)} {belongs} to {reason}")


def _commit(args: argparse.Namespace, ctx: Context) -> int:
    """Commit every change in the workspace's draft repository."""
    from ..server.core import draft as draft_core

    emit(draft_core.commit_draft(ctx, args.message))
    return 0


def _build_workspace(args: argparse.Namespace, ctx: Context) -> int:
    """Compile the workspace's draft, surfacing the core's own exit code."""
    from ..server.core import build as build_core

    result = build_core.draft_build(ctx, args.ref)
    emit(result)
    return int(result["exit_code"])


def _lint_workspace(args: argparse.Namespace, ctx: Context) -> int:
    """Measure the workspace's draft, surfacing the core's own exit code."""
    from ..server.core import build as build_core

    result = build_core.draft_lint(ctx, worktree=not args.committed)
    emit(result)
    return int(result["exit_code"])


def _render_workspace(args: argparse.Namespace, ctx: Context) -> int:
    """Print the workspace manifest's structures as kramdown blocks.

    ``print(..., end="")`` rather than :func:`~ai_rfc.agent.emit`: the blocks
    are the payload, not a result about one, and the tool arm returns the same
    string with no quotes around it and no trailing newline added.
    """
    from ..server.core import structures as structures_core

    print(structures_core.render_structures(ctx), end="")
    return 0


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
    render.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=None,
        help="Manifest to render. Omitted: the workspace's own, through the "
        "core, printed as the tool arm prints it.",
    )
    render.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Also write structures.md into this directory (needs MANIFEST).",
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
    build_verb.add_argument(
        "draftrepo",
        type=Path,
        nargs="?",
        default=None,
        help="The nested draft repository. Omitted: the workspace's own, "
        "through the core.",
    )
    build_verb.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory receiving build/. Required with DRAFTREPO.",
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
        default=None,
        # The default is written out rather than left to `%(default)s`: the
        # stored default is None so that "was it given" is answerable, and
        # `%(default)s` would print that None into the help.
        help="Comma-separated make targets " f"(default: {','.join(DEFAULT_TARGETS)}).",
    )
    build_verb.add_argument(
        "--date", default=None, help="xml2rfc -D date; default: the ref's commit date."
    )
    build_verb.add_argument(
        "--strict", action="store_true", help="Exit 3 when the build has findings."
    )

    lint_verb = verbs.add_parser("lint", help="Measure a draft revision's quality.")
    lint_verb.add_argument(
        "draftrepo",
        type=Path,
        nargs="?",
        default=None,
        help="The nested draft repository. Omitted: the workspace's own, "
        "through the core.",
    )
    lint_verb.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory for lint-report.json. Required with DRAFTREPO.",
    )
    # All three name which text to measure, so argparse refuses any two of
    # them together. The two halves disagree about the default on purpose:
    # `ai-rfc draft lint DRAFTREPO` reads HEAD, because an operator naming a
    # repository means the revision in it, while a bare `ai-rfc draft lint`
    # reads the uncommitted file, because that is what `ai_rfc_draft_lint()`
    # does and an author lints before committing.
    which = lint_verb.add_mutually_exclusive_group()
    which.add_argument(
        "--ref", default=None, help="Tag, branch or commit to read (default: HEAD)."
    )
    which.add_argument(
        "--worktree",
        action="store_true",
        help="Read the uncommitted draft file instead of a ref.",
    )
    which.add_argument(
        "--committed",
        action="store_true",
        help="Measure HEAD rather than the uncommitted file (no DRAFTREPO).",
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

    commit = verbs.add_parser(
        "commit",
        help="Commit every change in the workspace's draft/ (a clean tree is "
        "an error).",
    )
    commit.add_argument("-m", "--message", required=True, help="Commit message.")


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
        or ``lint --strict`` reported findings. The workspace forms of
        ``build`` and ``lint`` return the core's code raw instead, so a gate's
        3 arrives as 3 rather than collapsed.

    Raises:
        AssertionError: If ``args.verb`` names no branch. argparse admits only
            the verbs :func:`configure` declares, so this is unreachable by
            argv; it replaces the fallthrough that used to run ``gate`` for
            any verb missing a branch, silently and with the wrong arguments.
    """
    # Function-local, and not to be tidied to the top (D13's shape, and the
    # idiom of ``pipeline/run.py:77``). ``ai_rfc/cli.py``'s ``build_parser``
    # imports this module to call ``configure``, and ``configure`` needs none
    # of this. Measured: the root door pays **0** extra modules either way,
    # because the lifecycle verbs it also mounts already hold
    # ``lifecycle.common``; the standalone ``python -m ai_rfc.draft --help``
    # pays **71**, and this guard is what it keeps out.
    #
    # 71 and not the 72 a bare ``import ai_rfc.lifecycle.common`` adds after
    # this module: argparse's help rendering loads ``locale`` and ``textwrap``
    # first, and ``locale`` is in this chain too, so by the time ``--help``
    # could pay, one of the 72 is already there. The number to state is the
    # one on the path the guard protects, not the one measured before the
    # parser is built.
    #
    # ``report`` rather than a local ``print``: every line below interpolates
    # a value an author, an operator or a model session controls — a cluster
    # id out of ``revisions.yaml``, a toolchain path out of ``toolchain.json``
    # — and a break in one of those forges a second line in this command's own
    # grammar. ``report_diagnostic`` is the other half of the pair and asks the
    # **raise site** whether its breaks are a producer's structure.
    from ..lifecycle.common import report, report_diagnostic

    if args.verb == "commit":
        return perform(partial(_commit, args))

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
            report_diagnostic("error: ", error)
            return 1
        report(f"note: checkpoint written to {checkpoint_dir}")
        return 0

    if args.verb == "render":
        if args.manifest is None:
            _refuse_other_form(
                args,
                [("--out", args.out is not None)],
                "`draft render MANIFEST`, which is the form that has a "
                "manifest to write structures.md beside",
            )
            return perform(partial(_render_workspace, args))
        try:
            text = render_all(load(args.manifest))
        except (ValueError, OSError) as error:
            report_diagnostic("error: ", error)
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
            completeness_report = build_completeness(
                workspace / "timeline",
                workspace / "checkpoints",
                workspace / "manifest.yaml",
                workspace / "revisions.yaml",
                workspace / "draft",
            )
        except (CompletenessError, OSError) as error:
            report_diagnostic("error: ", error)
            return 1

        write_completeness_report(args.out, completeness_report)

        found = completeness_findings(completeness_report)
        for finding in found:
            report(f"finding: {finding}")
        if not found:
            report("note: reconstruction complete")
        if found and args.strict:
            return 3
        return 0

    if args.verb == "build":
        if args.draftrepo is None:
            _refuse_other_form(
                args,
                [
                    ("--out", args.out is not None),
                    ("--toolchain", args.toolchain is not None),
                    ("--refcache", args.refcache is not None),
                    ("--targets", args.targets is not None),
                    ("--date", args.date is not None),
                    ("--strict", args.strict),
                ],
                "`draft build DRAFTREPO --out DIR`; the workspace form takes "
                "its paths and its toolchain from the context",
            )
            return perform(partial(_build_workspace, args))
        if args.out is None:
            args._parser.error("DRAFTREPO requires --out")
        try:
            toolchain = resolve_toolchain(args.toolchain)
            build_report = build(
                args.draftrepo,
                toolchain=toolchain,
                out=args.out,
                ref=args.ref,
                targets=tuple(
                    t
                    for t in (args.targets or ",".join(DEFAULT_TARGETS)).split(",")
                    if t
                ),
                date=args.date,
                refcache=args.refcache,
            )
        except (BuildError, OSError) as error:
            report_diagnostic("error: ", error)
            return 1
        for finding in build_report.findings:
            report(f"finding: {finding}")
        report(
            f"note: build of {build_report.commit[:12]} exited "
            f"{build_report.exit_code}; "
            f"report at {args.out / BUILD_DIR / REPORT_FILE}"
        )
        if build_report.findings and args.strict:
            return 3
        return 0

    if args.verb == "lint":
        if args.draftrepo is None:
            _refuse_other_form(
                args,
                [
                    ("--out", args.out is not None),
                    ("--ref", args.ref is not None),
                    ("--worktree", args.worktree),
                    ("--manifest", args.manifest is not None),
                    ("--strict", args.strict),
                ],
                "`draft lint DRAFTREPO --out DIR`; the workspace form measures "
                "the workspace's own draft and manifest",
            )
            return perform(partial(_lint_workspace, args))
        if args.out is None:
            args._parser.error("DRAFTREPO requires --out")
        if args.committed:
            args._parser.error("--committed: the explicit form spells this --ref HEAD")
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
                ref = args.ref or "HEAD"
                _, text = draft_text(args.draftrepo, ref)
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
            lint_report = lint(
                text,
                manifest=manifest,
                manifest_error=manifest_error,
                source={"path": str(args.draftrepo), "ref": ref},
            )
        except (ValueError, OSError) as error:
            report_diagnostic("error: ", error)
            return 1
        report_path = write_lint_report(args.out, lint_report)
        for finding in lint_report.findings:
            report(f"finding: {finding}")
        report(f"note: lint report at {report_path}")
        if lint_report.findings and args.strict:
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
            report_diagnostic("error: ", error)
            return 1

        write_gate_report(args.out, findings)

        for finding in findings:
            report(f"finding: {finding}")
        if findings and args.strict:
            return 3
        if not findings:
            report("note: gate clean")
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
