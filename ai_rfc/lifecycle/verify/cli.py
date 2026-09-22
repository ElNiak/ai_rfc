"""``ai-rfc verify --config``: every gate the workspace can pass, in one command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ... import __version__, ledger
from ...config import ConfigError
from ...draft import cli as draft_cli
from ...driver import record as run_record
from ...driver.coverage import Route, covers, read_transcript
from ...parser import Parser
from ...pipeline.run import perform, worst_exit_code
from ...pipeline.stages import BY_NAME
from .. import LifecycleError
from ..common import (
    add_config_argument,
    config_path_from,
    load_pair,
    report,
    report_diagnostic,
)
from ..workspace import Layout

NO_TIMELINE = "skipped (no timeline yet; run ai-rfc run first)"
NO_TOOLCHAIN = "skipped (no toolchain record; see ai-rfc doctor)"
#: Where the per-checkpoint route is frozen, after the precedent
#: :func:`ai_rfc.draft.gate.write_gate_report` sets.
COVERAGE_FILE = "coverage.json"
#: Why an uncovered checkpoint is a finding, spelled once. It names the three
#: routes rather than a verdict: the checkpoint is **unverified**, never
#: forged — a session row is appended only after the process returns, so a
#: killed session legitimately leaves none.
UNVERIFIED = "no session row, no pre-seed marker, no receipt"


def _outcome(code: int) -> str:
    """How one check's exit code reads in the report."""
    if code == 0:
        return "ok"
    if code == 3:
        return "findings"
    return f"error ({code})"


