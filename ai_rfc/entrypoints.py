"""The commands this package exposes, declared once.

The registry is read by ``ai_rfc.cli`` (the ``ai-rfc`` door), which mounts
every command it names into one argparse tree, and the conventions suite
asserts its invariants across it. Modules are named by dotted string rather
than imported, so reading the registry itself costs nothing — a reader that
only wants the verb table never imports a command.
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
        summary: One line. The verb table under ``ai-rfc --help`` shows this
            and nothing else, and ``mkdocs-click`` renders it into the
            generated CLI reference; it is what a reader chooses a command
            from, before the command's own ``--help`` — built by its
            ``configure`` — describes it and its arguments.
        section: The heading ``ai-rfc --help`` prints this command under,
            rendered by ``_epilog()`` in ``ai_rfc/cli.py`` in registration
            order. Entries sharing one are kept contiguous in
            :data:`ENTRY_POINTS`, because that order is the order the help
            prints.
        hidden: Retire this row from the operator's help without unmounting
            the verb. ``ai-rfc <verb>`` still parses and still runs — which is
            the whole of the distinction, and it is forced rather than
            preferred: ``ai_rfc/driver/render.py``'s arm-C table types
            ``python -m ai_rfc check`` and ``python -m ai_rfc draft …``
            through **this** door, so a deleted row would break that arm
            outright. A hidden entry still declares a ``section``, because a
            section is a property of the command and not of the listing; a
            section all of whose rows are hidden simply prints no heading.
    """

    verb: str
    prog: str
    module: str
    summary: str
    section: str
    hidden: bool = False

    def load(self) -> CommandModule:
        """Import the module this entry names.

        Returns:
            The command's module, which satisfies :class:`CommandModule`.
        """
        return cast(CommandModule, import_module(self.module))


#: Headings ``ai-rfc --help`` lists commands under. Plain text: ``_epilog()``
#: in ``ai_rfc/cli.py`` writes them verbatim, so backticks would print as
#: backticks.
LIFECYCLE = "Lifecycle"
DRIVEN = "Commands you drive"
BY_HAND = "Run these yourself"
PERFORMED = "Stages pipeline run reaches before it needs you"
AGENT = "Agent verbs (used inside sessions)"

#: The order ``ai-rfc --help`` prints the headings in. Kept beside them rather
#: than derived from :data:`ENTRY_POINTS` so a heading with no rows yet still
#: has a declared place, and so the help's shape is readable in one line.
SECTIONS = (LIFECYCLE, DRIVEN, BY_HAND, PERFORMED, AGENT)


