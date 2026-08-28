"""The ``python -m experiment`` command-line surface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import ExperimentError
from .paths import default_root
from .profile import init_profile, login_command
from .workspace import TARGETS, TEMPLATE_COMMIT, TEMPLATE_URL
from .workspace import prepare as prepare_workspace


def _report(message: str) -> None:
    print(message, file=sys.stderr)


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

    profile = commands.add_parser("profile", help="Isolated Claude Code profile.")
    profile_verbs = profile.add_subparsers(dest="verb", required=True)
    profile_init = profile_verbs.add_parser("init", help="Create the profile dir.")
    _add_root(profile_init)

    spike = commands.add_parser("spike", help="S0: prove the profile is hermetic.")
    _add_root(spike)
    spike.add_argument("--panther-repo", type=Path, required=True)
    spike.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )
    spike.add_argument("--claude", default="claude")
    spike.add_argument("--model", default="claude-opus-5")
    spike.add_argument("--timeout", type=int, default=300)

    render = commands.add_parser("render", help="Regenerate the plugin SKILL.md.")
    render.add_argument("--plugin-dir", type=Path, default=None)

    workspace = commands.add_parser("workspace", help="Pristine workspaces.")
    workspace_verbs = workspace.add_subparsers(dest="verb", required=True)
    prepare = workspace_verbs.add_parser("prepare", help="Build a pristine workspace.")
    _add_root(prepare)
    prepare.add_argument("target", choices=sorted(TARGETS))
    prepare.add_argument("--panther-repo", type=Path, required=True)
    prepare.add_argument("--template", default=TEMPLATE_URL)
    prepare.add_argument("--template-commit", default=TEMPLATE_COMMIT)

    campaign = commands.add_parser("campaign", help="Frozen run matrices.")
    campaign_verbs = campaign.add_subparsers(dest="verb", required=True)
    init = campaign_verbs.add_parser("init", help="Freeze a campaign.")
    _add_root(init)
    init.add_argument("--id", required=True)
    init.add_argument(
        "--pristine", required=True, help="Name under <root>/pristine, or a path."
    )
    init.add_argument("--arms", default="A,B,C")
    init.add_argument("--repeats", type=int, default=2)
    init.add_argument("--seed", type=int, default=20260826)
    init.add_argument("--model", default="claude-opus-5")
    init.add_argument("--effort", default="high")
    init.add_argument("--budget", type=float, default=25.0)
    init.add_argument("--timeout", type=int, default=7200)
    init.add_argument("--panther-repo", type=Path, required=True)
    init.add_argument("--plugin-dir", type=Path, default=None)
    init.add_argument("--python", default=sys.executable)
    init.add_argument("--claude", default="claude")
    init.add_argument("--skip-parity", action="store_true")

    run = commands.add_parser("run", help="Launch pending runs in the frozen order.")
    run.add_argument("campaign", type=Path)
    run.add_argument("--only", default=None, help="Comma-separated run ids.")

    audit = commands.add_parser("audit", help="Audit every run's transcript.")
    audit.add_argument("campaign", type=Path)

    analyze = commands.add_parser(
        "analyze", help="Recompute outcomes; write aggregate.json and report.md."
    )
    analyze.add_argument("campaign", type=Path)

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
            profile = init_profile(root)
            print(f"profile: {profile}")
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
            from .config import init_campaign

            plugin_dir = (args.plugin_dir or _default_plugin_dir()).resolve()
            pristine = Path(args.pristine)
            if not pristine.is_absolute():
                pristine = root / "pristine" / args.pristine
            parity = None if args.skip_parity else _run_parity(plugin_dir, args.python)
            campaign = init_campaign(
                root=root,
                campaign_id=args.id,
                pristine_dir=pristine,
                arms=tuple(args.arms.split(",")),
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
            )
            print(f"campaign: {campaign.dir}")
            print(f"run order: {' '.join(campaign.run_order)}")
            print(f"parity: {parity}")
            if parity is not None and not parity["passed"]:
                _report("parity suite FAILED - stop-ship per protocol")
                return 2
        elif args.command == "run":
            from .config import load_campaign
            from .matrix import execute

            statuses = execute(
                load_campaign(args.campaign.resolve()),
                only=args.only.split(",") if args.only else None,
                report=_report,
            )
            for status in statuses:
                print(
                    f"{status.run_id}: exit={status.exit_code} "
                    f"timed_out={status.timed_out}"
                )
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
