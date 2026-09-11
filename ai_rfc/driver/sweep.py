"""The loop that decides what happens next, and stops when it should not.

Spec §5 gives this a nine-row state machine and one signature,
``plan_next(ws, cfg, ledger) -> Action``, described as pure. A function handed
a ``Path`` and called pure reads disk, and a table that reads disk needs a
workspace per row to test. It is split here instead:

* :func:`observe` performs **every** disk and clock read — the stage states,
  the ledger's rows, :func:`~ai_rfc.driver.record.spent`,
  :func:`~ai_rfc.driver.record.attempts`, whether a round is due;
* :func:`plan_next` is a total function of what it returned, and is the nine
  rows;
* :func:`run` walks the two until something stops it.

Two rulings shape what it writes.

*Production renders its prompts on every invocation.* A campaign freezes its
prompts at ``campaign init``; production has no freeze step, so the system
prompt and the task are rendered from the package templates each time and
``run.json`` records the sha256 of both. **Prompt drift** is those digests
differing from the previous run's — which is what spec risk 5 asks for without
saying how. A deliberate template change is therefore *noted*, not refused;
making it refusable is what the spec reserves ``--reseal-prompts`` for.

*An interrupted run never writes* ``status.json``. The resume rule keys on its
absence: ``runs/<ts>/`` without one becomes ``runs/<ts>.interrupted-<cause>/``.
So nothing here writes it from a ``finally`` — only a normal exit and a
stop-classified one write it, and a sweep killed between them leaves exactly
the leftover the next resume is looking for.
"""

from __future__ import annotations

import hashlib
import string
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_rfc import ledger
from ai_rfc.pipeline.run import perform, workspace_from
from ai_rfc.pipeline.stages import BY_NAME
from ai_rfc.pipeline.state import State, state

from . import DriverError, printable, record, render
from .arms import arm_profile
from .consolidation import Due, consolidation_due, consolidations_recorded
from .session import SessionSpec, claude_version, run_session, surface_shortfall
from .stop import (
    ERRORED,
    StopReason,
    _checked_cluster_id,
    classify,
    exit_code,
    resume_line,
)

#: Production's arm (D58): the MCP shape, Read/Edit/Write/Grep/Glob, no Bash.
#: The three-arm instrument varies this; a production sweep never does.
ARM = "A"

#: The deterministic stages a sweep performs before any session. ``forge`` is
#: acquired at ``init`` (D34) and is not re-fetched here; ``mining`` and
#: ``prose`` are the agent's, which is what the sessions are for.
SWEPT_STAGES: tuple[str, ...] = ("history", "timeline", "views")

#: The stages whose staleness renumbers what a checkpoint pins.
SUBSTRATE_STAGES: tuple[str, ...] = ("timeline", "views")

#: The build gate, in the order spec §5 lists it. ``build`` is skipped when no
#: toolchain is configured, and hard when one is (D49).
GATE_STAGES: tuple[str, ...] = ("check", "lint", "build")

#: What one iteration of the loop decided to do.
ACTIONS: tuple[str, ...] = ("stage", "session", "consolidation", "gate", "stop")

#: The sealed configuration, duplicated from
#: :attr:`ai_rfc.lifecycle.workspace.Layout.config` for the same reason
#: :data:`ai_rfc.driver.record.RUNS_DIR` duplicates ``runs``: this package sits
#: below ``lifecycle`` and may not import it.
CONFIG_FILE = "recon.yaml"
#: The system prompt this run rendered, beside the run rather than inside the
#: workspace: a session may write the workspace, and a prompt it could rewrite
#: is a prompt whose digest proves nothing.
PROMPT_FILE = "prompt.md"
#: The consolidation round's own system prompt.
CONSOLIDATION_PROMPT_FILE = "prompt-consolidation.md"
#: The cause an interrupted run directory is moved aside under. A checkpoint
#: is moved aside under a timestamp instead, because several may be leftover
#: at once and the cause is the same for all of them.
INTERRUPT_CAUSE = "interrupt"
#: Fixed width and chronological as a string, which is what
#: :func:`~ai_rfc.driver.record.previous_run_record` orders by. Microseconds
#: rather than seconds so two invocations of one workspace inside the same
#: second cannot mint the same id — a collision there is refused by
#: ``write_run_record``, and refusing a legitimate second run would be worse
#: than the collision it guards against.
RUN_ID_FORMAT = "%Y%m%dT%H%M%S.%fZ"


def report(message: str) -> None:
    """Progress and diagnostics to stderr, as one printable line.

    A sweep of sixty-nine clusters runs for hours, and the ``panther.*``
    loggers swallow warnings, so these go straight to the stream an operator
    is watching.

    **This is the one place a value under an agent's control is escaped for
    this artifact**, and it is a boundary rather than a rule each caller
    follows. Four lines interpolate a cluster id — the per-cluster progress
    line, ``cluster_halted``'s detail, the consolidation round's base, and a
    moved-aside directory's name — and a membership check cannot save any of
    them: ``ledger._rows`` validates only that ``id`` and ``ordinal`` are
    *present*, so a cluster id carrying a newline is a perfectly legitimate
    member of the timeline. This stream is not only an operator's log; the
    optimize track renders an equivalent line into a prompt a model reads, so
    a forged record here is a forged record there.

    Args:
        message: The line to print.
    """
    print(printable(message), file=sys.stderr)


@dataclass(frozen=True)
class Observation:
    """Everything :func:`plan_next` decides from, read once.

    Attributes:
        stages: Each stage's name mapped to what its artifacts say about it.
        any_checkpoint: Whether any cluster is already frozen. A moved-aside
            checkpoint does not count — it is evidence of an interruption, not
            a pin something was numbered against.
        window: The inclusive ordinal bounds this reconstruction covers, which
            is what the run's ``task_sha256`` is rendered over.
        ordinals: The timeline's ordinal for each of its cluster ids, in
            ordinal order. It serves two jobs and is stored once for both: a
            resume line naming a cluster is guarded by **membership** (this
            package cannot read a timeline from inside
            :mod:`~ai_rfc.driver.stop`), and a ``--until cluster:<id>`` bound
            is resolved **positionally**, which needs the ordinal rather than
            the name. :attr:`known_clusters` is the membership view of it.
        cluster: The lowest-ordinal outstanding cluster, or None when none is.
        attempts: What that cluster has already consumed, across every run of
            the workspace, less anything ``--retry`` forgave.
        launches: How many sessions **this invocation** has already given it,
            whatever those sessions were classified as. The durable count above
            only counts sessions that ended on their own (D61), so a cluster
            whose sessions are all killed consumes nothing and would otherwise
            be relaunched forever.
        spent_usd: The lifetime figure the budget caps.
        seconds_left: Against the sweep's wall clock, or None when no deadline
            was given — which is every production invocation today, since no
            ``recon.yaml`` field feeds one (``sessions.timeout_s`` is per
            session, D61).
        round_due: The consolidation round to run now, or None.
        attempted_rounds: Ordinals of the mid-sweep rounds already tried this
            invocation. A round that recorded nothing stays due, so without
            this the sweep buys it once per cluster from the first failure on.
        shortfall: What the first judgeable session mounted instead of the
            ``ai_rfc`` server, or None.
        last_error: Why the last session is an ``errored`` one — a launch or
            API failure — or None.
    """

    stages: Mapping[str, State]
    any_checkpoint: bool
    window: tuple[int, int]
    ordinals: Mapping[str, int]
    cluster: ledger.ClusterState | None
    attempts: int
    launches: int
    spent_usd: float
    seconds_left: float | None
    round_due: Due | None
    attempted_rounds: frozenset[int] = field(default_factory=frozenset)
    shortfall: str | None = None
    last_error: str | None = None

    @property
    def known_clusters(self) -> tuple[str, ...]:
        """Every cluster id the timeline holds, in ordinal order.

        Derived rather than stored beside :attr:`ordinals`: two fields over one
        key set is two answers to one question, and the one that drifted would
        be the one a membership guard trusted.

        Returns:
            The ids.
        """
        return tuple(self.ordinals)


