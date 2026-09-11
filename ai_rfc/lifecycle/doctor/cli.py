"""``ai-rfc doctor``: every environment question a run would fail on, asked once."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ... import __version__, toolchain
from ...config import (
    ConfigError,
    ConfigParseError,
    ReconConfig,
    experiments_root,
    load_config,
    profile_dir,
)
from ...toolchain import RECORD_FILE, TOOLS_DIR
from ..common import CONFIG_ENV, report, report_structured
from ..profile import login_command


@dataclass(frozen=True)
class Check:
    """One question and its answer.

    Attributes:
        name: The question, as the report and the JSON key both spell it.
        ok: Whether it was answered as a run needs it.
        severity: ``error`` fails doctor; ``warning`` and ``info`` do not.
        detail: What was found, in the operator's own paths.
        fix: What to do about it; printed only when the check is not ok.
    """

    name: str
    ok: bool
    severity: str
    detail: str
    fix: str = ""


def _claude(config: ReconConfig | None) -> Check:
    name = config.sessions.claude if config and config.sessions else "claude"
    resolved = shutil.which(name)
    if resolved is None:
        return Check(
            "claude",
            False,
            "error",
            f"{name!r} not found on PATH",
            "install Claude Code or set sessions.claude to its absolute path",
        )
    try:
        version = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, timeout=30
        )
        text = (version.stdout or version.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return Check(
            "claude", False, "error", f"{resolved}: {error}", "reinstall Claude Code"
        )
    return Check("claude", True, "info", f"{resolved}: {text}")


def _profile(config: ReconConfig | None) -> Check:
    """The isolated Claude Code profile sessions launch against.

    Reports the directory; never creates it. A diagnostic that fixed what it
    found could not report a missing profile at all — asking would have made
    the answer yes — and would write to the filesystem on every run.

    ``login_command`` takes the experiments ROOT and derives ``root /
    "profile"`` itself, so a configured profile is only expressible when it is
    named ``profile`` inside its parent.
    """
    configured = (
        config.sessions.profile
        if config and config.sessions and config.sessions.profile
        else None
    )
    directory = configured or profile_dir(experiments_root())
    if directory.name != "profile":
        return Check(
            "profile",
            False,
            "warning",
            f"sessions.profile is {directory}; the harness derives "
            "<root>/profile, so nothing here reads this path",
            f"name it 'profile' under its parent ({directory.parent / 'profile'})",
        )
    root = directory.parent
    login = login_command(root)
    if not directory.is_dir():
        return Check(
            "profile",
            False,
            "warning",
            f"no profile at {directory}; model sessions would launch "
            "unauthenticated (the deterministic stages do not need one)",
            f"mkdir -p {directory}, then log in once with: {login}",
        )
    return Check(
        "profile", True, "info", f"{directory} is present; log in once with: {login}"
    )


def _toolchain(config: ReconConfig | None) -> Check:
    record = (
        config.toolchain
        if config and config.toolchain
        else experiments_root() / TOOLS_DIR / RECORD_FILE
    )
    if not record.exists():
        return Check(
            "toolchain",
            False,
            "warning",
            f"no record at {record}; draft builds will be skipped",
            "ai-rfc toolchain provision",
        )
    ok, reasons = toolchain.verify(record)
    if ok:
        return Check("toolchain", True, "info", f"{record} verifies")
    return Check(
        "toolchain",
        False,
        "error",
        "; ".join(reasons),
        "ai-rfc toolchain provision into a fresh root, or repair the recorded paths",
    )


def _token(config: ReconConfig | None) -> Check:
    if config is None or config.source.host == "none" or not config.source.token_env:
        return Check("token", True, "info", "no forge token needed")
    if os.environ.get(config.source.token_env):
        return Check("token", True, "info", f"{config.source.token_env} is set")
    return Check(
        "token",
        False,
        "warning",
        f"{config.source.token_env} is not set; the forge is fetched "
        "anonymously at lower fidelity",
        f"export {config.source.token_env}=…",
    )


def _deps() -> Check:
    missing = []
    for module in ("yaml", "mcp"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        return Check(
            "deps",
            False,
            "error",
            "missing: " + ", ".join(missing),
            "pip install -e $AIRFC",
        )
    return Check("deps", True, "info", "yaml, mcp importable")


def _workspace(config: ReconConfig | None) -> Check:
    if config is None:
        return Check(
            "workspace", True, "info", "no config given; workspace not checked"
        )
    for ancestor in (config.workspace, *config.workspace.parents):
        if (ancestor / "CLAUDE.md").exists() or (ancestor / ".claude").is_dir():
            return Check(
                "workspace",
                False,
                "warning",
                f"{ancestor} holds a CLAUDE.md or .claude/; sessions run with "
                "project settings from the workspace and would load it",
                "move the workspace outside every CLAUDE.md ancestry (the "
                "experiments root exists for this)",
            )
    return Check(
        "workspace", True, "info", f"{config.workspace} has no CLAUDE.md ancestor"
    )


def _guarded(name: str, question: Callable[[], Check]) -> Check:
    """Run one check, turning a failure of the check itself into its answer.

    ``doctor`` is what an operator runs when the environment is already
    broken, so a check that raises is the one outcome it must not have: a
    read-only root or an unreadable refcache would otherwise reach them as a
    stack trace instead of the line naming which question could not be asked.

    Args:
        name: The check's name, so a failure stays attributable to it.
        question: The check to run.

    Returns:
        The check's own answer, or an error-severity one naming the failure.
    """
    try:
        return question()
    except OSError as error:
        return Check(
            name,
            False,
            "error",
            f"the check itself failed: {error}",
            "fix the filesystem error above, then re-run ai-rfc doctor",
        )


def checks(config: ReconConfig | None) -> list[Check]:
    """Every check, in the order they are printed.

    Args:
        config: The reconstruction to check against, or ``None`` for the
            questions that do not need one.

    Returns:
        One :class:`Check` per question. Never raises ``OSError``: a check
        that cannot complete answers with its own failure.
    """
    return [
        _guarded("claude", lambda: _claude(config)),
        _guarded("profile", lambda: _profile(config)),
        _guarded("toolchain", lambda: _toolchain(config)),
        _guarded("token", lambda: _token(config)),
        _guarded("deps", _deps),
        _guarded("workspace", lambda: _workspace(config)),
    ]


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc doctor``.

    Args:
        parser: The sub-parser the root door mounts this verb into, or this
            command's own standalone parser.
    """
    parser.description = (
        "Check the environment a reconstruction runs in; exit 1 only for what "
        "a run cannot survive."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            f"recon.yaml to check against (default: ${CONFIG_ENV}, else "
            "generic checks)."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Machine-readable output."
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.doctor`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc doctor")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc doctor {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Print every check.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        1 when any error-severity check failed, else 0. A warning names
        something worth fixing that a run survives, so it does not.
    """
    config = None
    config_path = args.config or (
        Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else None
    )
    if config_path is not None:
        try:
            config = load_config(config_path)
        except ConfigParseError as error:
            report_structured(f"error: {error}")
            return 1
        except ConfigError as error:
            report(f"error: {error}")
            return 1
    results = checks(config)
    if args.as_json:
        print(json.dumps({"checks": [asdict(c) for c in results]}, indent=2))
    else:
        for check in results:
            status = "ok" if check.ok else check.severity
            line = f"{check.name}: {status} — {check.detail}"
            if check.fix and not check.ok:
                line += f" (fix: {check.fix})"
            print(line)
    return 1 if any(not c.ok and c.severity == "error" for c in results) else 0


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
