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
from typing import Any, Mapping

from ai_rfc.draft.build import BuildReport, build, load_toolchain
from ai_rfc.draft.checkpoint import MANIFEST_FILE
from ai_rfc.draft.gate import (
    GateError,
    _checkpoint_dir,
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
#: Nothing was at the path. A recorded revision always had a checkpoint —
#: `record_revision` refuses to record one until its `checkpoint.json` exists
#: (`server/core/revisions.py:67-71`), and the checkpoint step writes
#: `manifest.yaml` beside it — so no kill between tagging and checkpointing
#: reaches here. Two things do: a workspace whose checkpoint went missing after
#: it was recorded, and a consolidation whose recorded `checkpoint` path
#: `_checkpoint_dir` resolves elsewhere than the writer guarded, since it keeps
#: only the basename and the writer accepts any workspace-relative path.
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
        # Why those metrics are None, for a reader who has only the row.
        "manifest_error": report.manifest_error,
    }


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


def revision_lints(workspace: Path) -> list[dict[str, Any]]:
    """Lint every revision a run tagged, each against its own frozen manifest.

    A frozen manifest that is absent, or that is there and will not load, is
    reported as a lint finding rather than raised: the draft at that tag is
    still worth measuring, and the lint has a parameter for exactly this.
    :func:`_frozen_manifest` says which of the two happened.

    A tag the draft repository will not yield a draft at is reported the same
    way, by :func:`_draft_at`. There is no text to lint in that case, so every
    metric in the row is ``None`` rather than the zeros an empty draft would
    score: an absent revision must not read as an empty one.

    Args:
        workspace: The run's workspace, holding ``revisions.yaml``, the
            checkpoint roots and the nested ``draft`` repository.

    Returns:
        One record per revision in revision-number order, carrying the tag,
        number, cluster id and kind from the revision map, the
        ``manifest_status`` its frozen manifest resolved to, the
        ``draft_status`` its tag resolved to with the ``draft_error`` that
        explains it, and the metrics :func:`reduce_lint` projects.

    Raises:
        GateError: If the revision map is malformed.
        OSError: If the revision map cannot be read, a frozen manifest is
            there and cannot be opened, or git cannot be invoked.
    """
    draft_repo = workspace / "draft"
    checkpoints = workspace / "checkpoints"
    consolidations = workspace / "consolidations"
    rows: list[dict[str, Any]] = []
    # `load_revisions` returns its entries ordered by revision number.
    for entry in load_revisions(workspace / "revisions.yaml"):
        frozen_path = _checkpoint_dir(entry, checkpoints, consolidations)
        frozen, error, manifest_status = _frozen_manifest(frozen_path / MANIFEST_FILE)
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
    return rows


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


def final_build(
    workspace: Path, toolchain_path: str | None, out: Path
) -> dict[str, Any] | None:
    """Build the draft as the run last tagged it, and reduce the report.

    The run's own sealed reference cache is used when it has one, so the build
    reproduces from the references that run was given rather than from
    whatever the toolchain's shared cache holds now.

    ``out`` is the caller's to choose and is never inside the run directory: a
    run directory is evidence, and writing a build into it would edit the
    thing being measured.

    Args:
        workspace: The run's workspace.
        toolchain_path: The path a campaign froze in ``Campaign.toolchain``,
            or None when it froze none.
        out: Directory to receive ``build/``; supplied by the caller.

    Returns:
        ``exit_code``, ``findings``, ``idnits``, ``broken_references`` and
        ``diagnostic_counts``; or None when the campaign froze no toolchain.

    Raises:
        BuildError: If the toolchain record is unreadable or the ref does not
            resolve to a single draft.
        OSError: If the build's own tools cannot be invoked.
    """
    if toolchain_path is None:
        return None
    sealed = workspace / REFCACHE_DIR
    return _reduce_build(
        build(
            workspace / "draft",
            toolchain=load_toolchain(Path(toolchain_path)),
            out=out,
            ref=latest_tag(workspace),
            refcache=sealed if sealed.is_dir() else None,
        )
    )


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