@dataclass(frozen=True)
class Action:
    """One row of the state machine, named.

    Attributes:
        kind: One of :data:`ACTIONS`.
        stage: The deterministic stage to perform, for ``stage``.
        cluster: The cluster a ``session`` is for, and the one a
            ``cluster_halted`` stop names in its resume line.
        round_due: The consolidation round, for ``consolidation``.
        at_end: Whether that round is the sweep's last. The sweep-end round is
            the deliverable and its failure exits 1; a mid-sweep one is noted
            and the sweep continues (D59).
        reason: Why the sweep stopped, for ``stop``.
        detail: What to print beside the reason.
    """

    kind: str
    stage: str | None = None
    cluster: ledger.ClusterState | None = None
    round_due: Due | None = None
    at_end: bool = False
    reason: StopReason | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        """Refuse a kind outside :data:`ACTIONS`.

        The loop dispatches on this string, and an unrecognised one would fall
        through every branch to ``mode``'s check: ``"one"`` would then report
        ``action_performed`` for work nobody did — a sweep reporting success
        for nothing — and ``"all"`` would spin on the same observation.

        Raises:
            DriverError: If ``kind`` is not one of :data:`ACTIONS`.
        """
        if self.kind not in ACTIONS:
            raise DriverError(
                f"{self.kind!r} is not an action; the loop dispatches on this "
                f"and would silently do nothing. Actions: {', '.join(ACTIONS)}"
            )


def _sha256(text: str) -> str:
    """The digest of a rendered prompt, over its UTF-8 bytes."""
    return hashlib.sha256(text.encode()).hexdigest()