def write_coverage_report(
    out: Path, routes: dict[str, Route | None], unreadable: list[str]
) -> Path:
    """Freeze what covered each checkpoint, and what could not be adjudicated.

    Quiet by default and auditable afterwards: a workspace holding a hundred
    covered checkpoints must not drown the tally line, and the route each one
    was covered by is still worth keeping. The shape follows
    :func:`ai_rfc.draft.gate.write_gate_report`'s.

    Args:
        out: The workspace's ``out/`` directory; created if absent.
        routes: What :func:`~ai_rfc.driver.coverage.covers` returned.
        unreadable: One ``cannot adjudicate`` line per candidate transcript
            that could not be read. Neither a finding nor an error.

    Returns:
        The path written.
    """
    out.mkdir(parents=True, exist_ok=True)
    path = out / COVERAGE_FILE
    path.write_text(
        json.dumps(
            {
                "checkpoints": {
                    cluster_id: route.value if route is not None else None
                    for cluster_id, route in routes.items()
                },
                "unreadable": list(unreadable),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    return path


def _coverage(layout: Layout) -> tuple[dict[str, Route | None], list[str]]:
    """Which route covers each checkpoint of a production workspace.

    This is the caller half of the coverage ruling: it enumerates the
    candidates — the workspace's own ``runs/*/sessions.jsonl`` and
    ``runs/*/events.jsonl``, which is where a driver run leaves them — and the
    predicate in :mod:`ai_rfc.driver.coverage` decides. A workspace with no
    ``runs/`` at all is ordinary rather than suspicious: it is what a
    hand-driven or MCP-only reconstruction looks like, so the two remaining
    routes decide and nothing is said about the directory that is not there.

    A parse error must not escape. ``verify`` maps any exit code that is not 0
    or 3 to 1, so a detector letting one through would turn a clean workspace
    into an error; :func:`~ai_rfc.driver.coverage.read_transcript` catches it
    and names the damage instead.

    Args:
        layout: The workspace.

    Returns:
        The routes, and one ``cannot adjudicate`` line per unreadable
        candidate.
    """
    parsed = []
    unreadable = []
    for transcript in run_record.transcripts(layout.root):
        events, damage = read_transcript(transcript)
        if damage is None:
            parsed.append(events)
        else:
            unreadable.append(damage)
    routes = covers(
        layout.checkpoints,
        session_rows=run_record.session_rows(layout.root),
        transcripts=parsed,
    )
    return routes, unreadable


def verify(config_path: Path, *, strict: bool) -> int:
    """Run drift, strict check, strict gate, completeness, lint, coverage, build.

    Args:
        config_path: The operator's ``recon.yaml``.
        strict: Whether findings reach the caller as an exit code.

    Returns:
        0 when nothing that ran failed; 3 findings (only under ``strict``); 1
        when a check ran and could not complete. A skipped check contributes
        no exit code, so the tally on the last line — not the exit code — is
        what says whether every check ran.

    Raises:
        LifecycleError: If the workspace was never initialised.
        ConfigError: If either the given or the sealed config does not validate.
    """
    # `load_pair`, not `load_sealed`: a refused identity field is a finding
    # this verb reports, so raising on one would hide what was asked for.
    given, _sealed, layout, refused, noted = load_pair(config_path)
    codes: list[int] = []
    ran = ["drift"]
    skipped: list[str] = []
    if refused:
        report("drift: refused — " + "; ".join(refused))
        codes.append(3)
    else:
        report("drift: ok" + (f" ({len(noted)} noted)" if noted else ""))
    # `gate` maps every revision tag onto a timeline cluster and
    # `completeness` counts the clusters that produced no claim, so both read
    # the timeline as well as the registers `init` writes. On a workspace that
    # has been initialised but not run they have nothing to read, which is a
    # check that cannot run yet rather than one that failed.
    clustered = (layout.root / ledger.CLUSTERS_FILE).exists()
    for name in ("check", "gate", "lint"):
        if name == "gate" and not clustered:
            report(f"gate: {NO_TIMELINE}")
            skipped.append(name)
            continue
        result = perform(BY_NAME[name], layout, strict=True)
        report(f"{name}: {_outcome(result.exit_code)}")
        codes.append(result.exit_code)
        ran.append(name)
    if clustered:
        completeness = draft_cli.main(
            ["completeness", str(layout.root), "--out", str(layout.out), "--strict"]
        )
        report(f"completeness: {_outcome(completeness)}")
        codes.append(completeness)
        ran.append("completeness")
    else:
        report(f"completeness: {NO_TIMELINE}")
        skipped.append("completeness")
    # Always runs: every workspace has checkpoints or has none, and neither is
    # a check that cannot run yet. An empty checkpoints directory is a clean
    # coverage result, not a skip.
    routes, unreadable = _coverage(layout)
    uncovered = sorted(
        cluster_id for cluster_id, route in routes.items() if route is None
    )
    report(f"coverage: {_outcome(3 if uncovered else 0)}")
    for line in unreadable:
        report(line)
    for cluster_id in uncovered:
        report(f"finding: checkpoint {cluster_id} is unverified: {UNVERIFIED}")
    write_coverage_report(layout.out, routes, unreadable)
    codes.append(3 if uncovered else 0)
    ran.append("coverage")
    if given.toolchain is not None and given.toolchain.exists():
        result = perform(
            BY_NAME["build"], layout, strict=True, toolchain=given.toolchain
        )
        report(f"build: {_outcome(result.exit_code)}")
        codes.append(result.exit_code)
        ran.append("build")
    else:
        report(f"build: {NO_TOOLCHAIN}")
        skipped.append("build")
    # A skipped check appends no exit code, so 0 cannot be told from a clean
    # full pass by the code alone. The tally is what carries that difference
    # to an operator and to a driver reading stderr.
    report(
        f"checks: {len(ran)} ran, {len(skipped)} skipped"
        + (f" ({', '.join(skipped)})" if skipped else "")
    )
    # The rule this door wrote first, now read from its one home so the two
    # aggregating doors cannot rank the same three codes in two orders.
    # `strict` stays here: whether findings are an exit code at all is this
    # verb's own question, not the ranking's.
    worst = worst_exit_code(codes)
    return 0 if worst == 3 and not strict else worst


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc verify``."""
    parser.description = (
        "Config drift, strict manifest check, citation gate, completeness, "
        "lint, checkpoint coverage and build, in one exit code. A check whose "
        "inputs do not exist yet is skipped and contributes no exit code, so 0 "
        "means nothing that ran failed, not that everything ran; the last line "
        "tallies how many checks ran and names every one that was skipped. "
        "Coverage asks what says each checkpoint was produced — a session row, "
        "a pre-seed marker or a transcript receipt — and reports one that "
        "nothing vouches for as unverified, never as forged."
    )
    add_config_argument(parser)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 3 when any check reports findings.",
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.verify`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = Parser(prog="ai-rfc verify")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc verify {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Perform the verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        The verify exit code, or 1 on a refusal.
    """
    try:
        return verify(config_path_from(args), strict=args.strict)
    except (LifecycleError, ConfigError, OSError) as error:
        # One clause. Whether a parser's caret survives is the raise
        # site's property to declare, not this clause's to guess:
        # report_diagnostic escapes the path each message opens with
        # and prints a parser's block with its breaks.
        report_diagnostic("error: ", error)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.verify``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
