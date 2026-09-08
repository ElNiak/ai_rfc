"""``ai-rfc toolchain provision|verify``: the Internet-Draft toolchain, once."""

from __future__ import annotations

import argparse
from pathlib import Path

from ... import __version__
from ...config import experiments_root
from ...toolchain import RECORD_FILE, TOOLS_DIR, ToolchainError, provision, verify
from ..common import report
from ..workspace import TEMPLATE_COMMIT, TEMPLATE_URL


def configure(parser: argparse.ArgumentParser) -> None:
    """Add the ``provision`` and ``verify`` verbs.

    Args:
        parser: The sub-parser the root door mounts this verb into, or this
            command's own standalone parser.
    """
    parser.description = (
        "Install once (networked) or re-check offline the template toolchain "
        "every build uses."
    )
    verbs = parser.add_subparsers(dest="verb", required=True)
    prov = verbs.add_parser("provision", help="Install it once (networked).")
    prov.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Experiments root (default: $AI_RFC_EXPERIMENTS_ROOT or "
            "~/ai-rfc-experiments)."
        ),
    )
    prov.add_argument(
        "--template",
        default=TEMPLATE_URL,
        help="Template repository (default: %(default)s).",
    )
    prov.add_argument(
        "--template-commit",
        default=TEMPLATE_COMMIT,
        help="Commit to pin (default: %(default)s).",
    )
    ver = verbs.add_parser("verify", help="Re-check it offline.")
    ver.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Experiments root holding tools/toolchain.json.",
    )
    ver.add_argument(
        "--record", type=Path, default=None, help="A toolchain.json elsewhere."
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.toolchain`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc toolchain")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc toolchain {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Perform the verb.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success; 1 when provisioning refused or ``verify`` said no, one
        reason per line.
    """
    root = args.root or experiments_root()
    if args.verb == "provision":
        try:
            record = provision(
                root, template=args.template, template_commit=args.template_commit
            )
        except (ToolchainError, OSError) as error:
            report(f"error: {error}")
            return 1
        print(f"toolchain: {record}")
        return 0
    record = args.record or root / TOOLS_DIR / RECORD_FILE
    ok, reasons = verify(record)
    if ok:
        print("ok")
        return 0
    for reason in reasons:
        report(f"toolchain: {reason}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