def _digest_of(path: Path) -> str | None:
    """A file's digest, or None when it cannot be read.

    ``run.json`` requires the key to be present and never requires a value: a
    workspace missing its sealed config is a workspace ``run`` should not have
    reached, and recording that the digest is unknown says more than crashing
    a run that has already started spending.

    Args:
        path: The file to digest.

    Returns:
        The hex digest, or None.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def plugin_root() -> Path:
    """The shipped ``ai-rfc`` plugin whose skill texts the prompts bundle.

    Derived from this package's own location rather than configured, which is
    what makes a production prompt reproducible from the installed package
    alone. It mirrors ``experiment/cli.py``'s ``_repo_root``, and the two are
    the same directory.

    Returns:
        The plugin directory.
    """
    return Path(render.__file__).resolve().parents[2] / "plugins" / "ai-rfc"


def _refuse_duplicate_ids(states: tuple[ledger.ClusterState, ...]) -> None:
    """Refuse a timeline where two rows claim one cluster id.

    Failing loudly beats reconstructing something narrower than the operator
    asked for. A duplicate id has no correct reading: the checkpoints, the
    revision entries and the attempt counts of the two rows are the same
    records, so whichever row survives a lookup, the other one's work is
    attributed to it. Every other agent-written value in this package is
    checked before it is used, and this is that check for the timeline itself.

    Args:
        states: The ledger's rows, in ordinal order.

    Raises:
        DriverError: If any id appears more than once.
    """
    seen: set[str] = set()
    repeated: set[str] = set()
    for cluster in states:
        if cluster.id in seen:
            repeated.add(cluster.id)
        seen.add(cluster.id)
    duplicated = sorted(repeated)
    if duplicated:
        raise DriverError(
            "this workspace's timeline names "
            f"{', '.join(repr(name) for name in duplicated)} twice; a cluster "
            "id is what a checkpoint, a revision entry and an attempt count "
            "are all looked up by, so two rows claiming one id have no "
            "correct reading. Re-cluster the timeline"
        )


def _any_checkpoint(workspace: Path) -> bool:
    """Whether a live checkpoint pins the current cluster numbering.

    A moved-aside checkpoint is skipped: it is the evidence of an interrupted
    freeze, and ``draft.completeness.checkpoint_records`` already passes it
    over for the same reason — it holds no ``checkpoint.json``.

    Args:
        workspace: The workspace root.

    Returns:
        True when at least one checkpoint record exists.
    """
    root = workspace / "checkpoints"
    if not root.is_dir():
        return False
    return any(
        (child / ledger.CHECKPOINT_FILE).is_file()
        for child in root.iterdir()
        if child.is_dir() and record.INTERRUPTED not in child.name
    )


def next_round(workspace: Path, cfg: Any, *, at_end: bool = False) -> Due | None:
    """Whether a consolidation round is due — SP7c's hook, over the config.

    The interval is read from the configuration rather than from a constant,
    which is the whole of what this wrapper adds: ``sessions.consolidate_every``
    was loaded and re-serialised and consumed nowhere, so an operator who wrote
    ``3`` got a sweep that consolidated every ten.

    Args:
        workspace: The workspace root.
        cfg: The validated configuration; its ``sessions`` block supplies the
            interval, and a config without one schedules nothing.
        at_end: True when no cluster is outstanding, which consolidates any
            unconsolidated remainder regardless of the interval.

    Returns:
        The round to run, or None when none is due.
    """
    if cfg.sessions is None:
        return None
    return consolidation_due(workspace, cfg.sessions.consolidate_every, at_end=at_end)


def observe(
    workspace: Path,
    cfg: Any,
    *,
    deadline: float | None = None,
    shortfall: str | None = None,
    last_error: str | None = None,
    launches: Mapping[str, int] | None = None,
    attempted_rounds: Iterable[int] = (),
    forgiven: Mapping[str, int] | None = None,
) -> Observation:
    """Read the workspace and the clock once, for one turn of the loop.

    Everything :func:`plan_next` needs is gathered here so that function can be
    pure. The keyword arguments are the facts that are **not** on disk: what
    the loop has already launched and already tried, and what the last session
    turned out to be.

    Args:
        workspace: The workspace root.
        cfg: The validated configuration.
        deadline: A ``time.monotonic`` value the sweep must finish by, or None
            for no wall-clock cap. Nothing in a ``recon.yaml`` supplies one
            today; the argument exists so the row is implemented rather than
            unreachable.
        shortfall: What the first judgeable session mounted instead of the
            ``ai_rfc`` server.
        last_error: Why the last session was a launch or API failure.
        launches: Cluster id to how many sessions this invocation gave it.
        attempted_rounds: Ordinals of the mid-sweep rounds already tried.
        forgiven: Cluster id to attempts ``--retry`` discounts. Every id must
            name a cluster of this workspace's timeline.

    Returns:
        One :class:`Observation`.

    Raises:
        DriverError: If ``forgiven`` names a cluster the timeline does not
            hold — a ``--retry`` value that silently forgave nothing would
            leave the operator watching the same halt repeat — or if the
            timeline names one cluster id twice, which has no correct reading
            and would narrow the window this run is rendered over.
        ledger.LedgerError: If a timeline exists but cannot be read.
        OSError: If a stage artifact exists but cannot be read.
    """
    layout = workspace_from(workspace)
    stages = {entry.stage.name: entry.state for entry in state(layout)}
    bounds = _window_bounds(workspace, cfg)
    states: tuple[ledger.ClusterState, ...] = ()
    if layout.clusters_jsonl.exists():
        states = ledger.clusters(workspace, bounds)
    # Refused before the mapping is built, because building it is what hides
    # the problem: keying by id collapses two rows into one, and the window
    # then derives from whichever survived. Measured on `a@1, b@3, a@5`: the
    # deduped window is (3, 5) where the rows say (1, 5) — and the window is
    # what `task_sha256` is rendered over, so a duplicate silently narrows what
    # the reconstruction covers. `ledger._rows` validates only that `id` and
    # `ordinal` are *present*, and cluster ids come from agent-written YAML, so
    # this is a shape the substrate permits rather than a hypothetical.
    _refuse_duplicate_ids(states)
    # Insertion order is ordinal order: `ledger._rows` sorts by ordinal, so
    # `known_clusters` reads back in the order `next_cluster` walks.
    ordinals = {cluster.id: cluster.ordinal for cluster in states}
    for cluster_id in forgiven or {}:
        _checked_cluster_id(cluster_id, tuple(ordinals))
    outstanding = next((c for c in states if not c.done), None)
    seen = list(ordinals.values())
    window = bounds or ((min(seen), max(seen)) if seen else (0, 0))
    attempts = 0
    if outstanding is not None:
        attempts = max(
            record.attempts(workspace, outstanding.id)
            - (forgiven or {}).get(outstanding.id, 0),
            0,
        )
    return Observation(
        stages=stages,
        any_checkpoint=_any_checkpoint(workspace),
        window=window,
        ordinals=ordinals,
        cluster=outstanding,
        attempts=attempts,
        launches=(launches or {}).get(outstanding.id, 0) if outstanding else 0,
        spent_usd=record.spent(workspace),
        seconds_left=None if deadline is None else deadline - time.monotonic(),
        round_due=next_round(workspace, cfg, at_end=outstanding is None),
        attempted_rounds=frozenset(attempted_rounds),
        shortfall=shortfall,
        last_error=last_error,
    )


def plan_next(obs: Observation, cfg: Any) -> Action:
    """The nine rows of spec §5's state machine, as one total function.

    Pure: it reads nothing but its two arguments, which is what makes each row
    a unit test rather than a fixture.

    The order is the precedence, and three places in it are decisions rather
    than transcription:

    * **A surface shortfall outranks everything.** It is a verdict on how the
      run was launched, and every row below it would spend money on sessions
      that cannot checkpoint, gate or tag.
    * **A stale substrate outranks performing the stage again.** Row 2 would
      otherwise re-run exactly what row 3 exists to refuse.
    * **A spent budget outranks a halted cluster.** ``--retry`` cannot buy a
      session there is no budget for, so reporting the halt first would send
      the operator round the loop twice. ``per_cluster`` guards the resource
      before its attempt loop for the same reason.

    Args:
        obs: What :func:`observe` read.
        cfg: The validated configuration; its ``sessions`` block supplies the
            budget and the attempt cap.

    Returns:
        The action to take.
    """
    if obs.stages.get("pin") is not State.DONE:
        return Action(
            "stop",
            reason=StopReason.needs_init,
            detail="the clone is not pinned; init does the clone and the forge",
        )
    if obs.shortfall is not None:
        return Action(
            "stop",
            reason=StopReason.surface_shortfall,
            detail=(
                f"the session mounted {obs.shortfall} rather than the ai_rfc "
                "server, so it could not checkpoint, gate or tag"
            ),
        )
    if obs.last_error is not None:
        return Action("stop", reason=StopReason.session_failed, detail=obs.last_error)
    if obs.any_checkpoint and any(
        obs.stages.get(name) is State.STALE for name in SUBSTRATE_STAGES
    ):
        stale = [n for n in SUBSTRATE_STAGES if obs.stages.get(n) is State.STALE]
        return Action(
            "stop",
            reason=StopReason.stale_substrate,
            detail=(
                f"{', '.join(stale)} moved under existing checkpoints; "
                "re-clustering renumbers what they pin"
            ),
        )
    for name in SWEPT_STAGES:
        if obs.stages.get(name) not in (State.DONE, State.RECOMPUTED):
            return Action("stage", stage=name)
    sessions = cfg.sessions
    if sessions is None:
        # Guarded here as well as in `run`, and for the same reason
        # `next_round` guards it: the table must not depend on which caller
        # reached it. Without this, a hand-mined config raises AttributeError
        # on the budget row — an error no handler in this tree names.
        raise DriverError(
            "no sessions are configured; a sweep has no budget, no attempt "
            "cap and nothing to launch"
        )
    if obs.spent_usd >= sessions.budget_usd:
        return Action(
            "stop",
            reason=StopReason.budget,
            detail=f"${obs.spent_usd:.2f} of ${sessions.budget_usd:.2f} spent",
        )
    if obs.seconds_left is not None and obs.seconds_left <= 0:
        return Action(
            "stop", reason=StopReason.wall_clock, detail="the wall clock is reached"
        )
    at_end = obs.cluster is None
    if obs.round_due is not None and (
        at_end or obs.round_due.ordinal not in obs.attempted_rounds
    ):
        return Action("consolidation", round_due=obs.round_due, at_end=at_end)
    if obs.cluster is not None:
        cap = sessions.attempts_per_cluster
        if obs.attempts >= cap or obs.launches >= cap:
            return Action(
                "stop",
                cluster=obs.cluster,
                reason=StopReason.cluster_halted,
                detail=(
                    f"cluster {obs.cluster.id} (ordinal {obs.cluster.ordinal}) "
                    f"did not finish in {cap} attempt(s)"
                ),
            )
        return Action("session", cluster=obs.cluster)
    return Action("gate")


def exit_code_for(reason: StopReason, *, strict_findings: bool = False) -> int:
    """The process exit code for a sweep that stopped for this reason.

    A thin pass-through to :func:`ai_rfc.driver.stop.exit_code`, kept so a
    caller of this module never has to reach past it for the one number the
    operator's shell sees.

    Args:
        reason: Why the sweep stopped.
        strict_findings: Whether ``check --strict`` reported the findings.

    Returns:
        0, 1 or 3.
    """
    return exit_code(reason, strict_findings=strict_findings)


def resume_for(
    action: Action,
    config_path: Path,
    known_clusters: Iterable[str],
    *,
    until: str | None = None,
    mode: str = "all",
) -> str:
    """The one line an operator types after this stop.

    Args:
        action: The ``stop`` action.
        config_path: The operator's ``recon.yaml``, as they named it.
        known_clusters: Every cluster id the timeline holds, for the
            membership guard a line naming one goes through.
        until: The bound this invocation was given, carried onto the line so a
            copied resume stops where the operator asked rather than sweeping
            past it (D59's "exact").
        mode: The sweep's mode. ``"one"`` means the operator was stepping, and
            the line keeps that cadence instead of sending them into a full
            sweep — the fact :func:`~ai_rfc.driver.stop.resume_line` cannot
            derive from the reason alone.

    Returns:
        The line, beginning with ``ai-rfc``.

    Raises:
        DriverError: If the action carries no reason, or the reason is
            ``done`` — a finished sweep has nothing to resume.
    """
    if action.reason is None:
        raise DriverError(f"a {action.kind} action names no stop to resume from")
    names_cluster = (
        action.reason is StopReason.cluster_halted and action.cluster is not None
    )
    cluster_id = action.cluster.id if names_cluster and action.cluster else None
    return resume_line(
        action.reason,
        config_path,
        cluster_id=cluster_id,
        known_clusters=tuple(known_clusters) if cluster_id is not None else None,
        until=until,
        stepping=mode == "one",
    )


def move_leftovers_aside(workspace: Path) -> list[Path]:
    """Rename what an interrupted run left behind, before anything is planned.

    A ``runs/<ts>/`` without ``status.json`` and a ``checkpoints/<id>/``
    without ``checkpoint.json`` are both the mark of a kill between two writes.
    Neither is deleted: the run's transcript is still owed against the lifetime
    budget, and the half-frozen checkpoint is the evidence of what the retry
    walked into.

    What is **already** moved aside is skipped rather than moved again. It has
    no marker file either, so it satisfies the same predicate on every
    subsequent resume; unguarded, each pass appended another suffix until the
    name reached ``ENAMETOOLONG``. ``move_aside`` now refuses it as a backstop,
    which turns that loop into a raised error rather than a fix — the filter
    here is the fix.

    A dirty draft worktree is named and left to the retry (spec §5): what an
    interrupted session was in the middle of writing is the retry's context,
    and cleaning it would throw away the only account of it.

    Args:
        workspace: The workspace root.

    Returns:
        The new paths, in the order they were moved.

    Raises:
        DriverError: If a leftover collides with a name already taken.
        OSError: If a rename fails.
    """
    moved: list[Path] = []
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for root, marker, cause in (
        (workspace / record.RUNS_DIR, record.STATUS_FILE, INTERRUPT_CAUSE),
        (workspace / "checkpoints", ledger.CHECKPOINT_FILE, stamp),
    ):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or record.INTERRUPTED in child.name:
                continue
            if (child / marker).is_file():
                continue
            moved.append(record.move_aside(child, cause))
            report(f"moved aside: {child.name} -> {moved[-1].name}")
    draft = workspace_from(workspace).draft
    if (draft / ".git").exists():
        dirty = subprocess.run(
            ["git", "-C", str(draft), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        if dirty.stdout.strip():
            report(
                f"note: {draft} has uncommitted changes; they are left for the "
                "retry to work from rather than cleaned"
            )
    return moved


def _window_bounds(workspace: Path, cfg: Any) -> tuple[int, int] | None:
    """The ordinal bounds this reconstruction covers, or None for every cluster.

    One reader, because a figure counted over a different window than the
    sweep planned against is a figure about someone else's reconstruction.

    Args:
        workspace: The workspace root.
        cfg: The validated configuration.

    Returns:
        The inclusive bounds, or None.
    """
    return cfg.window or ledger.window_of(workspace)


def _report_ledger(workspace: Path, cfg: Any) -> None:
    """Print the cluster ledger, or say why it cannot be read.

    Guarded the way ``status`` and ``verify`` guard theirs: a stop can happen
    before a timeline exists — ``needs_init`` always does — and a stop path
    that raised on its way out would replace the diagnosis with a traceback.

    Counted over the **configured window**, the one :func:`observe` planned
    against. Read unwindowed, a stop's "N of M done" counts clusters the
    operator never asked for, and it is that figure a resume decision is made
    on.

    Args:
        workspace: The workspace root.
        cfg: The validated configuration, for the window.
    """
    try:
        summary = ledger.counts(
            ledger.clusters(workspace, _window_bounds(workspace, cfg))
        )
    except (ledger.LedgerError, OSError):
        report("clusters: no timeline yet")
        return
    report(
        f"clusters: {summary['done']} of {summary['in_window']} done, "
        f"{summary['partial']} partial, {summary['outstanding']} outstanding"
    )


def _require_toolchain(cfg: Any) -> None:
    """Refuse a sweep whose configured toolchain record is not on disk.

    This closes a failure that fails **open**, and it is invisible from every
    single vantage point along the way. ``config.py`` defaults ``toolchain`` to
    ``<experiments root>/tools/toolchain.json`` rather than to None, so a
    loaded configuration always names one — which is why
    :func:`_build_gate`'s "build skipped (no toolchain configured)" branch
    cannot be reached from a ``recon.yaml`` at all, and why
    ``session_env`` always exports ``AI_RFC_TOOLCHAIN``.

    What follows is not a launch failure, which is what makes it expensive.
    The MCP server starts and advertises its tools; only each individual
    *call* comes back ``AI_RFC_TOOLCHAIN=… is not a file``, resolved per call
    rather than at startup. So the session announces a whole surface,
    :func:`~ai_rfc.driver.session.surface_shortfall` has nothing to report,
    and the session exits 0 having finished nothing — which classifies as
    ``refused`` and **consumes an attempt**. The sweep then spends
    ``attempts_per_cluster`` sessions per cluster discovering it, and halts
    with ``cluster_halted``, whose resume line says ``--retry`` — a remedy
    that cannot work.

    ``init`` does not cover this. ``lifecycle.workspace.require_toolchain``
    returns early when the configuration declares no ``references``, which is
    the default, and a record can be removed after ``init`` in any case.

    None is left alone deliberately: it means *no toolchain configured*, which
    is the documented skip, and only an API caller building a ``ReconConfig``
    by hand can produce it.

    Args:
        cfg: The validated configuration.

    Raises:
        DriverError: If ``toolchain`` names a path that is not a file.
    """
    if cfg.toolchain is None or cfg.toolchain.is_file():
        return
    raise DriverError(
        f"no toolchain record at {cfg.toolchain}; a session would launch, "
        "mount every tool and then be refused by each one, spending an "
        "attempt per cluster to learn it. Run: ai-rfc toolchain provision"
    )


def _build_gate(cfg: Any, workspace: Path) -> tuple[StopReason, bool]:
    """Run ``check --strict``, ``lint`` and ``build``; report what they found.

    All three are run **strict**, which is a deviation from spec §5's literal
    "``check --strict``, ``lint``, ``build``" and is measured rather than
    assumed: ``draft/cli.py`` returns 3 for findings only under ``--strict``
    and 0 otherwise, so a lint or a build run plainly would report findings on
    stderr and hand the sweep a success. The gate would then be a green that
    proves nothing.

    ``build`` is skipped without a configured toolchain and hard with one
    (D49).

    Args:
        cfg: The validated configuration.
        workspace: The workspace root.

    Returns:
        ``(reason, strict_findings)`` — ``done`` when every gate passed, else
        ``build_failed``. ``strict_findings`` is True only when
        ``check --strict`` was the gate that fired, which is the one exit code
        spec §5 reserves 3 for.
    """
    layout = workspace_from(workspace)
    for name in GATE_STAGES:
        if name == "build" and cfg.toolchain is None:
            report("build: skipped (no toolchain configured)")
            continue
        result = perform(
            BY_NAME[name],
            layout,
            strict=True,
            toolchain=cfg.toolchain if name == "build" else None,
        )
        if result.ok:
            report(f"{name}: clean")
            continue
        report(f"error: {name} exited {result.exit_code}")
        return StopReason.build_failed, name == "check" and result.exit_code == 3
    return StopReason.done, False


def _open_run(workspace: Path, cfg: Any, obs: Observation) -> tuple[Path, Path]:
    """Mint the run directory and write ``run.json`` and the system prompt.

    Written when the first session is about to launch rather than at the top of
    the invocation, for one reason: ``task_sha256`` is the digest of the task
    rendered over the run's window, and the window is not knowable until the
    timeline is current. An invocation that only performs deterministic stages,
    or stops before reaching one, therefore leaves no run directory at all —
    which is also the shape the resume scan wants, since an empty leftover is
    one more thing for it to explain.

    Args:
        workspace: The workspace root.
        cfg: The validated configuration.
        obs: The observation the first session is planned from; its
            ``spent_usd`` is the run's ``spent_before_usd`` and its ``window``
            is what the task digest is taken over.

    Returns:
        ``(run_dir, prompt_path)``.

    Raises:
        DriverError: If a run of this id already has a record, or the prompts
            will not render.
        OSError: If the directory or either file cannot be written.
    """
    run_id = datetime.now(timezone.utc).strftime(RUN_ID_FORMAT)
    run_dir = workspace / record.RUNS_DIR / run_id
    prompt = render.arm_prompt(ARM, plugin_root())
    task = render.render_task(obs.window)
    prompt_sha256, task_sha256 = _sha256(prompt), _sha256(task)
    previous = record.previous_run_record(workspace)
    drift = (
        None
        if previous is None
        else (
            previous.get("prompt_sha256") != prompt_sha256
            or previous.get("task_sha256") != task_sha256
        )
    )
    record.write_run_record(
        run_dir,
        {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config_sha256": _digest_of(workspace / CONFIG_FILE),
            "init_sha256": _digest_of(workspace / ledger.INIT_RECORD),
            "prompt_sha256": prompt_sha256,
            "task_sha256": task_sha256,
            "prompt_drift": drift,
            "claude_version": claude_version(cfg.sessions.claude),
            "budget_usd": cfg.sessions.budget_usd,
            "spent_before_usd": obs.spent_usd,
        },
    )
    prompt_path = run_dir / PROMPT_FILE
    prompt_path.write_text(prompt)
    if drift:
        report(
            "note: the rendered prompts differ from the previous run's; "
            "run.json records both digests"
        )
    return run_dir, prompt_path


def _spec(
    cfg: Any,
    workspace: Path,
    *,
    task: str,
    prompt_file: Path,
    budget_usd: float,
    append: bool,
) -> SessionSpec:
    """One session's launch description, in production's shape.

    Args:
        cfg: The validated configuration.
        workspace: The session's working directory.
        task: The rendered task prompt.
        prompt_file: The rendered system prompt.
        budget_usd: What the reconstruction has left, which is this session's
            cap: the totals then hold however many sessions there turn out to
            be.
        append: Whether to append to the run's transcript.

    Returns:
        The spec :func:`~ai_rfc.driver.session.run_session` takes.
    """
    sessions = cfg.sessions
    return SessionSpec(
        claude=sessions.claude,
        model=sessions.model,
        effort=sessions.effort,
        budget_usd=budget_usd,
        timeout_s=sessions.timeout_s,
        profile=sessions.profile,
        # The interpreter this package is installed under: it is what the MCP
        # server and the guard must run as, and a session that resolved its own
        # would mount a different substrate than the sweep is accounting for.
        python=sys.executable,
        workspace=workspace,
        toolchain=cfg.toolchain,
        prompt_file=prompt_file,
        task=task,
        surface=arm_profile(ARM),
        append=append,
    )


@dataclass
class _Sweep:
    """The loop's own state — what disk does not hold and the clock does not say.

    A dataclass rather than a dozen locals because :func:`run` hands the same
    six facts to every branch, and a branch that forgot to update one is how a
    session gets charged for the previous session's result.
    """

    sessions_run: int = 0
    results_seen: int = 0
    any_timeout: bool = False
    surface_judged: bool = False
    reported_damage: int = 0
    launches: dict[str, int] = field(default_factory=dict)
    attempted_rounds: set[int] = field(default_factory=set)
    shortfall: str | None = None
    last_error: str | None = None
    #: Every session id this run has already attributed to a row.
    #: :attr:`~ai_rfc.driver.session.SessionResult.session_ids` is the whole
    #: shared transcript's, so without this the row writer cannot tell which
    #: of them belongs to the session it is recording.
    known_sessions: set[str] = field(default_factory=set)


def _passed(cluster: ledger.ClusterState | None, target: int) -> bool:
    """Whether the sweep has gone past an ordinal it was told to stop at.

    Args:
        cluster: The outstanding cluster, or None when none is.
        target: The bound's ordinal.

    Returns:
        True once nothing is outstanding, or the outstanding cluster sits
        *after* the target. Equal is not past: the target's own session has
        not run yet, and a bound is inclusive of the cluster it names.
    """
    return cluster is None or cluster.ordinal > target


def _bound_reached(until: str | None, obs: Observation) -> bool:
    """Whether ``--until`` says this invocation has done what it was asked.

    Tested before each action rather than after one, so a bound already
    satisfied when the sweep started stops it rather than being stepped over —
    the defect ``lifecycle/run`` fixed by moving its own test out of the
    just-performed branch.

    **A cluster or ordinal bound is positional, not a name to match.** Spec §5
    defines ``--until`` as a place to run *up to*, so the question is whether
    the outstanding cluster has passed the target — not whether it *is* the
    target. Asking the latter answered True for every cluster that was not the
    named one, so ``run --until cluster:c3`` with ``c1`` outstanding reported
    the bound honoured, launched nothing and returned **0**: a success an
    operator cannot tell from a finished sweep. This is why the ordinal is
    carried on the observation at all.

    Reaching a bound is a stop like any other, carrying
    :attr:`~ai_rfc.driver.stop.StopReason.bound_reached` through
    :func:`_finish` — it is not ``done``, because the operator asked to stop
    somewhere and got there, which is not the same as finishing.

    Args:
        until: ``<stage>``, ``cluster:<id>``, ``ordinal:<n>``, or None.
        obs: The current observation.

    Returns:
        True when the bound is reached.

    Raises:
        DriverError: If the bound names no stage, no integer, or neither a
            cluster of this timeline nor an ordinal one carries. **All three
            spellings are closed sets**, and for a reason the positional fix
            sharpened rather than removed: an unmatchable bound used to stop
            the sweep at once, and now sits after every cluster instead — so
            it spends the whole window and still reports itself honoured.

            The stage set is :data:`SWEPT_STAGES` — the stages this sweep
            performs — and **not** every name in ``obs.stages``, which is the
            whole pipeline. Those two differed by nine names, all of them
            refused by ``--until``'s parser, so a caller reaching this
            function directly got a bound that resolved here and then a resume
            line reading ``ai-rfc run … --until forge``, which the root parser
            refuses at exit 2. ``tests/cli/test_run.py`` asserts the two closed
            sets are equal so they cannot drift apart again.
    """
    if until is None:
        return False
    if until.startswith("cluster:"):
        cluster_id = _checked_cluster_id(until.split(":", 1)[1], obs.known_clusters)
        return _passed(obs.cluster, obs.ordinals[cluster_id])
    if until.startswith("ordinal:"):
        raw = until.split(":", 1)[1]
        try:
            ordinal = int(raw)
        except ValueError:
            raise DriverError(f"{raw!r} is not an ordinal") from None
        if ordinal not in set(obs.ordinals.values()):
            # The closed set `cluster:<id>` is checked against, in the other
            # spelling of the same bound. Making the bound positional changed
            # what an unmatched one costs: an ordinal above every cluster now
            # sits after the whole sweep rather than matching nothing, so the
            # window is spent and the bound still reports as honoured.
            raise DriverError(
                f"ordinal {ordinal} names no cluster of this workspace's "
                f"timeline; its ordinals are "
                f"{', '.join(str(o) for o in sorted(set(obs.ordinals.values())))}"
            )
        return _passed(obs.cluster, ordinal)
    if until in SWEPT_STAGES:
        return obs.stages[until] in (State.DONE, State.RECOMPUTED)
    raise DriverError(
        f"{until!r} names no stage, cluster or ordinal; use "
        f"{', '.join(SWEPT_STAGES)}, cluster:<id> or ordinal:<n>"
    )


def _run_one_session(
    cfg: Any,
    workspace: Path,
    run_dir: Path,
    prompt_path: Path,
    swept: _Sweep,
    obs: Observation,
    *,
    kind: str,
    task: str,
    task_template: Path,
    cluster: ledger.ClusterState | None,
) -> None:
    """Launch one session, read it back, and append its row.

    The transcript is shared by every session of a run, so ``seen`` is captured
    **before** the launch and handed to both ``run_session`` and ``classify``.
    Leaving either at 0 charges this session for every earlier one and, for a
    session that died without saying anything, classifies it on the previous
    session's success — D61's defect exactly, and silent in both directions.

    Args:
        cfg: The validated configuration.
        workspace: The workspace root.
        run_dir: The run's directory.
        prompt_path: The system prompt for this session.
        swept: The loop's state, updated in place.
        obs: The observation this session was planned from.
        kind: ``"cluster"`` or ``"consolidation"``.
        task: The rendered task prompt.
        task_template: The template it was rendered from, for the row.
        cluster: The cluster this session is for, or None.
    """
    seen = swept.results_seen
    budget_left = max(cfg.sessions.budget_usd - obs.spent_usd, 0.0)
    result = run_session(
        _spec(
            cfg,
            workspace,
            task=task,
            prompt_file=prompt_path,
            budget_usd=budget_left,
            append=swept.sessions_run > 0,
        ),
        run_dir,
        seen=seen,
    )
    swept.sessions_run += 1
    swept.results_seen = result.results_seen
    swept.any_timeout = swept.any_timeout or result.timed_out
    classification = classify(result, seen=seen)
    # The ids this session added, not the transcript's first. `session_ids` is
    # documented as every distinct id in the **shared** transcript in
    # first-appearance order, so `[0]` is the first session's forever and every
    # row after the first attributed its work to session one. Filtered against
    # what the run has already claimed, exactly as `experiment.per_cluster`
    # does — the contract's other consumer, which never dropped it.
    #
    # Not `[-1]` either: a session killed before its init event adds no id at
    # all, and the tail is then the *previous* session's. A row that names no
    # session is the truth about a session that never named itself.
    announced = [sid for sid in result.session_ids if sid not in swept.known_sessions]
    swept.known_sessions.update(announced)
    if cluster is not None:
        swept.launches[cluster.id] = swept.launches.get(cluster.id, 0) + 1
    record.append_session(
        run_dir,
        {
            "session": swept.sessions_run,
            "kind": kind,
            "cluster_id": cluster.id if cluster else None,
            "ordinal": cluster.ordinal if cluster else None,
            "task_template": str(task_template),
            "classification": classification,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "cost_usd": result.cost_usd,
            "lifetime_cost_usd": obs.spent_usd + result.cost_usd,
            "budget_given_usd": budget_left,
            "session_id": announced[0] if announced else None,
            "wall_s": round(result.wall_s, 1),
            "damaged": result.damaged,
            "argv": list(result.argv),
        },
    )
    if result.damaged > swept.reported_damage:
        report(
            f"note: {result.damaged} transcript line(s) unreadable; the spend "
            "figure is a floor and the cap is enforced against it"
        )
        swept.reported_damage = result.damaged
    if not swept.surface_judged:
        # Judged on the first session that *can* be judged, not on the first
        # session outright: a session that produced no init event has not said
        # what it mounted, and spending the verdict on that silence leaves the
        # rest of the window unguarded.
        swept.surface_judged, shortfall = surface_shortfall(ARM, list(result.events))
        if shortfall is not None:
            swept.shortfall = shortfall
    if classification == ERRORED:
        swept.last_error = (
            f"the session exited {result.exit_code} without reaching the model "
            "(no result event of its own); the launch environment is what to fix"
        )


def _run_consolidation(
    cfg: Any,
    workspace: Path,
    run_dir: Path,
    swept: _Sweep,
    obs: Observation,
    action: Action,
) -> bool:
    """Run one consolidation round; report whether it recorded its revision.

    ``recorded`` is re-derived from disk rather than read off an exit code: an
    agent can exit 0 having done nothing, and ``consolidation_due`` answers
    None both for a map with nothing outstanding and for one its loader
    refuses — so a round that destroyed the map would otherwise be credited by
    its own damage.

    Args:
        cfg: The validated configuration.
        workspace: The workspace root.
        run_dir: The run's directory.
        swept: The loop's state.
        obs: The observation this round was planned from.
        action: The ``consolidation`` action.

    Returns:
        True when the round recorded its revision.

    Raises:
        DriverError: If the base cluster names no cluster of this timeline.
        OSError: If the round's system prompt cannot be written.
    """
    due = action.round_due
    assert due is not None  # noqa: S101 - plan_next never emits one without it
    # Checked before it is printed, not after: the report sink is a line-per-
    # record artifact and the optimize track renders an equivalent line into a
    # prompt a model reads, so an unchecked id here is an injection.
    base = _checked_cluster_id(due.base_cluster, obs.known_clusters)
    report(f"consolidation {due.ordinal:02d} due ({due.reason}), base {base}")
    prompt_path = run_dir / CONSOLIDATION_PROMPT_FILE
    if not prompt_path.exists():
        prompt_path.write_text(render.consolidation_prompt(ARM, plugin_root()))
    template = render.task_template_path("consolidation")
    # Two passes because render_task fills only the window; the round and its
    # base are per-round values no template can carry. The second pass cannot
    # be forged by the first, whose substitutions are window bounds and
    # therefore integers, and safe_substitute never re-parses what it wrote.
    task = string.Template(
        render.render_task(
            (due.ordinal, due.ordinal), template=template, profile="consolidation"
        )
    ).safe_substitute(ordinal=due.ordinal, base=base)
    _run_one_session(
        cfg,
        workspace,
        run_dir,
        prompt_path,
        swept,
        obs,
        kind="consolidation",
        task=task,
        task_template=template,
        cluster=None,
    )
    if consolidations_recorded(workspace) >= due.ordinal:
        report(f"consolidation {due.ordinal:02d} recorded")
        return True
    report(
        f"consolidation {due.ordinal:02d} recorded no revision"
        + ("" if action.at_end else "; continuing the sweep")
    )
    return False


def _performed(action: Action) -> str:
    """What one action did, for the stop line and the status record.

    ``mode="one"`` reports :attr:`~ai_rfc.driver.stop.StopReason
    .action_performed`, which names the *kind* of stop and not the work — so
    without this the only durable account of what an ``ai-rfc next`` did would
    be the progress lines in a terminal an operator has since closed.

    Three kinds reach here and no others: a ``stop`` returns from ``_finish``
    above and a ``gate`` from the branch before it, so the only actions that
    fall through to the bottom of the loop are the three this covers. That is
    why there is no defensive fallback — one would fabricate a description
    rather than report an impossible state.

    Args:
        action: The action the loop just performed.

    Returns:
        One line, with no line break of its own. A cluster id is
        agent-written, so it is escaped where it is printed
        (:func:`report` → :func:`~ai_rfc.driver.printable`) and JSON-encoded
        where it is recorded; this function does not escape it a third time.
    """
    if action.kind == "stage":
        return f"stage {action.stage}"
    if action.kind == "consolidation":
        assert action.round_due is not None  # noqa: S101 - plan_next names one
        return f"consolidation round {action.round_due.ordinal:02d}"
    assert action.cluster is not None  # noqa: S101 - a session names its cluster
    return f"session for cluster {action.cluster.id}"


def _finish(
    cfg: Any,
    workspace: Path,
    run_dir: Path | None,
    action: Action,
    swept: _Sweep,
    *,
    config_path: Path,
    known_clusters: tuple[str, ...],
    strict_findings: bool = False,
    until: str | None = None,
    mode: str = "all",
) -> int:
    """Write the status record, print the ledger and the resume line, exit.

    The status is written **here and nowhere else**, and never from a
    ``finally``: its absence is what marks a run interrupted, so a sweep that
    wrote it unconditionally would make a killed run indistinguishable from a
    finished one and leave the next resume nothing to move aside.

    Args:
        cfg: The validated configuration, for the window the ledger line is
            counted over.
        workspace: The workspace root.
        run_dir: The run's directory, or None when no session ever launched.
        action: The ``stop`` action, carrying the reason and any cluster.
        swept: The loop's state.
        config_path: The configuration the resume line names.
        known_clusters: The timeline's ids, from the observation this stop was
            planned from. The membership guard is only worth running against
            the real set: checking a halted cluster's id against a set built
            from that same id would certify nothing.
        strict_findings: Whether ``check --strict`` reported the findings.
        until: The bound this invocation was given, for the resume line.
        mode: The sweep's mode, for the resume line's verb.

    Returns:
        The process exit code.
    """
    reason = action.reason or StopReason.done
    code = exit_code_for(reason, strict_findings=strict_findings)
    if run_dir is not None:
        record.write_status(
            run_dir,
            {
                "run_id": run_dir.name,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "reason": reason.value,
                "detail": action.detail,
                "exit_code": code,
                "sessions": swept.sessions_run,
                "timed_out": swept.any_timeout,
                "spent_usd": record.spent(workspace),
            },
        )
    if action.detail:
        report(f"{reason.value}: {action.detail}")
    else:
        report(f"{reason.value}")
    _report_ledger(workspace, cfg)
    if reason is not StopReason.done:
        try:
            report(
                "resume: "
                + resume_for(
                    action, config_path, known_clusters, until=until, mode=mode
                )
            )
        except DriverError as error:
            # A resume line that cannot be rendered must not replace the stop
            # it was describing. The reason and the ledger are already printed.
            report(f"note: no resume line could be rendered: {error}")
    return code


def run(
    cfg: Any,
    workspace: Path,
    *,
    mode: str = "all",
    until: str | None = None,
    retry: str | None = None,
    config_path: Path | None = None,
    deadline: float | None = None,
) -> int:
    """Perform everything that is next, until something stops it.

    Args:
        cfg: The validated configuration. It must declare ``sessions``: a
            configuration without one keeps the agent boundary, and a
            hand-mined workspace stays possible.
        workspace: The workspace root.
        mode: ``"all"`` to sweep, ``"one"`` to perform exactly one action —
            which is what ``ai-rfc next`` is. A ``"one"`` that performed its
            action stops with
            :attr:`~ai_rfc.driver.stop.StopReason.action_performed`; a
            ``"one"`` whose row was a *stop* carries that stop's own reason,
            so the verb never reports success for work it could not do.
        until: A bound: a stage name, ``cluster:<id>`` or ``ordinal:<n>``.
        retry: A cluster whose already-spent attempts this invocation forgives
            (D59). The rows stay on disk; only the cap changes.
        config_path: The configuration the resume line names. Defaults to the
            workspace's sealed copy, which is a path ``--config`` accepts;
            a caller that knows what the operator typed should pass it.
        deadline: A ``time.monotonic`` value the sweep must finish by. Nothing
            in a ``recon.yaml`` supplies one today.

    Returns:
        0 when the sweep finished, its bound was reached, or ``mode="one"``
        performed its action; 1 when it stopped with work it could not do;
        3 when ``check --strict`` reported findings.

    Raises:
        DriverError: If no sessions are configured, the mode is not one of the
            two, or a bound or a retry names nothing.
        KeyboardInterrupt: Straight through. An interrupted run writes no
            status record, which is the whole of the resume contract.
    """
    if cfg.sessions is None:
        raise DriverError(
            "no sessions are configured; add a sessions: block to let a sweep "
            "drive model sessions, or stop at the agent boundary"
        )
    if mode not in ("all", "one"):
        raise DriverError(f"{mode!r} is not a mode; use 'all' or 'one'")
    resume_path = config_path or workspace / CONFIG_FILE
    move_leftovers_aside(workspace)
    forgiven = {retry: record.attempts(workspace, retry)} if retry is not None else None
    swept = _Sweep()
    run_dir: Path | None = None
    prompt_path: Path | None = None
    while True:
        obs = observe(
            workspace,
            cfg,
            deadline=deadline,
            shortfall=swept.shortfall,
            last_error=swept.last_error,
            launches=swept.launches,
            attempted_rounds=swept.attempted_rounds,
            forgiven=forgiven,
        )
        if _bound_reached(until, obs):
            # Through `_finish` like every other stop, and for the reason
            # `_finish` exists: it is the only writer of `status.json`, and the
            # absence of that file is what `move_leftovers_aside` reads as
            # *this run was killed*. Returning early here meant a bounded
            # `run --until cluster:c1` that had launched real sessions left a
            # run directory the **next** invocation renamed
            # `.interrupted-interrupt`, and said nothing about what it did.
            assert until is not None  # noqa: S101 - a None bound is never reached
            return _finish(
                cfg,
                workspace,
                run_dir,
                Action(
                    "stop",
                    reason=StopReason.bound_reached,
                    detail=printable(until),
                ),
                swept,
                config_path=resume_path,
                known_clusters=obs.known_clusters,
                until=until,
                mode=mode,
            )
        action = plan_next(obs, cfg)
        if action.kind == "stop":
            return _finish(
                cfg,
                workspace,
                run_dir,
                action,
                swept,
                config_path=resume_path,
                known_clusters=obs.known_clusters,
                until=until,
                mode=mode,
            )
        if action.kind == "gate":
            reason, strict = _build_gate(cfg, workspace)
            return _finish(
                cfg,
                workspace,
                run_dir,
                Action("stop", reason=reason),
                swept,
                config_path=resume_path,
                known_clusters=obs.known_clusters,
                strict_findings=strict,
                until=until,
                mode=mode,
            )
        if action.kind == "stage":
            assert action.stage is not None  # noqa: S101 - plan_next names one
            result = perform(BY_NAME[action.stage], workspace_from(workspace))
            if not result.ok:
                return _finish(
                    cfg,
                    workspace,
                    run_dir,
                    Action(
                        "stop",
                        reason=StopReason.stage_failed,
                        detail=f"{action.stage} exited {result.exit_code}",
                    ),
                    swept,
                    config_path=resume_path,
                    known_clusters=obs.known_clusters,
                    until=until,
                    mode=mode,
                )
            report(f"performed: {action.stage}")
        else:
            if run_dir is None:
                # The last moment before anything is spent, and deliberately
                # not the top of this function: a malformed `--until` or
                # `--retry` is the operator's own typo and is refused by
                # `observe` above, so checking the environment first would
                # answer a typo with "provision a toolchain". Everything
                # between here and there is free and idempotent.
                _require_toolchain(cfg)
                run_dir, prompt_path = _open_run(workspace, cfg, obs)
            assert prompt_path is not None  # noqa: S101 - written beside run_dir
            if action.kind == "consolidation":
                recorded = _run_consolidation(
                    cfg, workspace, run_dir, swept, obs, action
                )
                if not action.at_end:
                    assert action.round_due is not None  # noqa: S101
                    swept.attempted_rounds.add(action.round_due.ordinal)
                elif not recorded:
                    return _finish(
                        cfg,
                        workspace,
                        run_dir,
                        Action(
                            "stop",
                            reason=StopReason.consolidation_failed,
                            detail="the sweep-end round recorded no revision",
                        ),
                        swept,
                        config_path=resume_path,
                        known_clusters=obs.known_clusters,
                        until=until,
                        mode=mode,
                    )
            else:
                assert action.cluster is not None  # noqa: S101 - a session's row
                report(
                    f"cluster {action.cluster.id} (ordinal "
                    f"{action.cluster.ordinal}): attempt "
                    f"{obs.attempts + 1} of {cfg.sessions.attempts_per_cluster}, "
                    f"${cfg.sessions.budget_usd - obs.spent_usd:.2f} left"
                )
                _run_one_session(
                    cfg,
                    workspace,
                    run_dir,
                    prompt_path,
                    swept,
                    obs,
                    kind="cluster",
                    task=render.render_task(
                        (action.cluster.ordinal, action.cluster.ordinal)
                    ),
                    task_template=render.TASK_TEMPLATE,
                    cluster=action.cluster,
                )
        if mode == "one":
            # Through `_finish` like every other stop, and for the reason the
            # bounded stop above gives: it is the only writer of `status.json`,
            # and the absence of that file is what `move_leftovers_aside`
            # reads as *this run was killed*. Returning early here meant every
            # `ai-rfc next` that launched a session left a run directory the
            # **next** `next` renamed `.interrupted-interrupt`, and said
            # neither what it had done nor what to type after it.
            return _finish(
                cfg,
                workspace,
                run_dir,
                Action(
                    "stop",
                    reason=StopReason.action_performed,
                    detail=_performed(action),
                ),
                swept,
                config_path=resume_path,
                known_clusters=obs.known_clusters,
                until=until,
                mode=mode,
            )
