"""``ai-rfc next --config``: perform exactly one action, then stop.

``run``'s one-step form. Spec §5 gives it in one sentence — "``next`` performs
one row" — and the row is a row of the *sweep's* state machine, so this verb
is :func:`ai_rfc.driver.sweep.run` with ``mode="one"`` and nothing else. It
does not walk the deterministic stages itself: ``history``, ``timeline`` and
``views`` are row 2 of that same table (``sweep.SWEPT_STAGES`` is exactly
``run``'s ``_walkable()``), so a walk here would perform three actions before
asking the sweep for one.

``--until`` and ``--retry`` come from ``run``'s parser rather than from a
second copy: one grammar, declared once, because two parsers for one grammar
is how they drift.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ... import __version__, ledger
from ...config import ConfigError, ConfigParseError
from ...driver import DriverError, sweep
from .. import LifecycleError
from ..common import (
    add_config_argument,
    config_path_from,
    load_sealed,
    report,
    report_structured,
)
from ..run.cli import BOUNDARY, add_sweep_arguments


def run_one(
    config_path: Path, *, until: str | None = None, retry: str | None = None
) -> int:
    """Perform one row of the sweep's state machine, then return.

    Args:
        config_path: The operator's ``recon.yaml``.
        until: A bound the sweep resolves before it plans anything: a stage
            name, ``cluster:<id>`` or ``ordinal:<n>``. A bound already
            satisfied stops the invocation having performed nothing, which is
            the bound doing its job rather than a failure.
        retry: A cluster whose already-spent attempts this invocation
            forgives.

    Returns:
        0 when the action was performed or the bound was already reached, 1
        when the row the sweep planned was itself a stop, 3 when the action
        was the build gate and ``check --strict`` reported findings.

    Raises:
        LifecycleError: If the workspace is not initialised, an identity field
            drifted, or the configuration declares no sessions.
        ConfigError: If the configuration does not validate.
        ledger.LedgerError: If the workspace's progress cannot be read.
        DriverError: From anywhere in the sweep — a bound or a ``--retry``
            naming no cluster of this timeline, an unlaunchable binary, an
            unparseable transcript line.
    """
    given, _sealed, layout, noted = load_sealed(config_path)
    for line in noted:
        report(f"note: config drift: {line}")
    if given.sessions is None:
        # The refusal is this verb's own, not the sweep's, and the asymmetry
        # with `run` is deliberate. `run` without sessions has real work — the
        # deterministic stages — and stops at the agent boundary reporting 0,
        # which is what keeps a hand-mined workspace possible. `next` has no
        # row it could perform, and `sweep.run`'s own message ("no sessions
        # are configured") would read as a broken verb rather than as the
        # boundary it is. So it names `run` as the way to perform the
        # deterministic half.
        raise LifecycleError(
            "next performs one row of the sweep's state machine, and this "
            "workspace's config declares no sessions, so there is no sweep. "
            "Add a sessions: block, or perform the deterministic stages up "
            f"to the {BOUNDARY} boundary with: ai-rfc run --config "
            f"{config_path}"
        )
    # The configuration **as given**, not the sealed copy, for `run`'s reason:
    # a `sessions:` block added after `init` is noted drift rather than
    # refused (D57), so the seal does not carry it. `config_path` is the path
    # the operator typed, which is what the resume line prints back.
    return sweep.run(
        given,
        layout.root,
        mode="one",
        until=until,
        retry=retry,
        config_path=config_path,
    )


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc next``."""
    parser.description = (
        "Perform exactly one action — the next row of the same state machine "
        "run sweeps — then print the ledger and the line to type after it. "
        "Needs a sessions: block; without one, run walks the deterministic "
        "stages to the agent boundary."
    )
    add_config_argument(parser)
    add_sweep_arguments(parser)


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.next`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc next")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc next {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Perform the verb; 1 on a refusal or an unreadable input.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        The sweep's own code once it is driving, 1 on a refusal.
    """
    try:
        return run_one(config_path_from(args), until=args.until, retry=args.retry)
    except (ConfigParseError, ledger.LedgerParseError) as error:
        # Named before their bases, as in `run`: a YAML parser's block is the
        # one diagnostic here whose line breaks are its own and whose closing
        # caret marks a column. Every other refusal composes one record around
        # a value and must stay one line.
        report_structured(f"error: {error}")
        return 1
    except (
        LifecycleError,
        ConfigError,
        ledger.LedgerError,
        # `DriverError` is a `RuntimeError`, not a `LifecycleError`, and this
        # verb reaches the sweep on every successful path — so without it the
        # commonest refusal here (a `--retry` or a bound naming no cluster of
        # this timeline) would print a traceback where the others print a
        # line.
        DriverError,
        OSError,
    ) as error:
        report(f"error: {error}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.next``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
