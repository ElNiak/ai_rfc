"""``ai-rfc run --config``: perform every deterministic stage that is next.

Then, with a ``sessions:`` block configured, hand over to
:func:`ai_rfc.driver.sweep.run` and drive model sessions through the agent
stages. Without one the agent boundary still stops the walk, with the ledger
printed: a hand-mined workspace stays possible, and the operator sees what
remains rather than only being told whose turn it is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ... import __version__, ledger
from ...config import ConfigError, ConfigParseError
from ...driver import DriverError, sweep
from ...pipeline.run import perform
from ...pipeline.stages import BY_NAME, STAGES, Performer, is_optional
from ...pipeline.state import State, state
from .. import LifecycleError
from ..common import (
    add_config_argument,
    config_path_from,
    load_sealed,
    report,
    report_structured,
)

BOUNDARY = "mining"

#: Spec §5's two positional spellings of ``--until``. They name a place in the
#: timeline rather than a stage, so only the sweep — which holds the timeline —
#: can resolve one, and only a configuration with sessions reaches the sweep.
CLUSTER_BOUND = "cluster:"
ORDINAL_BOUND = "ordinal:"


def _walkable() -> list[str]:
    """The stages the walk actually performs, in order.

    These are the stage names ``--until`` accepts; it also takes spec §5's two
    positional spellings, which name no stage at all. ``forge`` is
    deterministic and sits before the boundary, but the walk skips it as
    optional — offering it would name a stage the ``until`` test can never
    match, so ``run --until forge`` would silently continue to the boundary
    instead of stopping.
    """
    return [
        stage.name
        for stage in STAGES
        if stage.performer is Performer.DETERMINISTIC
        and stage.ordinal < BY_NAME[BOUNDARY].ordinal
        and not is_optional(stage)
    ]


def _positional(until: str | None) -> bool:
    """Whether a bound names a place in the timeline rather than a stage."""
    return until is not None and until.startswith((CLUSTER_BOUND, ORDINAL_BOUND))


def _bound(value: str) -> str:
    """``--until``'s three spellings, checked for shape before any work runs.

    Spec §5 gives the bound as ``<stage>|cluster:<id>|ordinal:<n>``. Only the
    stage spelling is resolvable here: a cluster id and an ordinal are facts
    about a timeline, which the sweep holds and an argument parser does not.
    So the *syntax* is a closed set here and the *membership* is a closed set
    in :func:`ai_rfc.driver.sweep._bound_reached`, which has the timeline to
    check it against.

    An ordinal is required to be ASCII digits rather than merely to survive
    :func:`int`, which accepts surrounding whitespace: ``ordinal:7\\n`` would
    parse and then be echoed back on a report line as two lines.

    Args:
        value: What the operator typed after ``--until``.

    Returns:
        The bound, unchanged.

    Raises:
        argparse.ArgumentTypeError: If it is none of the three spellings.
    """
    if value.startswith(CLUSTER_BOUND):
        cluster_id = value.removeprefix(CLUSTER_BOUND)
        if not cluster_id:
            raise argparse.ArgumentTypeError(f"{value!r} names no cluster")
        if not cluster_id.isprintable():
            # Membership is the real guard on a cluster id, and it lives in
            # the sweep because it needs a timeline. What is left here is the
            # character class, and it is needed: this bound is interpolated
            # into the refusal below, which is a line-per-record artifact an
            # operator copies from — a newline there forges a whole second
            # instruction under the first. Refused rather than escaped,
            # because at the parser there is still somewhere to say no.
            raise argparse.ArgumentTypeError(
                f"cluster id {cluster_id!r} is not printable; it would reach "
                "a copied command line unescaped"
            )
        return value
    if value.startswith(ORDINAL_BOUND):
        raw = value.removeprefix(ORDINAL_BOUND)
        if not (raw.isascii() and raw.isdigit()):
            raise argparse.ArgumentTypeError(f"{raw!r} is not an ordinal")
        return value
    if value in _walkable():
        return value
    raise argparse.ArgumentTypeError(
        f"{value!r} names no stage this walk performs, no cluster and no "
        f"ordinal; use {', '.join(_walkable())}, "
        f"{CLUSTER_BOUND}<id> or {ORDINAL_BOUND}<n>"
    )


def run_stages(config_path: Path, *, until: str | None = None) -> int:
    """Walk the pipeline from what state says is pending, then drive the sweep.

    The deterministic stages before the agent boundary are performed here. What
    happens at the boundary is decided by the configuration: with a
    ``sessions:`` block the walk continues into
    :func:`ai_rfc.driver.sweep.run`, which owns every agent stage and the build
    gate behind them; without one the boundary stops the walk and the ledger is
    printed, because a hand-mined workspace stays possible.

    Args:
        config_path: The operator's ``recon.yaml``.
        until: Stop at this bound: a stage this walk performs, ``cluster:<id>``
            or ``ordinal:<n>``. Only a stage bound is honoured here; the two
            positional spellings pass through to the sweep, which has the
            timeline to resolve them against.

    Returns:
        0 when the boundary or a stage bound was reached, the failing stage's
        exit code when one failed, and the sweep's own code once it is driving
        — 0 finished or bounded, 1 stopped with work outstanding, 3 strict
        findings.

    Raises:
        LifecycleError: If the workspace is not initialised, an identity field
            drifted, the clone is not pinned, or a positional bound was given
            for a configuration that has no sessions to resolve it with.
        ledger.LedgerError: If the workspace's progress cannot be read.
        DriverError: From anywhere below. ``sweep.run``'s own three refusals
            narrow to one — the bound — since ``run`` passes neither ``retry``
            nor ``mode``; but the sweep does not catch what it calls, so a
            duplicate cluster id (re-checked by ``observe`` on *every*
            iteration, not only the first), an unlaunchable binary
            (``session.py:97``), an unparseable transcript line
            (``stream.py:41``) and the renderer's refusals all surface here
            too.
    """
    given, _sealed, layout, noted = load_sealed(config_path)
    for line in noted:
        report(f"note: config drift: {line}")
    if given.sessions is None and _positional(until):
        # D16's rule in its second setting: a bound that cannot fire must be
        # refused, not silently overrun. Only the sweep resolves a cluster or
        # an ordinal, and without sessions there is no sweep — so this bound
        # would let the walk run to the boundary and report success.
        raise LifecycleError(
            # Repr, not the bare value, following `stop._checked_cluster_id`'s
            # message for the same reason: this line is itself a
            # line-per-record artifact, and `run_stages` is reachable without
            # `_bound` — from `run` given any namespace. A guard that lives
            # only in the parser is a rule callers follow, not a boundary.
            f"--until {until!r} names a place in the timeline, and only a "
            "sweep resolves one; this workspace's config declares no "
            f"sessions, so run stops at the {BOUNDARY} boundary and the bound "
            "could never fire. Add a sessions: block, or bound the walk with "
            f"a stage name: {', '.join(_walkable())}"
        )
    by_name = {entry.stage.name: entry for entry in state(layout)}
    if by_name["pin"].state is not State.DONE:
        raise LifecycleError(
            f"the clone is not pinned: {by_name['pin'].reason}; "
            f"run: ai-rfc init --config {config_path}"
        )
    performed: list[str] = []
    stopped: str | None = None
    for stage in STAGES:
        if stage.performer is not Performer.DETERMINISTIC:
            if stage.name == BOUNDARY:
                break
            continue
        if stage.ordinal >= BY_NAME[BOUNDARY].ordinal:
            break
        if is_optional(stage):
            continue  # forge was acquired at init
        entry = by_name[stage.name]
        if entry.state not in (State.DONE, State.RECOMPUTED):
            result = perform(stage, layout)
            performed.append(stage.name)
            if not result.ok:
                report(f"error: {stage.name} exited {result.exit_code}")
                return result.exit_code
            report(f"performed: {stage.name}")
            by_name = {e.stage.name: e for e in state(layout)}
        # Tested after the already-current branch, not inside it: `--until`
        # bounds the walk, so it must stop whether or not this invocation was
        # the one that performed the stage. Testing it only after a `perform`
        # let a second `run --until history` step over its own bound and go on
        # to build the timeline and the views.
        if until == stage.name:
            stopped = stage.name
            break
    if not performed:
        report("performed: nothing (everything the walk reached was current)")
    if stopped is not None:
        report(f"stopped after {stopped}")
        return 0
    if given.sessions is not None:
        # The configuration **as given**, not the sealed copy: a `sessions:`
        # block added after `init` is noted drift rather than refused (D57),
        # so the seal does not carry it and sweeping the sealed copy would
        # refuse the very block the operator just wrote. `config_path` is the
        # path they typed, which is what the sweep's resume line prints back.
        return sweep.run(given, layout.root, until=until, config_path=config_path)
    # The ledger is read unguarded here, unlike in `status` and `verify`:
    # reaching this line means the walk ran to the boundary, so `views` is
    # current, so the timeline it was built from is on disk. The two reporting
    # verbs have no such guarantee — they are asked about workspaces at any
    # stage, including one that was only initialised.
    summary = ledger.counts(ledger.clusters(layout.root))
    nxt = ledger.next_cluster(layout.root)
    report(f"boundary: {BOUNDARY} — {BY_NAME[BOUNDARY].instruction}")
    report(
        f"clusters: {summary['done']} of {summary['in_window']} done, "
        f"{summary['partial']} partial, {summary['outstanding']} outstanding"
    )
    if nxt is not None:
        report(f"next cluster: {nxt.id} (ordinal {nxt.ordinal})")
    report(
        "sessions: not configured (add a sessions: block to let ai-rfc "
        "run drive model sessions)"
    )
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc run``."""
    parser.description = (
        "Perform every deterministic stage that is next, then drive model "
        "sessions when a sessions: block is configured; stop at the agent "
        "boundary with the ledger printed when one is not. The stages: tuning "
        "a recon.yaml validates is not wired into the stage builders yet."
    )
    add_config_argument(parser)
    parser.add_argument(
        "--until",
        type=_bound,
        metavar="BOUND",
        default=None,
        help=(
            "Stop at this bound: a stage ("
            + ", ".join(_walkable())
            + f"), {CLUSTER_BOUND}<id> or {ORDINAL_BOUND}<n>. The two "
            "positional spellings need a sessions: block."
        ),
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.run`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc run")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc run {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Perform the verb; 1 on a refusal or an unreadable input.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 at the boundary or a bound, a stage's own exit code when one failed,
        the sweep's own code once it is driving, 1 on a refusal.
    """
    try:
        return run_stages(config_path_from(args), until=args.until)
    except ConfigParseError as error:
        # Named before its base: the parser's block is the one diagnostic here
        # whose line breaks are its own, and whose closing caret marks a
        # column. Every other refusal below composes one record around a value
        # — a `--until` bound, a path, a cluster id — and must stay one line.
        report_structured(f"error: {error}")
        return 1
    except (
        LifecycleError,
        ConfigError,
        ledger.LedgerError,
        # The sweep refuses a bound that names no cluster of this timeline, and
        # it does so before launching anything. `DriverError` is a
        # `RuntimeError`, not a `LifecycleError`, so until `run` drove a sweep
        # nothing here could have raised it — and an uncaught one would print a
        # traceback where the other refusals print a line.
        DriverError,
        OSError,
    ) as error:
        report(f"error: {error}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.run``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
