"""``ai-rfc verify --config``: every gate the workspace can pass, in one command."""

from __future__ import annotations

import argparse
from pathlib import Path

from ... import __version__, ledger
from ...config import ConfigError, ConfigParseError
from ...draft import cli as draft_cli
from ...pipeline.run import perform
from ...pipeline.stages import BY_NAME
from .. import LifecycleError
from ..common import (
    add_config_argument,
    config_path_from,
    load_pair,
    report,
    report_structured,
)

NO_TIMELINE = "skipped (no timeline yet; run ai-rfc run first)"
NO_TOOLCHAIN = "skipped (no toolchain record; see ai-rfc doctor)"


def _outcome(code: int) -> str:
    """How one check's exit code reads in the report."""
    if code == 0:
        return "ok"
    if code == 3:
        return "findings"
    return f"error ({code})"


def verify(config_path: Path, *, strict: bool) -> int:
    """Run drift, strict check, strict gate, completeness, lint and build.

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
    if any(code not in (0, 3) for code in codes):
        return 1
    if 3 in codes:
        return 3 if strict else 0
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc verify``."""
    parser.description = (
        "Config drift, strict manifest check, citation gate, completeness, "
        "lint and build, in one exit code. A check whose inputs do not exist "
        "yet is skipped and contributes no exit code, so 0 means nothing that "
        "ran failed, not that everything ran; the last line tallies how many "
        "checks ran and names every one that was skipped."
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
    parser = argparse.ArgumentParser(prog="ai-rfc verify")
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
    except ConfigParseError as error:
        report_structured(f"error: {error}")
        return 1
    except (LifecycleError, ConfigError, OSError) as error:
        report(f"error: {error}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.verify``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
