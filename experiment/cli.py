"""The ``python -m experiment`` command-line surface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import DEFAULT_MODEL, EFFORTS, ExperimentError
from .arms import ARMS
from .paths import default_root
from .profile import init_profile, login_command
from .workspace import TARGETS, TEMPLATE_COMMIT, TEMPLATE_URL
from .workspace import prepare as prepare_workspace


def _report(message: str) -> None:
    print(message, file=sys.stderr)


def _arms(value: str) -> tuple[str, ...]:
    """Parse and validate a comma-separated arm list.

    Validating here rather than in ``init_campaign`` matters because campaign
    init runs the parity suite first: a typo caught at parse time costs an
    argparse error, and a typo caught later costs that whole suite's runtime.

    Args:
        value: The raw ``--arms`` string, e.g. ``"A,C"``.

    Returns:
        The parsed arms, in the order given.

    Raises:
        argparse.ArgumentTypeError: If the list is empty, repeats an arm, or
            names one the harness does not define.
    """
    arms = tuple(part.strip() for part in value.split(",") if part.strip())
    if not arms:
        raise argparse.ArgumentTypeError("no arms given")
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown arm(s) {', '.join(unknown)}; known arms are " f"{', '.join(ARMS)}"
        )
    if len(set(arms)) != len(arms):
        raise argparse.ArgumentTypeError(f"repeated arm in {value!r}")
    return arms


def _model(value: str) -> str:
    """Reject an empty model id without pinning the set of valid ones.

    A ``choices=`` list here would lock the harness out of every model released
    after this file was written, which is the opposite of what the flag is for.

    Args:
        value: The raw ``--model`` string.

    Returns:
        The stripped model id.

    Raises:
        argparse.ArgumentTypeError: If it is blank.
    """
    model = value.strip()
    if not model:
        raise argparse.ArgumentTypeError("model id cannot be empty")
    return model


def _default_plugin_dir() -> Path:
    """The ai-rfc plugin beside this package."""
    return Path(__file__).resolve().parents[1] / "plugins" / "ai-rfc"


def _run_parity(plugin_dir: Path, python: str) -> dict:
    """Run the server parity suite; the protocol's stop-ship construct check.

    Args:
        plugin_dir: The ai-rfc plugin root.
        python: Interpreter to run pytest with.

    Returns:
        Whether it passed and pytest's last line.
    """
    env = {**os.environ, "SSLKEYLOGFILE": ""}
    completed = subprocess.run(
        [python, "-m", "pytest", "-q", "tests/test_parity.py"],
        cwd=plugin_dir / "server",
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return {
        "passed": completed.returncode == 0,
        "summary": lines[-1] if lines else completed.stderr[-200:],
    }


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Runs root (default: ARFC_EXPERIMENTS_ROOT or ~/arfc-experiments).",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiment",
        description="AI+MCP vs AI+CLI experiment harness over the ai_rfc plugin.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profile_cmd = commands.add_parser("profile", help="Isolated Claude Code profile.")
    profile_verbs = profile_cmd.add_subparsers(dest="verb", required=True)
    profile_init = profile_verbs.add_parser("init", help="Create the profile dir.")
    _add_root(profile_init)

    spike = commands.add_parser("spike", help="S0: prove the profile is hermetic.")
    _add_root(spike)
    spike.add_argument(
        "--panther-repo",
        type=Path,
        required=True,
        help="Checkout supplying the a_rfc substrate the session may reach.",
    )
    spike.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )
    spike.add_argument(
        "--claude", default="claude", help="Claude Code binary to launch."
    )
    spike.add_argument(
        "--model",
        type=_model,
        default=DEFAULT_MODEL,
        help="Model id to launch against (default: %(default)s).",
    )
    spike.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds before the session's process group is killed.",
    )

    render = commands.add_parser("render", help="Regenerate the plugin SKILL.md.")
    render.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )

    workspace = commands.add_parser("workspace", help="Pristine workspaces.")
    workspace_verbs = workspace.add_subparsers(dest="verb", required=True)
    prepare = workspace_verbs.add_parser("prepare", help="Build a pristine workspace.")
    _add_root(prepare)
    prepare.add_argument(
        "target",
        choices=sorted(TARGETS),
        help="Reconstruction target; fixes the source workspace and window.",
    )
    prepare.add_argument(
        "--panther-repo",
        type=Path,
        required=True,
        help="Checkout holding the substrate and the source reconstruction.",
    )
    prepare.add_argument(
        "--template",
        default=TEMPLATE_URL,
        help="Internet-Draft template repository (default: %(default)s).",
    )
    prepare.add_argument(
        "--template-commit",
        default=TEMPLATE_COMMIT,
        help="Template commit to pin (default: %(default)s).",
    )

    campaign = commands.add_parser("campaign", help="Frozen run matrices.")
    campaign_verbs = campaign.add_subparsers(dest="verb", required=True)
    init = campaign_verbs.add_parser("init", help="Freeze a campaign.")
    _add_root(init)
    init.add_argument(
        "--id", required=True, help="Campaign identifier; names its directory."
    )
    init.add_argument(
        "--pristine", required=True, help="Name under <root>/pristine, or a path."
    )
    init.add_argument(
        "--arms",
        type=_arms,
        default="A,B,C",
        help=(
            "Comma-separated arms to run; one arm is a production run rather "
            f"than a comparison. Known arms: {', '.join(ARMS)}."
        ),
    )
    init.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="Runs per arm (default: %(default)s).",
    )
    init.add_argument(
        "--seed",
        type=int,
        default=20260826,
        help="Seed fixing the frozen run order (default: %(default)s).",
    )
    init.add_argument(
        "--model",
        type=_model,
        default=DEFAULT_MODEL,
        help="Model id every run launches against (default: %(default)s).",
    )
    init.add_argument(
        "--effort",
        choices=EFFORTS,
        default="high",
        help="Reasoning effort per launch (default: %(default)s).",
    )
    init.add_argument(
        "--budget",
        type=float,
        default=25.0,
        help=(
            "USD ceiling per run (default: %(default)s). Size it against the "
            "cluster count: a run killed on budget cannot be resumed in place."
        ),
    )
    init.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Seconds before a run's process group is killed (default: %(default)s).",
    )
    init.add_argument(
        "--panther-repo",
        type=Path,
        required=True,
        help="Checkout supplying the a_rfc substrate every run may reach.",
    )
    init.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )
    init.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter used for the parity suite (default: this one).",
    )
    init.add_argument(
        "--claude",
        default="claude",
        help="Claude Code binary; frozen as a resolved path, not a name.",
    )
    init.add_argument(
        "--session-mode",
        choices=("single", "per-cluster"),
        default="single",
        help=(
            "How a run is executed (default: %(default)s). single gives the "
            "whole window to one agent session; per-cluster spawns one per "
            "cluster, which over a long window avoids reasoning about late "
            "clusters from a compacted summary and makes a killed run resumable."
        ),
    )
    init.add_argument(
        "--skip-parity",
        action="store_true",
        help="Skip the parity suite. It is the protocol's stop-ship check.",
    )

    run = commands.add_parser("run", help="Launch pending runs in the frozen order.")
    run.add_argument("campaign", type=Path, help="Campaign directory.")
    run.add_argument("--only", default=None, help="Comma-separated run ids.")

    audit = commands.add_parser("audit", help="Audit every run's transcript.")
    audit.add_argument("campaign", type=Path, help="Campaign directory.")

    analyze = commands.add_parser(
        "analyze", help="Recompute outcomes; write aggregate.json and report.md."
    )
    analyze.add_argument("campaign", type=Path, help="Campaign directory.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one harness command.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        0 on success, 1 when the harness refused or an input was unusable.
    """
    args = _parser().parse_args(argv)
    root = args.root if getattr(args, "root", None) else default_root()
    try:
        if args.command == "profile" and args.verb == "init":
            profile_path = init_profile(root)
            print(f"profile: {profile_path}")
            print(f"log in once with:\n  {login_command(root)}")
        elif args.command == "spike":
            from .spike import run_spike

            plugin_dir = args.plugin_dir or _default_plugin_dir()
            report = run_spike(
                root=root,
                panther_repo=args.panther_repo.resolve(),
                plugin_dir=plugin_dir.resolve(),
                claude_bin=args.claude,
                model=args.model,
                timeout_s=args.timeout,
            )
            for check in report["checks"]:
                flag = "PASS" if check["passed"] else "FAIL"
                need = "required" if check["required"] else "product"
                print(f"{flag}  {check['check']:<14} ({need})")
            print(f"report: {root / 'spike-report.json'}")
            return 0 if report["go"] else 2
        elif args.command == "render":
            from .render import write_plugin_skill

            plugin_dir = args.plugin_dir or _default_plugin_dir()
            print(f"wrote {write_plugin_skill(plugin_dir.resolve())}")
        elif args.command == "workspace" and args.verb == "prepare":
            pristine = prepare_workspace(
                TARGETS[args.target],
                root=root,
                panther_repo=args.panther_repo.resolve(),
                template=args.template,
                template_commit=args.template_commit,
            )
            record = json.loads((pristine / "pristine.json").read_text())
            print(f"pristine: {pristine}")
            print(
                f"clusters: {record['cluster_count']}  "
                f"pre-seeded: {len(record['pre_seeded'])}  "
                f"window: {record['window']}"
            )
        elif args.command == "campaign" and args.verb == "init":
            from .config import CampaignConfig, init_campaign

            plugin_dir = (args.plugin_dir or _default_plugin_dir()).resolve()
            pristine = Path(args.pristine)
            if not pristine.is_absolute():
                pristine = root / "pristine" / args.pristine
            parity = None if args.skip_parity else _run_parity(plugin_dir, args.python)
            campaign = init_campaign(
                CampaignConfig(
                    root=root,
                    campaign_id=args.id,
                    pristine_dir=pristine,
                    arms=args.arms,
                    repeats=args.repeats,
                    seed=args.seed,
                    model=args.model,
                    effort=args.effort,
                    budget_usd=args.budget,
                    timeout_s=args.timeout,
                    panther_repo=args.panther_repo.resolve(),
                    plugin_root=plugin_dir,
                    python=args.python,
                    claude_bin=args.claude,
                    parity=parity,
                    session_mode=args.session_mode,
                )
            )
            print(f"campaign: {campaign.dir}")
            print(f"run order: {' '.join(campaign.run_order)}")
            print(f"parity: {parity}")
            if parity is not None and not parity["passed"]:
                _report("parity suite FAILED - stop-ship per protocol")
                return 2
        elif args.command == "run":
            from .config import load_campaign
            from .driver import launch_pending

            statuses = launch_pending(
                load_campaign(args.campaign.resolve()),
                only=args.only.split(",") if args.only else None,
                report=_report,
            )
            for status in statuses:
                print(
                    f"{status.run_id}: exit={status.exit_code} "
                    f"timed_out={status.timed_out}"
                )
            failed = [
                status.run_id
                for status in statuses
                if status.timed_out or status.exit_code != 0
            ]
            if failed:
                # Every run's outcome was printed and then discarded, so a
                # driver could not distinguish a campaign where nothing worked
                # from one where everything did.
                _report(f"error: {len(failed)} run(s) failed: {', '.join(failed)}")
                return 1
        elif args.command == "audit":
            from .audit import audit_campaign
            from .config import load_campaign

            audits = audit_campaign(load_campaign(args.campaign.resolve()))
            for run_id, audit in audits.items():
                print(
                    f"{run_id}: integrity={audit['integrity']} "
                    f"bypass={audit['bypass_attempts']['count']} "
                    f"errors={audit['errors']['class1']}/{audit['errors']['class2']}"
                )
        elif args.command == "analyze":
            from .audit import audit_campaign
            from .config import load_campaign
            from .metrics import analyze_campaign
            from .report import render_report

            campaign = load_campaign(args.campaign.resolve())
            audit_campaign(campaign)
            aggregate = analyze_campaign(campaign)
            report_path = campaign.analysis_dir / "report.md"
            report_path.write_text(render_report(aggregate))
            print(f"aggregate: {campaign.analysis_dir / 'aggregate.json'}")
            print(f"report: {report_path}")
    except (ExperimentError, OSError) as error:
        _report(f"error: {error}")
        return 1
    return 0
