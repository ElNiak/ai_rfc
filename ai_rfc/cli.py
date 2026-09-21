"""The one door onto the tool: ``ai-rfc <verb> [args]``.

One argparse tree mounts every registered command through its ``configure``
function, so ``ai-rfc <verb>`` and ``python -m ai_rfc.<sub>`` parse the same
arguments and return the same exit codes, and ``panther ai-rfc`` forwards argv
here untouched.
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import import_module
from types import FrameType
from typing import NoReturn, Union

from . import __version__
from .entrypoints import ENTRY_POINTS, SECTIONS

PROG = "ai-rfc"

#: What ``signal.getsignal`` returns and ``signal.signal`` accepts: a handler,
#: or one of the two integral constants ``SIG_DFL`` and ``SIG_IGN``. Named
#: here because :func:`_swap_sigterm` both takes and returns one, and the two
#: halves must be the same type for a borrow to be restorable.
_Disposition = Union[Callable[[int, FrameType | None], object], int, None]

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


def _interrupted(signum: int, frame: FrameType | None) -> NoReturn:
    """Turn a signal into the exception Ctrl-C already raises.

    ``KeyboardInterrupt`` and not ``SystemExit``, so that SIGTERM and SIGINT
    reach exactly one handler in the tree: :func:`ai_rfc.driver.sweep.run`
    catches it around the session loop and stops with
    :attr:`~ai_rfc.driver.stop.StopReason.operator_interrupt`, and
    ``spawn.py``'s ``except BaseException`` kills the session's process group
    on the way out either way. Two exception types would have meant two
    handlers, and the second one would have been the one nobody wrote.

    Args:
        signum: The signal delivered, as the ``signal`` module passes it.
        frame: The interrupted stack frame; unused, and part of the handler
            protocol rather than of this function's own interface.

    Raises:
        KeyboardInterrupt: Always. A handler that returned would let the
            interrupted call resume, which for a session ``wait`` means the
            operator's second Ctrl-C is the one that works.
    """
    raise KeyboardInterrupt


def _swap_sigterm(handler: _Disposition) -> _Disposition:
    """Install one SIGTERM disposition and return the one it replaced.

    The **only** place in the package that writes a process-global signal
    disposition, which is why both the borrow and the give-back go through it
    rather than calling ``signal.signal`` twice: a second call site is how a
    handler gets installed somewhere that is not the one console script, and
    ``grep -rn "signal.signal" ai_rfc/`` is the check that says so.

    Args:
        handler: The disposition to install — a handler function,
            ``signal.SIG_DFL`` or ``signal.SIG_IGN``.

    Returns:
        The disposition that was in force before the call.

    Raises:
        ValueError: If called off the main thread, which is the standard
            library's own refusal. :func:`_sigterm_as_interrupt` is where that
            is decided to be acceptable.
    """
    return signal.signal(signal.SIGTERM, handler)


@contextmanager
def _sigterm_as_interrupt() -> Iterator[None]:
    """Borrow SIGTERM for the length of one :func:`main` call, then give it back.

    Three guards, each closing a way this could reach a process that is not
    ``ai-rfc``. :func:`main` is called in-process by most of this suite and by
    ``panther ai-rfc``, so none of them is hypothetical.

    *Only from the default disposition.* An embedder that has already taken
    SIGTERM for its own shutdown would otherwise find it replaced by one that
    raises ``KeyboardInterrupt`` out of the middle of its call. ``SIG_IGN`` is
    left alone by the same test, and deliberately: an ignored SIGTERM is
    inherited across ``exec``, so a process started under one was meant not to
    die of it.

    *Restored in a ``finally``.* A handler left behind in a pytest-xdist
    worker turns that worker's own shutdown into a traceback.

    *``ValueError`` swallowed.* ``signal.signal`` raises it off the main
    thread, which is where a library caller may well be. The call then runs
    with the disposition it found, which is what a thread gets anyway — the
    signal is delivered to the main thread whatever this one asked for.

    Yields:
        None. The body runs with SIGTERM raising ``KeyboardInterrupt``,
        unless one of the three guards declined the install.
    """
    installed = False
    previous = signal.getsignal(signal.SIGTERM)
    if previous is signal.SIG_DFL:
        try:
            _swap_sigterm(_interrupted)
            installed = True
        except ValueError:
            # Off the main thread. Not an error: nothing here is owed a
            # handler, and the main thread's disposition is what will fire.
            installed = False
    try:
        yield
    finally:
        if installed:
            _swap_sigterm(previous)


def main(argv: list[str] | None = None) -> int:
    """Parse and run one verb.

    SIGTERM is borrowed for the length of the call (see
    :func:`_sigterm_as_interrupt`) so that a supervisor's ``kill`` and the
    operator's Ctrl-C stop a sweep the same way: with a status record, a
    resume line and no orphaned session. The install lives here and nowhere
    else — a process-global disposition is the one thing a library module may
    not set for its importer.

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
    with _sigterm_as_interrupt():
        args = build_parser().parse_args(argv)
        return args._run(args)
