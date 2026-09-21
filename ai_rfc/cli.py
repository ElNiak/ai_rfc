"""The one door onto the tool: ``ai-rfc <verb> [args]``.

One argparse tree mounts every registered command through its ``configure``
function, so ``ai-rfc <verb>`` and ``python -m ai_rfc.<sub>`` parse the same
arguments and return the same exit codes, and ``panther ai-rfc`` forwards argv
here untouched.
"""

from __future__ import annotations

import argparse
import sys
from importlib import import_module
from typing import NoReturn

from . import __version__
from .entrypoints import ENTRY_POINTS, SECTIONS

PROG = "ai-rfc"

#: Written rather than derived, for one reason only: PANTHER's door test
#: asserts ``panther ai-rfc --help`` opens with exactly ``usage: ai-rfc
#: <verb>``, and that test lives in a repository this one cannot edit.
USAGE = "%(prog)s <verb> [args]\n       %(prog)s --help | --version"


class Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose refusals go through the stderr boundary.

    argparse composes ``unrecognized arguments: %s`` with ``%s`` and not
    ``%r``, interpolating ``' '.join(argv)`` — the operator's own tokens —
    straight into a stderr line. A token carrying a newline therefore writes a
    second line that reads as a diagnostic of the tool's own: driven against a
    mounted verb it planted ``resume: ai-rfc run --config /tmp/evil.yaml``
    under a real refusal, which is a fabricated instruction in the one place
    an operator is most likely to copy one from.

    **A funnel, not a list of sites.** ``error()`` is the single method every
    argparse diagnostic is routed through, so overriding it covers each of
    those messages *without enumerating them* — which is the distinction this
    package keeps making, and the same one :func:`ai_rfc.driver.printable`
    rests on: a predicate over a category beats a list of characters somebody
    thought of. Three forging messages are known and each is an **instance**,
    not the set: ``:1836`` unrecognised arguments from ``parse_args``,
    ``:2351`` the same message from ``parse_intermixed_args``, and ``:2230``
    ambiguous option — the last found by a reviewer on a depth-2 sub-parser
    after this was written, and already covered. (Read out of the 3.10.12
    stdlib rather than carried over: ``:2351`` is a second entry point onto
    one message, not a second message. ``invalid choice`` is not among them;
    it interpolates with ``%r`` and forges nothing.) A newly noticed
    fourth needs no second fix; if one ever did, that would mean argparse had
    stopped funnelling, which is the thing to check. Sub-parsers are
    covered without a second edit and without a registry: ``add_subparsers``
    does ``kwargs.setdefault("parser_class", type(self))``, and no
    ``configure`` in this package passes one of its own. Measured over the
    tree ``build_parser()`` actually returns rather than over a stand-in:
    all **27** leaves are this class, and so is every sub-verb of the **13**
    that have one.

    Not covered, and deliberately: each command's ``build_standalone_parser``
    constructs :class:`argparse.ArgumentParser` directly, so
    ``python -m ai_rfc.<sub>`` keeps the stock ``error()``. Sweeping those is
    a separate change to a dozen files with a dozen tests, and this is the
    door fifteen verbs newly reach.
    """

    def error(self, message: str) -> NoReturn:
        """Print the usage and one escaped diagnostic, then exit 2.

        Args:
            message: argparse's own text, with the offending tokens already
                interpolated into it.

        Raises:
            SystemExit: Always, with code 2 — argparse's contract for a
                malformed invocation, unchanged.
        """
        # Function-local: importing ``lifecycle.common`` at module scope costs
        # 90 modules (measured, 137 -> 227) and ``import ai_rfc.cli`` is on no
        # error path at all. It is free where it actually runs — by the time a
        # parser can refuse anything, ``build_parser`` has imported every verb
        # and ``lifecycle.common`` with them (299 modules, already loaded).
        from .lifecycle.common import report

        self.print_usage(sys.stderr)
        report(f"{self.prog}: error: {message}")
        self.exit(2)


def _epilog() -> str:
    """Render the verb table, grouped under the headings in declared order.

    Hidden entries are skipped, and a section all of whose rows are hidden
    prints no heading at all — which is what retires ``PERFORMED`` from the
    listing while leaving its four verbs mounted. The column width is measured
    over the rows that render rather than over the whole registry, so a hidden
    verb longer than every visible one cannot pad the table for a name nobody
    sees. (It does not today: ``citation-gate`` is the longest either way.)

    Returns:
        One block per section with a visible row: its heading, then one
        aligned row per command registered under it.
    """
    visible = [entry for entry in ENTRY_POINTS if not entry.hidden]
    width = max(len(entry.verb) for entry in visible)
    lines: list[str] = []
    for section in SECTIONS:
        rows = [entry for entry in visible if entry.section == section]
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
    parser = Parser(
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
        The verb's exit code: 0 clean, 1 the command could not complete, 3
        findings — the one code with a machine consumer, ``sweep.py``'s build
        gate reading a ``check --strict`` 3. argparse exits 2 itself, which is
        the only thing 2 ever means, and raises ``SystemExit`` for ``--help``
        and ``--version``.
    """
    args = build_parser().parse_args(argv)
    return args._run(args)
