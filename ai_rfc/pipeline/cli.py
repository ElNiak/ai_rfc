"""Command-line entry point for the deterministic pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ai_rfc import __version__
from ai_rfc.driver import printable
from ai_rfc.parser import Parser

from ..ledger import LedgerError
from .run import PipelineError, perform, workspace_from
from .stages import BY_NAME, OPTIONAL, STAGES, Performer, is_optional
from .state import State, draft_head, next_stage, state
from .substrate import check
from .workspace import Workspace


def _report(message: str) -> None:
    """Write a diagnostic to stderr, as one printable line.

    Deliberately not the ``logging`` module. Every ``panther.*`` logger is
    configured with ``propagate=False`` and a handler admitting only ``ERROR``,
    so a logged warning here is discarded before anyone sees it.

    Escaped here rather than at each call site, which is the shape
    :func:`ai_rfc.lifecycle.common.report` already gives the lifecycle verbs
    and ``draft/cli.py``: every caller interpolates an operator- or
    agent-controlled value into these lines — a repository path, a cluster
    id, the text of a caught error carrying ``git``'s own stderr — and one
    carrying a line break forges a second line beneath the first.
    :func:`~ai_rfc.driver.printable` is the predicate over the unprintable
    category, so the escape is not an enumeration of line endings. The
    substrate may not import ``lifecycle``, which is why this is that
    boundary's twin rather than a call to it.
    """
    print(printable(message), file=sys.stderr)


def _refuse_argparse_two(stage_name: str) -> int:
    """Report a stage's exit 2 as the defect it is, and return 1.

    ``perform`` builds every stage's argv itself (``run.py``'s ``DISPATCH``),
    so a 2 coming back says the argv *this* command composed was malformed — a
    defect here, not something the operator typed. Propagating it would hand a
    caller a 2 about an invocation nobody made. Both places this door performs
    a stage route through here, so the two doors onto the walk cannot disagree.

    Args:
        stage_name: The stage that exited 2, named in the diagnostic.

    Returns:
        Always 1 — the command could not complete. The caller prints no line of
        its own for this code, so one failure is one ``error:`` line.
    """
    _report(
        f"error: stage {stage_name} exited 2, which belongs to argparse "
        f"alone; the argv this command built for it is malformed"
    )
    return 1


def configure(parser: argparse.ArgumentParser) -> None:
    """Add this command's arguments to ``parser``.

    Args:
        parser: Either the root's subparser for this command or the standalone
            parser :func:`build_standalone_parser` builds; both must carry the
            same arguments, so both are configured here.
    """
    parser.description = (
        "Run the deterministic stages of a reconstruction in order, "
        "stopping wherever a person or a model has to act."
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    status = verbs.add_parser("status", help="Report every stage's state.")
    status.add_argument("workspace", type=Path, help="The workspace root.")
    status.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit pipeline-status.json to stdout instead of a table.",
    )

    substrate = verbs.add_parser(
        "substrate",
        help="Check that the pinned clone can carry a reconstruction.",
    )
    substrate.add_argument("workspace", type=Path, help="The workspace root.")

    run = verbs.add_parser("run", help="Perform the deterministic stages.")
    run.add_argument("workspace", type=Path, help="The workspace root.")
    run.add_argument(
        "--from",
        dest="start",
        choices=[stage.name for stage in STAGES],
        default=None,
        help=(
            "First stage to run; default is wherever the workspace stands. "
            "The re-derivable checks (check, gate, lint) run only inside a "
            "range given explicitly with --from/--until."
        ),
    )
    run.add_argument(
        "--until",
        dest="until",
        choices=[stage.name for stage in STAGES],
        default=None,
        help=(
            "Last stage to run; default is the next agent boundary. The "
            "re-derivable checks (check, gate, lint) run only inside a "
            "range given explicitly with --from/--until."
        ),
    )
    run.add_argument(
        "--forge-url", default=None, help="Repository URL, for the forge stage."
    )
    run.add_argument(
        "--host",
        choices=("github", "gitlab"),
        default=None,
        help="Forge kind; pass it for any self-hosted instance.",
    )
    run.add_argument(
        "--cluster", default=None, help="Cluster id, for the checkpoint stage."
    )
    run.add_argument(
        "--toolchain",
        type=Path,
        default=(
            Path(os.environ["AI_RFC_TOOLCHAIN"])
            if os.environ.get("AI_RFC_TOOLCHAIN")
            else None
        ),
        help=(
            "toolchain.json for the build stage (default: $AI_RFC_TOOLCHAIN); "
            "without it, build is skipped."
        ),
    )
    run.add_argument(
        "--strict",
        action="store_true",
        help="Exit 3 on findings rather than only reporting them, in the "
        "stages that accept it.",
    )
    run.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a machine-readable record of the run to stdout.",
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.pipeline`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = Parser(prog="ai-rfc pipeline")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc pipeline {__version__}"
    )
    configure(parser)
    return parser


def status_payload(workspace: Path) -> dict:
    """Every stage's state and the next action, as ``pipeline-status.json``.

    Public because ``ai-rfc status`` prints this same body under a config's
    own layout; a second derivation there would be free to disagree with the
    one a driver reads.

    Args:
        workspace: The workspace root, recorded verbatim under ``workspace``.

    Returns:
        The status payload: ``workspace``, one ``stages`` entry per stage, and
        ``next_action``.
    """
    ws = workspace_from(workspace)
    action = next_stage(ws)
    return {
        "workspace": str(workspace),
        "stages": [
            {
                "ordinal": entry.stage.ordinal,
                "name": entry.stage.name,
                "kind": entry.stage.performer.value,
                "state": entry.state.value,
                "reason": entry.reason,
            }
            for entry in state(ws)
        ],
        # Recorded key. The Python name is `next_stage` — "action" was unbound
        # and it returns a Stage — but `pipeline-status.json` is what a driver
        # reads, so the key keeps the word it was published under.
        "next_action": (
            None
            if action is None
            else {
                "stage": action.stage.name,
                "kind": action.stage.performer.value,
                "state": action.state.value,
                "reason": action.reason,
                "instruction": action.stage.instruction,
            }
        ),
    }


def print_status(payload: dict) -> None:
    """Print the stage table and the next action.

    Args:
        payload: A body from :func:`status_payload`; extra keys are ignored,
            so a caller may print its own lines beneath this one.
    """
    for entry in payload["stages"]:
        line = f"{entry['ordinal']}  {entry['name']:<12} {entry['state']}"
        if entry["reason"]:
            line += f" — {entry['reason']}"
        print(line)
    action = payload["next_action"]
    print()
    if action is None:
        print("next: nothing outstanding")
        return
    print(f"next: {action['stage']} ({action['kind']}) — {action['reason']}")
    if action["instruction"]:
        print(f"      {action['instruction']}")


def _run(args: argparse.Namespace) -> int:
    ws = workspace_from(args.workspace)
    # Captured before `start` is possibly overwritten by the walk's derived
    # start below: `_perform_rederivable` needs the caller's own `--from`
    # ordinal, not wherever the walk ended up beginning.
    explicit_start = BY_NAME[args.start].ordinal if args.start is not None else None
    start = explicit_start
    until = BY_NAME[args.until].ordinal if args.until else None

    if start is None:
        # A flag makes its optional stage outstanding again (Ruling R9): a
        # bare `run --toolchain X` on an otherwise-finished workspace must
        # still reach a pending or stale `build`, which `next_stage` would
        # otherwise always step over regardless of its state.
        enabled = {
            name
            for name, flag in OPTIONAL.items()
            if getattr(args, flag.lstrip("-").replace("-", "_")) is not None
        }
        action = next_stage(ws, enabled=enabled)
        if action is None:
            # Nothing left for the walk to do, but the re-derivable checks
            # below are a separate question from the walk, so this is an
            # empty walk rather than an early return: setting `start` past
            # every ordinal makes the loop below a no-op while still falling
            # through to `_perform_rederivable` and `_finish`.
            _report("note: nothing outstanding")
            start = STAGES[-1].ordinal + 1
        else:
            start = action.stage.ordinal

    performed: list[dict] = []
    halted_at: str | None = None
    code = 0
    for stage in STAGES:
        if stage.ordinal < start:
            continue
        if until is not None and stage.ordinal > until:
            break
        if is_optional(stage):
            # Skipping without the stage's flag matches how `state` steps over
            # it (`is_optional`/`OPTIONAL` is the one rule both read), and the
            # two disagreeing is what asking for the stage explicitly (below)
            # exists to prevent.
            flag = OPTIONAL[stage.name]
            given = getattr(args, flag.lstrip("-").replace("-", "_"))
            if given is None:
                if args.start == stage.name:
                    _report(
                        f"error: {stage.name} was asked for but no {flag} was given"
                    )
                    return 1
                _report(f"note: skipping {stage.name}; no {flag} given")
                continue
        if stage.performer is not Performer.DETERMINISTIC:
            # Reaching a boundary is the pipeline working, not failing: the
            # deterministic half is done and the next move is somebody else's.
            _report(
                f"boundary: stage {stage.ordinal} ({stage.name}) is "
                f"{stage.performer.value}; stopping here."
            )
            _report(f"next: {stage.instruction}")
            halted_at = stage.name
            break
        result = perform(
            stage,
            ws,
            strict=args.strict,
            cluster=args.cluster,
            forge_url=args.forge_url,
            host=args.host,
            toolchain=args.toolchain,
        )
        performed.append(
            {
                "stage": stage.name,
                "exit_code": result.exit_code,
                "argv": list(result.argv),
            }
        )
        if not result.ok:
            halted_at = stage.name
            if result.exit_code == 2:
                code = _refuse_argparse_two(stage.name)
            else:
                _report(f"error: {stage.name} exited {result.exit_code}")
                code = result.exit_code
            break

    rederived = _perform_rederivable(args, ws, performed, until, explicit_start)
    if code == 0:
        code = rederived
    return _finish(args, performed, halted_at=halted_at, code=code)


def _perform_rederivable(
    args: argparse.Namespace,
    ws: Workspace,
    performed: list[dict],
    until: int | None,
    explicit_start: int | None,
) -> int:
    """Run the checks the stage walk cannot reach, and return the worst exit code.

    ``check``, ``gate`` and ``lint`` are the re-derivable stages: none records
    doneness and none mutates the workspace beyond its own report, so all
    three are safe to run whenever their inputs exist. The walk cannot reach
    them reliably — ``check`` sits at ordinal 6 but the agent boundary
    ``prose`` at 7 ends the walk, and ``gate`` at 9 and ``lint`` at 10 both
    need the draft ``prose`` produces — so they are performed by state
    instead, after the walk. This includes a walk
    that performed nothing at all: ``_run`` no longer returns early when
    ``next_stage`` reports nothing outstanding, so a finished workspace still
    reaches this function.

    Three rules keep this from doing more than the caller asked for. A stage
    already recorded in ``performed`` is skipped — the walk already ran it
    this invocation (``--from check`` puts ``check`` on the walk directly),
    and running it again would duplicate the record without changing the
    exit code, since both stages are idempotent. When ``until`` bounds the
    run, a re-derivable stage past that ordinal is skipped too, so
    ``--until``'s contract holds for the whole command and not only for the
    walk that preceded this call. And when ``explicit_start`` bounds the run
    — the caller gave an explicit ``--from`` — a re-derivable stage before
    that ordinal is skipped the same way; it is deliberately the flag's own
    ordinal and not the walk's derived start, because on a workspace with no
    ``--from`` the walk itself starts at ``prose`` (the next agent boundary)
    while ``check`` at ordinal 6 must still run — bounding on the derived
    start would skip exactly the default-path check this function exists to
    perform.

    Args:
        args: The parsed arguments; ``strict`` decides whether findings exit 3.
        ws: The workspace to check.
        performed: The record the walk appended to; extended in place, and
            read to skip a stage the walk already performed.
        until: The last stage's ordinal the caller bounded the run to via
            ``--until``, or ``None`` when the caller gave none.
        explicit_start: The first stage's ordinal the caller bounded the run
            to via ``--from``, or ``None`` when the caller gave none.

    Returns:
        The highest exit code any check returned, or 0.
    """
    already = {entry["stage"] for entry in performed}
    states = {entry.stage.name: entry.state for entry in state(ws)}
    worst = 0
    for name in ("check", "gate", "lint"):
        if name in already:
            continue
        if explicit_start is not None and BY_NAME[name].ordinal < explicit_start:
            continue
        if until is not None and BY_NAME[name].ordinal > until:
            continue
        if states.get(name) is not State.RECOMPUTED:
            continue
        if name == "gate" and not (
            states.get("prose") is State.DONE and ws.questions.exists()
        ):
            # `_prose` (state.py) grades doneness from the draft repository
            # and revisions.yaml alone; no stage this walk performs ever
            # writes questions.yaml, so the register's existence is checked
            # here directly rather than assumed from `prose`'s state.
            continue
        if name == "lint" and draft_head(ws) is None:
            # `_prose` grades doneness from the draft repository and
            # revisions.yaml alone, not from a commit existing, so a draft
            # repository that was only `git init`ed reads DONE while
            # `draft lint`'s default `--ref HEAD` has nothing to read.
            continue
        result = perform(BY_NAME[name], ws, strict=args.strict, cluster=args.cluster)
        performed.append(
            {
                "stage": name,
                "exit_code": result.exit_code,
                "argv": list(result.argv),
            }
        )
        # Refused before the ranking, not after: `max` would let a 2 outrank a
        # sibling's 1 and leave the caller reading "you mistyped the command"
        # for an argv nobody typed.
        code = _refuse_argparse_two(name) if result.exit_code == 2 else result.exit_code
        worst = max(worst, code)
    return worst


def _finish(
    args: argparse.Namespace,
    performed: list[dict],
    *,
    halted_at: str | None,
    code: int = 0,
) -> int:
    if args.as_json:
        print(
            json.dumps(
                {
                    "workspace": str(args.workspace),
                    "performed": performed,
                    "halted_at": halted_at,
                },
                indent=2,
            )
        )
    return code


def run(args: argparse.Namespace) -> int:
    """Report or advance a reconstruction workspace.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, including when the run stops at a stage a person or a
        model must perform — reaching a boundary is the pipeline working —
        unless a re-derived ``check`` or ``gate`` still finds something under
        ``--strict``, since those run whether or not the walk reached them.
        1 if the workspace could not be read or a stage was asked for that
        this command does not perform — the two shapes of "could not
        complete". 3 when ``substrate`` completed and found a problem, which
        is a finding about the clone rather than a failure to look. Otherwise
        a stage's own exit code, so a strict gate's 3 reaches the caller
        unchanged. 2 is left to argparse.
    """
    try:
        if args.verb == "status":
            payload = status_payload(args.workspace)
            if args.as_json:
                print(json.dumps(payload, indent=2))
            else:
                print_status(payload)
            return 0
        if args.verb == "substrate":
            problems = check(Workspace(root=args.workspace).clone)
            for problem in problems:
                _report(f"error: {problem}")
            return 3 if problems else 0
        return _run(args)
    except (PipelineError, LedgerError, OSError) as error:
        _report(f"error: {error}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.pipeline``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
