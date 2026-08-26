"""The ``python -m experiment`` command-line surface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ExperimentError
from .paths import default_root
from .profile import init_profile, login_command


def _report(message: str) -> None:
    print(message, file=sys.stderr)


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

            plugin_dir = (
                args.plugin_dir
                or Path(__file__).resolve().parents[1] / "plugins" / "ai-rfc"
            )
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
    except (ExperimentError, OSError) as error:
        _report(f"error: {error}")
        return 1
    return 0
