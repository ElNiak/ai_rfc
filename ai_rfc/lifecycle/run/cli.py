"""``ai-rfc run --config``: perform every deterministic stage that is next.

Then stop at the agent boundary with the ledger printed, so the operator sees
what remains rather than only being told whose turn it is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ... import __version__, ledger
from ...config import ConfigError
from ...pipeline.run import perform
from ...pipeline.stages import BY_NAME, STAGES, Performer, is_optional
from ...pipeline.state import State, state
from .. import LifecycleError
from ..common import add_config_argument, config_path_from, load_sealed, report

BOUNDARY = "mining"


def _walkable() -> list[str]:
    """The stages the walk actually performs, in order.

    ``--until`` offers exactly these. ``forge`` is deterministic and sits
    before the boundary, but the walk skips it as optional — offering it would
    name a stage the ``until`` test can never match, so ``run --until forge``
    would silently continue to the boundary instead of stopping.
    """
    return [
        stage.name
        for stage in STAGES
        if stage.performer is Performer.DETERMINISTIC
        and stage.ordinal < BY_NAME[BOUNDARY].ordinal
        and not is_optional(stage)
    ]


def run_stages(config_path: Path, *, until: str | None = None) -> int:
    """Walk the pipeline from what state says is pending up to the agent boundary.

    Args:
        config_path: The operator's ``recon.yaml``.
        until: Stop after this stage instead of at the boundary.

    Returns:
        0 when the boundary (or ``until``) was reached; the failing stage's
        exit code otherwise.

    Raises:
        LifecycleError: If the workspace is not initialised, an identity field
            drifted, or the clone is not pinned.
        ledger.LedgerError: If the workspace's progress cannot be read.
    """
    given, _sealed, layout, noted = load_sealed(config_path)
    for line in noted:
        report(f"note: config drift: {line}")
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
            continue  # forge was acquired at init; build needs sessions (CLI-2)
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
        "sessions: configured (ai-rfc run drives them once CLI-2 lands)"
        if given.sessions
        else "sessions: not configured (add a sessions: block to let ai-rfc "
        "run drive model sessions)"
    )
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc run``."""
    parser.description = (
        "Perform every deterministic stage that is next; stop at the agent "
        "boundary with the ledger printed. The stages: tuning a recon.yaml "
        "validates is not wired into the stage builders yet."
    )
    add_config_argument(parser)
    parser.add_argument(
        "--until",
        choices=_walkable(),
        default=None,
        help="Stop after this stage.",
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
        0 at the boundary, a stage's own exit code when one failed, 1 on a
        refusal.
    """
    try:
        return run_stages(config_path_from(args), until=args.until)
    except (LifecycleError, ConfigError, ledger.LedgerError, OSError) as error:
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
