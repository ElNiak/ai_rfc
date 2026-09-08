"""The one door onto the tool: ``ai-rfc <verb> [args]``.

One argparse tree mounts every registered command through its ``configure``
function, so ``ai-rfc <verb>`` and ``python -m ai_rfc.<sub>`` parse the same
arguments and return the same exit codes, and ``panther ai-rfc`` forwards argv
here untouched.
"""

from __future__ import annotations

import argparse
from importlib import import_module

from . import __version__
from .entrypoints import ENTRY_POINTS, SECTIONS

PROG = "ai-rfc"

#: Written rather than derived. argparse would list every verb inline in the
#: usage line, which the sectioned epilog already does properly; and
#: ``panther ai-rfc --help`` is asserted to open with this exact line.
USAGE = "%(prog)s <verb> [args]\n       %(prog)s --help | --version"


def _epilog() -> str:
    """Render the verb table, grouped under the headings in declared order.

    Returns:
        One block per non-empty section: its heading, then one aligned row per
        command registered under it.
    """
    width = max(len(entry.verb) for entry in ENTRY_POINTS)
    lines: list[str] = []
    for section in SECTIONS:
        rows = [entry for entry in ENTRY_POINTS if entry.section == section]
        if not rows:
            continue
        lines.append(f"{section}:")
        lines.extend(f"  {entry.verb:<{width}}  {entry.summary}" for entry in rows)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the root parser with every registered verb mounted.

    Returns:
        A parser whose subcommands are the registry's, each configured by its
        own module so the two doors cannot disagree about an argument.
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        usage=USAGE,
        description="Reconstruct a specification from a repository's history.",
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    verbs = parser.add_subparsers(
        dest="verb",
        required=True,
        metavar="<verb>",
        help="One of the commands listed below.",
    )
    for entry in ENTRY_POINTS:
        module = import_module(entry.module)
        # `prog` is load-bearing, not cosmetic: with an explicit `usage` above,
        # argparse would otherwise derive every sub-parser's prog from that
        # two-line string.
        sub = verbs.add_parser(entry.verb, prog=entry.prog)
        module.configure(sub)
        sub.set_defaults(_run=module.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse and run one verb.

    Args:
        argv: Argument vector without the program name; ``None`` reads
            ``sys.argv[1:]``.

    Returns:
        The verb's exit code (0/1/3 per the package table; argparse exits 2
        itself, and raises ``SystemExit`` for ``--help`` and ``--version``).
    """
    args = build_parser().parse_args(argv)
    return int(args._run(args))
