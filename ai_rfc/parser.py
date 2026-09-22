"""The ``ArgumentParser`` subclass every door in this package is built on.

A leaf on purpose. It imports the standard library and nothing of ``ai_rfc``
at module scope, because both doors need the class and only one of them can
afford the root: ``ai_rfc.cli`` mounts the whole registry to build its tree,
and a command's ``build_standalone_parser`` mounts exactly one verb. Importing
the root for one class would hand every ``python -m ai_rfc.<sub>`` the
registry, the epilog machinery and every other verb's ``configure`` — the
wrong direction, and the reason R0.7 moved the class here rather than leaving
it where its first caller lived. ``ai_rfc.cli`` keeps using it by import, so
there is one hardened parser and not two.
"""

from __future__ import annotations

import argparse
import sys
from typing import NoReturn


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

    The standalone doors are covered too, and were not when this was first
    written: each command's ``build_standalone_parser`` constructed
    :class:`argparse.ArgumentParser` directly, so ``python -m ai_rfc.<sub>``
    kept the stock ``error()`` while the root door was hardened — one door
    defended and twenty-seven not. All 27 now construct this class, and
    ``tests/substrate/test_cli_conventions.py`` asserts it over
    ``ENTRY_POINTS``, the registry
    ``test_every_cli_module_on_disk_is_registered`` holds to the tree, so a
    twenty-eighth door cannot arrive with the stock parser and no failing
    test. That sweep is why the class lives here rather than in
    ``ai_rfc/cli.py``: a leaf is what a command mounting one verb can import.
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
