"""Draft quality across one run's revisions, and one build of its last.

The instrument answers two questions a campaign aggregate cannot: how the
prose moved between revisions, and whether the draft the run finally left
behind compiles. Both are read out of the workspace a run produced, so nothing
here launches a session or edits a draft.

Each revision is measured against the manifest *its own* checkpoint froze, not
against the live one. That is the whole point: a claim the third cluster mined
was not available to be cited in the first revision, so linting revision one
against the final manifest would report every later cluster's claims as
uncited and score an early draft down for prose it could not have written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Collection, Mapping

import yaml

from ai_rfc.draft.build import BuildError, BuildReport, build, load_toolchain
from ai_rfc.draft.checkpoint import MANIFEST_FILE
from ai_rfc.draft.gate import (
    GateError,
    _checkpoint_dir,
    _cluster_ordinals,
    draft_text,
    latest_tag,
    load_revisions,
)
from ai_rfc.draft.lint import LintReport, lint
from ai_rfc.schema import SchemaError, load

from ..lifecycle.workspace import REFCACHE_DIR
from ..models import Manifest
from .markdown import cell, fmt, separator

#: The frozen manifest loaded.
MANIFEST_READ = "read"
#: Nothing was at the path: any entry whose resolved checkpoint directory holds
#: no `manifest.yaml`, however it came to. Stated as that predicate and not as a
#: list of causes, because the causes are open — a checkpoint deleted after it
#: was recorded, a consolidation whose `checkpoint` path `_checkpoint_dir`
#: resolves elsewhere than the writer guarded, and an entry written straight
#: into the map, which bypasses the writer altogether, all reach it — as does
#: an entry naming a cluster the run's timeline does not have, which has no
#: directory to resolve *to*: `_frozen_for` reports that refusal here rather
#: than resolving it, because a climbing or absolute id would leave the
#: checkpoint root and be linted as though it were this revision's. What is
#: ruled *out* is a kill between tagging and checkpointing: `record_revision`
#: will not record a revision until its `checkpoint.json` exists
#: (`server/core/revisions.py:67-71`), and the checkpoint step writes
#: `manifest.yaml` beside it, so a recorded revision always had one.
MANIFEST_MISSING = "missing"
#: The document was there and the schema refused it.
MANIFEST_UNLOADABLE = "unloadable"

#: The draft at the tag was read out of the draft repository.
DRAFT_READ = "read"
#: The revision map registers the tag and the draft repository yields no draft
#: at it. A run killed between recording the entry and running `git tag` leaves
#: exactly this — and leaves the checkpoint *present*, since that is written
#: before the entry can be recorded at all.
DRAFT_UNREADABLE = "unreadable"

#: The revision map loaded. Its revisions may still be zero: a workspace no run
#: has tagged in yet reads `revisions: {}`, and that is a measurement.
REVISIONS_READ = "read"
#: Nothing was at the path. Incomplete evidence rather than a broken
#: instrument: `ledger._entries`, a sibling reader of the same file, already
#: treats absence as a legitimate state, and an arm can delete it — which is
#: why `revisions.yaml` is in `audit.STATE_FILES` (R29).
REVISIONS_MISSING = "missing"
#: The map is there and could not be turned into revisions — the parser
#: refused the bytes, or `load_revisions` refused the shape — so how many
#: revisions the run has is unknown. Arms hand-edit `revisions.yaml` — it is in
#: `audit.STATE_FILES` for that reason — so this is reachable by an agent, not
#: only by a damaged disk.
REVISIONS_UNREADABLE = "unreadable"

#: No build was asked for. The default, and the reason `build` is null on
#: almost every analysis ever written: `analyze` only builds under `--build`.
BUILD_NOT_REQUESTED = "not requested"
#: A build was asked for and the campaign froze no toolchain to run one with,
#: so there was nothing to build against. A property of the campaign, not of
#: the run — every run of such a campaign reports it.
BUILD_NO_TOOLCHAIN = "no toolchain"
#: The build ran and produced a report. It says nothing about whether the
#: draft compiled: a build that exits non-zero, or that idnits filled with
#: findings, is still a build that was measured, and `exit_code` is the column
#: that answers that question.
BUILD_BUILT = "built"
#: The build refused to start, and `build_error` says why. Stated as that
#: predicate and not as a list of causes, because the causes are open: every
#: `BuildError` the instrument meets before it has a report lands here, among
#: them an unreadable toolchain record, one that lacks a required key, a ref
#: that resolves to no commit or to other than exactly one draft at it, and a
#: failed clone or checkout of the draft repository. What is ruled *out* is a
#: build that ran: one that exits non-zero is `BUILD_BUILT`, and `exit_code`
#: says so. Reported rather than raised for the reason `_draft_at`,
#: `_frozen_manifest` and `_revision_map` carry (R31).
BUILD_FAILED = "failed"


def build_tally(runs: Mapping[str, Mapping[str, Any]]) -> str:
    """How a campaign's builds ended, counted by status.

    Every status is counted rather than the successes alone. A campaign that
    froze no toolchain reports :data:`BUILD_NO_TOOLCHAIN` for every run, and a
    line counting built and failed would say ``0 built, 0 failed`` about it —
    which reads as nothing having gone wrong, of a campaign that measured
    nothing. Counting the whole set is what keeps the line from re-introducing
    at the summary what R31 removed from the exit code.

    The order is fixed rather than the order the runs happen to arrive in, so
    two campaigns' lines can be compared term by term.

    Args:
        runs: The aggregate's ``runs``. A run archived before the build
            instrument existed carries no ``quality`` and falls under no
            status, so it is counted in none of the terms.

    Returns:
        The non-zero counts, such as ``2 built, 1 failed``, or
        ``nothing reported`` when no run carries a status at all.
    """
    counts: dict[Any, int] = {}
    for result in runs.values():
        status = (result.get("quality") or {}).get("build_status")
        counts[status] = counts.get(status, 0) + 1
    terms = [
        f"{counts[status]} {status}"
        for status in (
            BUILD_BUILT,
            BUILD_FAILED,
            BUILD_NO_TOOLCHAIN,
            BUILD_NOT_REQUESTED,
        )
        if counts.get(status)
    ]
    return ", ".join(terms) or "nothing reported"


def _unmeasured(measured: Any) -> Any:
    """The same shape with every leaf ``None``.

    Deriving the unmeasured payload from the measured one is what keeps a
    metric added to the projection later correct without a second edit
    somewhere else.

    Args:
        measured: A metric, or a mapping of them, as it would be reported.

    Returns:
        ``None`` for a metric, and for a mapping the same keys over ``None``.
    """
    if isinstance(measured, dict):
        return {key: _unmeasured(value) for key, value in measured.items()}
    return None


def reduce_lint(report: LintReport) -> dict[str, Any]:
    """Project one lint report down to the numbers an instrument aggregates.

    The keys are the report's own field names, so a caller reads
    ``row["citations"]["uncited"]`` and not a flattened spelling of it.
    ``structures`` is the one that is not a field: the lint carries it under
    ``extra``, and it is lifted here to sit beside the rest.

    Only scalars, ``None`` and lists come out. ``LintReport.findings`` is a
    tuple, and a tuple anywhere in this payload deserialises as a list, so an
    analysis written to disk and read back would no longer equal the value in
    hand.

    Args:
        report: What :func:`ai_rfc.draft.lint.lint` returned.

    Returns:
        The projection, nested one level under the report's field names. Every
        metric the manifest fed is ``None`` when there was no manifest to feed
        it, so a consumer needs no list of which those are. ``finding_count``
        is one of them: most of what it counts cannot be looked for without a
        manifest. What *can* be looked for without one is projected in its own
        right, so an unmeasured row still carries every prose signal the text
        alone shows.
    """
    structures = report.extra.get("structures", {})
    # What `lint` can only answer with a manifest in hand. Without one they are
    # not zero and not empty — `lint` initialises `uncited` to `[]` and
    # `cited_fraction` to None and fills neither — so a reduction that emitted
    # the initial values would report "cited everything" about a revision whose
    # manifest it never read. Nulling the whole block, rather than a list of
    # metric names kept somewhere else, is what makes a metric added here later
    # unmeasured-correct without a second edit.
    #
    # `finding_count` belongs in here for the same reason, which is less
    # obvious: five of the classes `LintReport.findings` draws need the
    # manifest — an unknown citation, an unrendered, stale or unknown
    # structure, and an unbound data-model claim — and every one of them is
    # structurally empty without it. The count that survives is not a smaller
    # count of the same thing, it is a count of the checks that still ran, and
    # nothing in a plain int says so. Measured: one text scores 3 with its
    # manifest and 1 without, which reads as a two-point improvement.
    #
    # Nulling it would otherwise cost the four findings that need no manifest —
    # the stub-abstract flag, the reference totals, a figure with no caption
    # citation, and a malformed delimiter, which `lint` reads out even with no
    # manifest because a broken delimiter is a draft-syntax defect no manifest
    # is needed to see. Each is projected in its own right below, in the
    # always-measured group, so an unmeasured row keeps every prose signal it
    # can still measure (R21).
    from_manifest: dict[str, Any] = {
        "citations": {
            "uncited": list(report.citations["uncited"]),
            "cited_fraction": report.citations["cited_fraction"],
        },
        "structures": {
            "defined": structures.get("defined", 0),
            "rendered": structures.get("rendered", 0),
        },
        # `findings` prepends a line of its own whenever `manifest_error` is
        # set. It needs no subtracting here: the count is nulled in exactly the
        # case that line exists.
        "finding_count": len(report.findings),
    }
    if report.manifest_error is not None:
        from_manifest = _unmeasured(from_manifest)
    # Copied a record at a time: the report's own dicts are mutable, and this
    # payload is a value a caller may store.
    uncaptioned = [dict(f) for f in report.blocks["figures_without_caption_citation"]]
    return {
        "sections": {"missing": list(report.sections["missing"])},
        "abstract": {
            "is_stub": report.abstract["is_stub"],
            "word_count": report.abstract["word_count"],
        },
        # The whole field, not a choice of its keys: `lint` returns
        # ``dict[str, int]`` here, and choosing would be one more thing to
        # revisit when it counts something else.
        "references": dict(report.references),
        "keywords": {"must_fraction": report.keywords["must_fraction"]},
        "blocks": {
            "figures": report.blocks["figures"],
            "tables": report.blocks["tables"],
            "figures_without_caption_citation": uncaptioned,
        },
        "citations": {
            "tokens": report.citations["tokens"],
            **from_manifest["citations"],
        },
        "structures": {
            "malformed": list(structures.get("malformed", ())),
            **from_manifest["structures"],
        },
        "narration_count": len(report.narration),
        "finding_count": from_manifest["finding_count"],
        # Why those metrics are None whenever it is set, for a reader who has
        # only the row — and the one null it does not explain: `cited_fraction`
        # is null with no error to report whenever `_citations` had no claims
        # to divide by (`lint.py:341,348-349`). Stated as that predicate and
        # not as the manifest that loaded and declares none, because a report
        # linted with no manifest at all satisfies it too. It is the only one
        # of them that can be; the rest are a list and three ints.
        "manifest_error": report.manifest_error,
    }


def _known_clusters(timeline_dir: Path) -> tuple[frozenset[str], str | None]:
    """Which clusters this run's timeline has, and why the set may be empty.

    The set is what makes the checkpoint join checkable: an entry naming a
    cluster the timeline does not have has no checkpoint directory under this
    root, whatever it spells.

    Reported rather than raised, the fourth arm of the ruling
    :func:`_frozen_manifest`, :func:`_draft_at` and :func:`_revision_map`
    carry: a run whose timeline is gone or half-written is evidence about that
    run, and this reducer sits inside
    :func:`~ai_rfc.experiment.metrics.analyze_campaign`'s comprehension, where
    a raise costs every other run its analysis. An empty set is therefore the
    honest answer to "which clusters does this run have?" for a run that has
    no readable timeline, and the reason is carried beside it so a row can say
    which of the two emptinesses it met.

    Args:
        timeline_dir: The run's ``timeline`` directory.

    Returns:
        The cluster ids, and the reason the set is empty when it is empty
        because the timeline could not be read rather than because it holds no
        clusters.
    """
    try:
        return frozenset(_cluster_ordinals(timeline_dir)), None
    except (OSError, ValueError, KeyError) as failure:
        return frozenset(), f"{timeline_dir}: {failure!r}"


def _frozen_for(
    entry: Any,
    checkpoints: Path,
    consolidations: Path,
    known: Collection[str],
    timeline_error: str | None,
) -> tuple[Manifest | None, str | None, str]:
    """The manifest this revision froze, or why there is none to read.

    Two ways there is none, reported as the one status because the
    measurement is the same — this revision has no frozen manifest — and the
    message says which: the directory resolved and held nothing, or the entry
    names a cluster the timeline does not have, so no directory under the
    checkpoint root is its checkpoint's and resolving one would leave the root
    (an absolute id replaces it outright).

    Args:
        entry: The revision whose frozen manifest is wanted.
        checkpoints: Root directory of the cluster checkpoints.
        consolidations: Root directory of the consolidation checkpoints.
        known: Every cluster id this run's timeline holds.
        timeline_error: Why ``known`` is empty, when it is empty because the
            timeline could not be read.

    Returns:
        What :func:`_frozen_manifest` returns, or ``None``, the refusal and
        :data:`MANIFEST_MISSING` when the entry has no directory to resolve.

    Raises:
        OSError: What :func:`_frozen_manifest` raises.
    """
    try:
        directory = _checkpoint_dir(entry, checkpoints, consolidations, known=known)
    except GateError as refusal:
        reason = str(refusal)
        if timeline_error is not None:
            # Both facts, because they are different repairs: the entry may be
            # fine and the timeline gone.
            reason = f"{reason}; the timeline could not be read ({timeline_error})"
        return None, reason, MANIFEST_MISSING
    return _frozen_manifest(directory / MANIFEST_FILE)


def _frozen_manifest(path: Path) -> tuple[Manifest | None, str | None, str]:
    """The manifest a checkpoint froze, or why it could not be read.

    Three outcomes, kept apart because two of them are evidence about the run
    and the third is a broken instrument. ``missing`` is reported and the
    analysis continues for the reason ``unloadable`` is: this reads a frozen
    workspace long after the run, and one revision whose checkpoint is not
    where this function looks must not take a campaign's good ones down with
    it. What actually reaches that branch is recorded on the constant — not a
    kill mid-round, which the revision writer's ordering forbids. ``unloadable``
    is reported too: a frozen workspace is evidence that is never re-gated, so
    a pilot whose manifest predates a vocabulary change is unloadable for good,
    and raising would let one such revision take a campaign's good ones down
    with it. Every other :exc:`OSError` propagates — an instrument that cannot
    open a file at a path it has just computed is broken, and a broken
    instrument must fail loudly rather than emit zeros.

    :exc:`FileNotFoundError` is caught rather than the path tested, because
    :meth:`~pathlib.Path.exists` answers False for a permission failure too and
    would fold "broken" back into "missing".

    Args:
        path: Where the frozen ``manifest.yaml`` should be.

    Returns:
        The manifest or None, the reason or None, and which outcome fired —
        one of :data:`MANIFEST_READ`, :data:`MANIFEST_MISSING` or
        :data:`MANIFEST_UNLOADABLE`.

    Raises:
        OSError: If the document is there and still cannot be read.
    """
    try:
        return load(path), None, MANIFEST_READ
    except FileNotFoundError:
        return None, f"no frozen manifest at {path}", MANIFEST_MISSING
    except SchemaError as failure:
        return None, str(failure), MANIFEST_UNLOADABLE


def _draft_at(
    draft_repo: Path, tag: str
) -> tuple[tuple[str, str] | None, str | None, str]:
    """The draft at a tag, or why the repository would not yield one.

    Reported rather than raised, for the reason :func:`_frozen_manifest`
    records: a tag the revision map registers and the draft repository does
    not hold is evidence about the run — it is what a run killed between
    appending the entry and running ``git tag`` leaves behind — and
    :func:`~ai_rfc.experiment.metrics.analyze_campaign` builds its runs in a
    comprehension, so raising would take the aggregate for every other run in
    the campaign down with it (R23).

    Every :exc:`GateError` arm is reported, not the absent tag alone: a ref
    holding no ``draft-*.md`` or several is damaged evidence too, and the
    message says which. :exc:`OSError` still propagates — git that cannot be
    invoked is a broken instrument, not a finding about the run.

    Args:
        draft_repo: The run's nested prose-draft git repository.
        tag: The revision tag to read.

    Returns:
        The file name and text, or None; the reason or None; and which
        outcome fired — :data:`DRAFT_READ` or :data:`DRAFT_UNREADABLE`.

    Raises:
        OSError: If git cannot be invoked at all.
    """
    try:
        return draft_text(draft_repo, tag), None, DRAFT_READ
    except GateError as failure:
        return None, str(failure), DRAFT_UNREADABLE


def _revision_map(path: Path) -> tuple[tuple[Any, ...] | None, str | None, str]:
    """The revisions a run recorded, or why the map could not be read.

    The third arm of the same ruling :func:`_draft_at` and
    :func:`_frozen_manifest` carry (R27). A map an arm hand-edited into a shape
    :func:`~ai_rfc.draft.gate.load_revisions` refuses is evidence about the run
    — ``revisions.yaml`` is in :data:`~ai_rfc.experiment.audit.STATE_FILES`
    precisely because arms edit it — and raising here would abort
    :func:`~ai_rfc.experiment.metrics.analyze_campaign`'s comprehension for
    every other run in the campaign, and lose a GEPA candidate its score.

    A map that is not there at all is reported too, and for the same reason
    rather than as a widening of the rule: the instrument computed the right
    path and the file is absent, which is incomplete evidence exactly as an
    absent tag is. An arm with shell access can delete it, and
    :func:`ai_rfc.ledger._entries` — another reader of this same file — has
    always treated absence as a legitimate state (R29).

    A map that is not valid YAML joins the wrong-shape map on
    :data:`REVISIONS_UNREADABLE` (R30): the parser refusing the bytes is
    damaged evidence in the same way the loader refusing the shape is, and a
    bad hand-edit produces it more readily than a valid-YAML-wrong-shape
    document. It is guarded here although nothing reaches it through
    :func:`~ai_rfc.experiment.metrics.analyze_run` today — ``ledger`` raises
    on the same bytes first — because ``latest_tag`` and ``ledger._entries``,
    the file's other two readers, both already guard it, and this would be the
    only one that did not.

    Every other :exc:`OSError` still propagates, so R17's split stays intact
    for a path that exists and cannot be read. :exc:`FileNotFoundError` is
    caught rather than the path tested, for the reason
    :func:`_frozen_manifest` records: :meth:`~pathlib.Path.exists` answers
    False for a permission failure too, and would fold "broken" back into
    "missing".

    Args:
        path: Where the run's ``revisions.yaml`` should be.

    Returns:
        The entries or None, the reason or None, and which outcome fired —
        :data:`REVISIONS_READ`, :data:`REVISIONS_MISSING` or
        :data:`REVISIONS_UNREADABLE`. An empty tuple with
        :data:`REVISIONS_READ` is a run that has recorded no revision yet,
        which is a measurement and not an absence.

    Raises:
        OSError: If the map is there and still cannot be read.
    """
    try:
        return load_revisions(path), None, REVISIONS_READ
    except FileNotFoundError:
        return None, f"no revision map at {path}", REVISIONS_MISSING
    except GateError as failure:
        return None, str(failure), REVISIONS_UNREADABLE
    except yaml.YAMLError as failure:
        # The path is added because the parser will not name it: it reports
        # `in "<unicode string>"`, having been handed text rather than a file,
        # which would send a reader hunting for a file by that name.
        # `ledger._entries` prefixes its own YAML guard for the same reason.
        return None, f"{path}: {failure}", REVISIONS_UNREADABLE


def _unmeasured_lint() -> dict[str, Any]:
    """The projection :func:`reduce_lint` returns, with every metric ``None``.

    The keys are derived by projecting a lint of the empty string and nulling
    every leaf, never written out here, so a metric :func:`reduce_lint` grows
    later is unmeasured-correct with no second edit. The empty text is a
    source of *keys* and never of values: :func:`_unmeasured` discards every
    number it scores, so a revision with no draft reports ``None`` and not the
    zeros an empty draft would earn.

    Returns:
        The projection over ``None``.
    """
    return _unmeasured(reduce_lint(lint("")))


def revision_lints(workspace: Path) -> dict[str, Any]:
    """Lint every revision a run tagged, each against its own frozen manifest.

    Three conditions are reported rather than raised, one per level of the
    evidence, and each names the level it belongs to.

    A frozen manifest that is absent, or that is there and will not load, is
    reported as a lint finding: the draft at that tag is still worth measuring,
    and the lint has a parameter for exactly this. :func:`_frozen_manifest`
    says which of the two happened.

    A tag the draft repository will not yield a draft at is reported by
    :func:`_draft_at`. There is no text to lint in that case, so every metric
    in the row is ``None`` rather than the zeros an empty draft would score:
    an absent revision must not read as an empty one.

    A revision map that is absent, or that is there and will not load, is
    reported by :func:`_revision_map`, and it is why this returns a record
    rather than the bare list it once did. There are no rows to null in either
    case — there is nothing to enumerate — so the status belongs to the
    payload instead. ``revisions`` is then ``[]``, and ``revisions_status`` is
    the only thing separating *this run recorded none* from *this run's count
    is unknown*; a reader that takes the empty list for zero revisions is
    wrong in both of the latter cases.

    Args:
        workspace: The run's workspace, holding ``revisions.yaml``, the
            checkpoint roots and the nested ``draft`` repository.

    Returns:
        ``revisions``, ``revisions_status`` and ``revisions_error``.
        ``revisions`` holds one record per revision in revision-number order,
        carrying the tag, number, cluster id and kind from the revision map,
        the ``manifest_status`` its frozen manifest resolved to, the
        ``draft_status`` its tag resolved to with the ``draft_error`` that
        explains it, and the metrics :func:`reduce_lint` projects.

    Raises:
        OSError: If the revision map is there and cannot be read, a frozen
            manifest is there and cannot be opened, or git cannot be invoked.
            An absent map and an absent frozen manifest are both reported.
    """
    entries, revisions_error, revisions_status = _revision_map(
        workspace / "revisions.yaml"
    )
    draft_repo = workspace / "draft"
    checkpoints = workspace / "checkpoints"
    consolidations = workspace / "consolidations"
    rows: list[dict[str, Any]] = []
    # `load_revisions` returns its entries ordered by revision number.
    known, timeline_error = _known_clusters(workspace / "timeline")
    for entry in entries or ():
        frozen, error, manifest_status = _frozen_for(
            entry, checkpoints, consolidations, known, timeline_error
        )
        drafted, draft_error, draft_status = _draft_at(draft_repo, entry.tag)
        if drafted is None:
            metrics = _unmeasured_lint()
            # `manifest_error` explains the manifest and not the text, so it is
            # known whether or not there was a draft to lint. Nulling it with
            # the metrics would leave `manifest_status` unexplained.
            metrics["manifest_error"] = error
        else:
            name, text = drafted
            metrics = reduce_lint(
                lint(
                    text,
                    manifest=frozen,
                    manifest_error=error,
                    source={"path": name, "ref": entry.tag},
                )
            )
        rows.append(
            {
                "tag": entry.tag,
                "number": entry.number,
                "cluster_id": entry.cluster_id,
                "kind": entry.kind,
                "manifest_status": manifest_status,
                "draft_status": draft_status,
                "draft_error": draft_error,
                **metrics,
            }
        )
    return {
        "revisions": rows,
        "revisions_status": revisions_status,
        "revisions_error": revisions_error,
    }


def _reduce_build(report: BuildReport) -> dict[str, Any]:
    """Project one build report down to what an analysis stores."""
    counts: dict[str, int] = {}
    for diagnostic in report.diagnostics:
        severity = diagnostic["severity"]
        counts[severity] = counts.get(severity, 0) + 1
    return {
        "exit_code": report.exit_code,
        "findings": list(report.findings),
        "idnits": dict(report.idnits),
        "broken_references": list(report.broken_references),
        "diagnostic_counts": dict(sorted(counts.items())),
    }


def _build_record(
    reduced: dict[str, Any] | None, status: str, error: str | None
) -> dict[str, Any]:
    """The three keys an analysis stores about one run's build.

    Args:
        reduced: The reduced build report, or None when there is not one.
        status: One of the four ``BUILD_*`` statuses.
        error: Why the build could not start, or None.

    Returns:
        The record, ready to splat into a run's ``quality``.
    """
    return {"build": reduced, "build_status": status, "build_error": error}


def build_not_requested() -> dict[str, Any]:
    """The build record of an analysis that was not asked for a build.

    The status exists so that a null ``build`` is not ambiguous. It was null
    on every analysis before ``--build``, and a reader could not tell an
    analysis that declined to build from a campaign that had no toolchain to
    build with — the distinction Task 3 left open until something rendered it.

    Returns:
        The record, with :data:`BUILD_NOT_REQUESTED` and no build.
    """
    return _build_record(None, BUILD_NOT_REQUESTED, None)


def final_build(
    workspace: Path, toolchain_path: str | None, out: Path
) -> dict[str, Any]:
    """Build the draft as the run last tagged it, and reduce the report.

    The run's own sealed reference cache is used when it has one, so the build
    reproduces from the references that run was given rather than from
    whatever the toolchain's shared cache holds now.

    ``out`` is the caller's to choose and is never inside the run directory: a
    run directory is evidence, and writing a build into it would edit the
    thing being measured.

    A build that refuses to start is reported as :data:`BUILD_FAILED` with
    its reason in ``build_error``, and not raised — the fourth arm of the
    ruling :func:`_frozen_manifest`, :func:`_draft_at` and
    :func:`_revision_map` carry (R31) — because
    :func:`~ai_rfc.experiment.metrics.analyze_campaign` builds its runs in a
    comprehension, so raising would take the aggregate for every other run in
    the campaign down with it, including the runs that built cleanly.

    The reason is reported and not classified further. The arms do not all
    read alike: a tag the draft repository does not hold is evidence about
    the run in exactly the way an absent revision map is — an arm writes
    ``revisions.yaml``, and :func:`~ai_rfc.draft.gate.latest_tag` reads the
    ref out of it — while a toolchain record that will not load is a fact
    about the campaign, and reads as one because every run of that campaign
    reports the same message. The rest are not sorted here: doing it would
    mean parsing git's stderr, which is the trade ``draft_status`` already
    declined, so :exc:`~ai_rfc.draft.build.BuildError` is caught whole and
    ``build_error`` carries the reason to the reader.

    :exc:`OSError` still propagates, so R17's split stays intact: a build whose
    own tools cannot be invoked is a broken instrument and not a finding.

    Args:
        workspace: The run's workspace.
        toolchain_path: The path a campaign froze in ``Campaign.toolchain``,
            or None when it froze none.
        out: Directory to receive ``build/``; supplied by the caller.

    Returns:
        ``build`` — ``exit_code``, ``findings``, ``idnits``,
        ``broken_references`` and ``diagnostic_counts``, or None when no build
        was produced — with ``build_status`` and ``build_error``.

    Raises:
        OSError: If the build's own tools cannot be invoked.
    """
    if toolchain_path is None:
        return _build_record(None, BUILD_NO_TOOLCHAIN, None)
    sealed = workspace / REFCACHE_DIR
    try:
        report = build(
            workspace / "draft",
            toolchain=load_toolchain(Path(toolchain_path)),
            out=out,
            ref=latest_tag(workspace),
            refcache=sealed if sealed.is_dir() else None,
        )
    except BuildError as failure:
        return _build_record(None, BUILD_FAILED, str(failure))
    return _build_record(_reduce_build(report), BUILD_BUILT, None)


def _flatten(record: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """One nested record as dotted metric names, so two can be lined up."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _magnitude(value: Any) -> int | float | None:
    """The number a metric is compared by, or None when it has none.

    A list compares by its length: the table shows how far a count moved, and
    the items themselves are in the lint reports it was built from. A bool is
    excluded deliberately — it is an ``int`` to Python, and subtracting two
    states would read as a quantity.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, (int, float)):
        return value
    return None


def _shown(value: Any) -> Any:
    """What a metric's own column holds: a list's length, else the value."""
    return len(value) if isinstance(value, (list, tuple)) else value


def _delta(before: Any, after: Any) -> str | None:
    """The signed change between two metrics, or None when there is not one.

    A metric only one side carries has no delta rather than a zero, which
    would read as "this did not move".
    """
    first, second = _magnitude(before), _magnitude(after)
    if first is None or second is None:
        return None
    change = second - first
    text = fmt(change)
    return f"+{text}" if change > 0 else text


def compare_lints(
    before: dict, after: dict, *, before_label: str, after_label: str
) -> str:
    """Render two reduced lint records side by side as a Markdown table.

    Both records are flattened to dotted metric names first, so a nested
    projection and a flat one compare the same way. Every cell goes through
    :func:`~ai_rfc.experiment.markdown.cell`: a metric name or a label
    carrying a pipe must not be able to add a column, and the values are
    agent-controlled prose in the end.

    An unmeasured metric renders as the em dash rather than as a number, and
    it does so without this function knowing which metrics need a manifest:
    :func:`reduce_lint` writes ``None`` for what it could not measure, and
    ``None`` is already the value that has no magnitude and no delta. The
    reason is a row of its own, so the dashes are explained.

    Args:
        before: A reduced lint record, nested or flat.
        after: The record to compare it against.
        before_label: Column heading for ``before`` — usually a revision tag.
        after_label: Column heading for ``after``.

    Returns:
        The table, header row first, one row per metric either side carries.
    """
    first, second = _flatten(before), _flatten(after)
    header = f"| metric | {cell(before_label)} | {cell(after_label)} | delta |"
    lines = [header, separator(header)]
    for name in sorted(set(first) | set(second)):
        before_value = first.get(name)
        after_value = second.get(name)
        lines.append(
            f"| {cell(name)} | {cell(_shown(before_value))} "
            f"| {cell(_shown(after_value))} "
            f"| {cell(_delta(before_value, after_value))} |"
        )
    return "\n".join(lines) + "\n"