ENTRY_POINTS: tuple[EntryPoint, ...] = (
    EntryPoint(
        "config",
        "ai-rfc config",
        f"{PACKAGE}.lifecycle.config.cli",
        "Print a starter recon.yaml (example) or the field reference (reference)",
        LIFECYCLE,
    ),
    EntryPoint(
        "init",
        "ai-rfc init",
        f"{PACKAGE}.lifecycle.init.cli",
        "Create the workspace from recon.yaml: clone at the pin, fetch the "
        "forge, scaffold the draft",
        LIFECYCLE,
    ),
    EntryPoint(
        "run",
        "ai-rfc run",
        f"{PACKAGE}.lifecycle.run.cli",
        "Perform every deterministic stage that is next, then drive model "
        "sessions when a sessions: block is configured",
        LIFECYCLE,
    ),
    EntryPoint(
        "next",
        "ai-rfc next",
        f"{PACKAGE}.lifecycle.next.cli",
        "Perform exactly one action — run's one-step form — then print the "
        "ledger and the line to type next",
        LIFECYCLE,
    ),
    EntryPoint(
        "status",
        "ai-rfc status",
        f"{PACKAGE}.lifecycle.status.cli",
        "Where the reconstruction stands: stage states, the cluster ledger, "
        "the pin, config drift",
        LIFECYCLE,
    ),
    EntryPoint(
        "verify",
        "ai-rfc verify",
        f"{PACKAGE}.lifecycle.verify.cli",
        "Every gate the workspace can pass, in one exit code",
        LIFECYCLE,
    ),
    EntryPoint(
        "toolchain",
        "ai-rfc toolchain",
        f"{PACKAGE}.lifecycle.toolchain.cli",
        "Install (once, networked) or verify (offline) the Internet-Draft toolchain",
        LIFECYCLE,
    ),
    EntryPoint(
        "doctor",
        "ai-rfc doctor",
        f"{PACKAGE}.lifecycle.doctor.cli",
        "Check the environment a reconstruction runs in",
        LIFECYCLE,
    ),
    EntryPoint(
        "pipeline",
        "ai-rfc pipeline",
        f"{PACKAGE}.pipeline.cli",
        "Show where a workspace stands and run whatever stage is ready "
        "(status, substrate, run)",
        DRIVEN,
    ),
    EntryPoint(
        "experiment",
        "ai-rfc experiment",
        f"{PACKAGE}.experiment.cli",
        # No parenthesised list: this row carries more verbs than any other,
        # and spelling them out overruns the longest other summary, which
        # _epilog() emits unwrapped. Deliberately no count here -- the one
        # that used to sit in this comment went stale twice over as verbs
        # landed, and a numeral is the part that rots. The verbs are one
        # `ai-rfc experiment --help` away.
        "Drive the three-arm AI+MCP vs AI+CLI experiment over this plugin, "
        "from profile to analysis",
        DRIVEN,
    ),
    EntryPoint(
        "check",
        "ai-rfc check",
        f"{PACKAGE}.check.cli",
        "Report which manifest claims are not backed by the code their "
        "anchors point at",
        BY_HAND,
        hidden=True,
    ),
    EntryPoint(
        "draft",
        "ai-rfc draft",
        f"{PACKAGE}.draft.cli",
        # **Stays under BY_HAND, though four of its seven verbs are agent
        # verbs** (`build`, `lint`, `commit`, `render` in `docs/parity.md`).
        # Neither heading is wholly true of this row: `draft checkpoint
        # <manifest> --timeline … --out …` is exactly what "Run these
        # yourself" means, and its workspace-form siblings are exactly what
        # "used inside sessions" means. `draft` is the one entry whose two
        # forms are deliberately the same verb, which is what
        # `tests/agent/test_draft_group.py` pins, and a heading cannot say
        # "both".
        #
        # Three costs decide it. Moving the row empties BY_HAND — `check` and
        # `coverage` are hidden (R3) — and deletes a heading
        # `test_the_hidden_verbs_section_still_renders_when_one_row_survives`
        # pins as surviving, leaving no visible sign that a by-hand surface
        # exists at all while arm C still types it through this door. It would
        # also be the only AGENT row three of whose verbs an agent never
        # types, in a section whose every other summary ends in its verb list.
        # And the summary cannot carry the caveat: the row renders at 123
        # characters against a widest row of 127, so four characters is the
        # whole budget.
        #
        # What guarantees which verbs an agent types is not the heading but
        # `docs/parity.md`, whose verb column is now walked against
        # `cli.build_parser()`. The heading is navigation; the table is the
        # contract.
        #
        # Seven verbs, so no parenthesised list — the `experiment` row's rule,
        # reached the same way. Listing them renders the row at 170
        # characters, 43 past the longest other row and 31 past the 139 that
        # comment already rejected; `_epilog()` emits it unwrapped. The list
        # was affordable at six and is not at seven, and the verbs are one
        # `ai-rfc draft --help` away.
        "Freeze the manifest per cluster, gate the prose against it, and "
        "drive the workspace's own draft repository",
        BY_HAND,
    ),
    EntryPoint(
        "coverage",
        "ai-rfc coverage",
        f"{PACKAGE}.coverage.cli",
        "Propose anchors for the lines a test run actually executed",
        BY_HAND,
        hidden=True,
    ),
    EntryPoint(
        "history",
        "ai-rfc history",
        f"{PACKAGE}.history.cli",
        "Turn a pinned clone's commits into a queryable corpus",
        PERFORMED,
        hidden=True,
    ),
    EntryPoint(
        "forge",
        "ai-rfc forge",
        f"{PACKAGE}.forge.cli",
        "Pull pull-request discussion from GitHub or GitLab (fetch, adopt)",
        PERFORMED,
        hidden=True,
    ),
    EntryPoint(
        "timeline",
        "ai-rfc timeline",
        f"{PACKAGE}.timeline.cli",
        "Group the corpus into ordered clusters, one per pull request",
        PERFORMED,
        hidden=True,
    ),
    EntryPoint(
        "views",
        "ai-rfc views",
        f"{PACKAGE}.views.cli",
        "Write the per-cluster evidence folder an author reads",
        PERFORMED,
        hidden=True,
    ),
    EntryPoint(
        "corpus",
        "ai-rfc corpus",
        f"{PACKAGE}.agent.corpus.cli",
        "Query the commit corpus this workspace was built from (query)",
        AGENT,
    ),
    EntryPoint(
        "cluster",
        "ai-rfc cluster",
        f"{PACKAGE}.agent.cluster.cli",
        "Read the timeline's clusters: the next one to do, and one cluster's "
        "evidence (get, next)",
        AGENT,
    ),
    EntryPoint(
        "claim",
        "ai-rfc claim",
        f"{PACKAGE}.agent.claim.cli",
        "Add, update and adjudicate the manifest's requirement claims "
        "(upsert, check, record-status)",
        AGENT,
    ),
    EntryPoint(
        "question",
        "ai-rfc question",
        f"{PACKAGE}.agent.question.cli",
        "Draft the questions only the author can answer, and export them as "
        "one bundle (draft, export)",
        AGENT,
    ),
    EntryPoint(
        "answer",
        "ai-rfc answer",
        f"{PACKAGE}.agent.answer.cli",
        "Record what the author answered, anchored to the transcript it was "
        "said in (record)",
        AGENT,
    ),
    EntryPoint(
        "revision",
        "ai-rfc revision",
        f"{PACKAGE}.agent.revision.cli",
        "Record what each draft revision froze, and tag it once the strict "
        "gates accept it (record, tag)",
        AGENT,
    ),
    EntryPoint(
        "checkpoint",
        "ai-rfc checkpoint",
        f"{PACKAGE}.agent.checkpoint.cli",
        "Freeze the manifest against one cluster, write-once",
        AGENT,
    ),
    EntryPoint(
        "gate",
        "ai-rfc gate",
        f"{PACKAGE}.agent.gate.cli",
        "Weigh every claim against the evidence its anchors point at",
        AGENT,
    ),
    EntryPoint(
        "citation-gate",
        "ai-rfc citation-gate",
        f"{PACKAGE}.agent.citation_gate.cli",
        "Check the draft's prose against the checkpoints its revisions froze",
        AGENT,
    ),
    EntryPoint(
        "structure",
        "ai-rfc structure",
        f"{PACKAGE}.agent.structure.cli",
        "Declare the data structures the draft renders as blocks (upsert)",
        AGENT,
    ),
)
