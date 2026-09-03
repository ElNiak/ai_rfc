"""The one door onto the tool: ``ai-rfc <verb> [args]``.

Every verb forwards its arguments untouched to the sub-CLI the registry names
and returns whatever that sub-CLI returns, so this door and
``python -m ai_rfc.<sub>`` cannot disagree about behaviour or exit codes.

Interim by design: the argparse root that SP3 builds replaces this module and
retires the leaf programs. It exists so the extraction ships one working door
without redesigning the verbs.
"""

from __future__ import annotations

import sys

from . import __version__
from .entrypoints import ENTRY_POINTS

PROG = "ai-rfc"
_BY_VERB = {entry.verb: entry for entry in ENTRY_POINTS}


def _usage() -> str:
    width = max(len(entry.verb) for entry in ENTRY_POINTS)
    lines = [f"usage: {PROG} <verb> [args]", f"       {PROG} --help | --version", ""]
    section = None
    for entry in ENTRY_POINTS:
        if entry.section != section:
            section = entry.section
            lines.append(f"{section}:")
        lines.append(f"  {entry.verb:<{width}}  {entry.summary}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Dispatch to one registered command.

    Args:
        argv: Argument vector without the program name; ``None`` reads
            ``sys.argv[1:]``.

    Returns:
        The sub-CLI's exit code; 0 for ``--help`` and ``--version``; 2 for a
        missing or unknown verb, which is a malformed invocation and so shares
        argparse's code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(_usage())
        return 2
    if args[0] in ("-h", "--help"):
        sys.stdout.write(_usage())
        return 0
    if args[0] == "--version":
        print(f"{PROG} {__version__}")
        return 0
    entry = _BY_VERB.get(args[0])
    if entry is None:
        sys.stderr.write(f"{PROG}: unknown verb {args[0]!r}\n" + _usage())
        return 2
    return entry.load().main(args[1:])
