"""The commands this package exposes, declared once.

The registry is read by ``ai_rfc.cli`` (the ``ai-rfc`` door), which builds its
usage listing from it, and the conventions suite asserts its invariants
across it. Modules are named by dotted string rather than imported, so
reading the registry costs nothing and the eight argparse CLIs load only
when one of them is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

#: This package, as a dotted path. Derived rather than written so the registry
#: survives a relocation that the rest of the tree would not.
PACKAGE = __name__.rsplit(".", 1)[0]


class CommandModule(Protocol):
    """A module exposing the substrate's argv-in, exit-code-out contract."""

    def main(self, argv: list[str] | None = ...) -> int:
        """Run the command and return its exit code."""
        ...


@dataclass(frozen=True)
class EntryPoint:
    """One command, reachable through either front door.

    Attributes:
        verb: The token following ``ai-rfc``.
        prog: The sub-CLI's own ``argparse`` ``prog=``, which ``--version``
            prints and which the leaf's usage line shows. Always
            ``ai-rfc <verb>``, so a usage line names the command the user
            typed; kept as data rather than derived so the conventions suite
            can assert the two agree.
        module: Dotted path of the ``cli`` module, not of its package — the
            ``__main__`` guard test derives that name by trimming one segment.
        summary: One line. Shown by ``--help`` and rendered into the generated
            CLI reference by ``mkdocs-click``, where it is the only description
            a reader gets, since the arguments forward untouched.
        section: The heading ``ai-rfc --help`` prints this command under,
            rendered by ``_usage()`` in ``ai_rfc/cli.py`` in registration
            order. Entries sharing one are kept contiguous in
            :data:`ENTRY_POINTS`, because that order is the order the help
            prints.
    """

    verb: str
    prog: str
    module: str
    summary: str
    section: str

    def load(self) -> CommandModule:
        """Import the module this entry names.

        Returns:
            The command's module, which satisfies :class:`CommandModule`.
        """
        return cast(CommandModule, import_module(self.module))


#: Headings ``ai-rfc --help`` lists commands under. Plain text: ``_usage()`` in
#: ``ai_rfc/cli.py`` writes them verbatim, so backticks would print as backticks.
DRIVEN = "Commands you drive"
BY_HAND = "Run these yourself"
PERFORMED = "Stages pipeline run reaches before it needs you"


ENTRY_POINTS: tuple[EntryPoint, ...] = (
    EntryPoint(
        "pipeline",
        "ai-rfc pipeline",
        f"{PACKAGE}.pipeline.cli",
        "Show where a workspace stands and run whatever stage is ready "
        "(status, substrate, run)",
        DRIVEN,
    ),
    EntryPoint(
        "check",
        "ai-rfc check",
        f"{PACKAGE}.check.cli",
        "Report which manifest claims are not backed by the code their "
        "anchors point at",
        BY_HAND,
    ),
    EntryPoint(
        "draft",
        "ai-rfc draft",
        f"{PACKAGE}.draft.cli",
        "Freeze the manifest per cluster, then gate the prose against it "
        "(checkpoint, gate, completeness, build, lint)",
        BY_HAND,
    ),
    EntryPoint(
        "coverage",
        "ai-rfc coverage",
        f"{PACKAGE}.coverage.cli",
        "Propose anchors for the lines a test run actually executed",
        BY_HAND,
    ),
    EntryPoint(
        "history",
        "ai-rfc history",
        f"{PACKAGE}.history.cli",
        "Turn a pinned clone's commits into a queryable corpus",
        PERFORMED,
    ),
    EntryPoint(
        "forge",
        "ai-rfc forge",
        f"{PACKAGE}.forge.cli",
        "Pull pull-request discussion from GitHub or GitLab (fetch, adopt)",
        PERFORMED,
    ),
    EntryPoint(
        "timeline",
        "ai-rfc timeline",
        f"{PACKAGE}.timeline.cli",
        "Group the corpus into ordered clusters, one per pull request",
        PERFORMED,
    ),
    EntryPoint(
        "views",
        "ai-rfc views",
        f"{PACKAGE}.views.cli",
        "Write the per-cluster evidence folder an author reads",
        PERFORMED,
    ),
)
