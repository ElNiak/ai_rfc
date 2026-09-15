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
from ai_rfc.draft.gate import _checkpoint_dir, draft_text, latest_tag, load_revisions
from ai_rfc.draft.lint import LintReport, lint
from ai_rfc.schema import SchemaError, load

from ..lifecycle.workspace import REFCACHE_DIR
from .report import _cell, _fmt, _separator

#: The comparison table's columns. It is also what sizes the separator: the
#: labels go through ``_cell``, which escapes a pipe rather than removing it,
#: and ``_separator`` counts pipes — so a label carrying one would otherwise
#: buy the table a fifth column the header does not have.
_RAILS = "| metric | before | after | delta |"

#: The metrics :func:`~ai_rfc.draft.lint.lint` can only compute with a manifest
#: in hand. When the manifest could not be read they are not zero and not
#: empty — they are unmeasured, and the table must not let a reader mistake
#: the one for the other.
_MANIFEST_DEPENDENT = frozenset(
    {
        "citations.uncited",
        "citations.cited_fraction",
        "structures.defined",
        "structures.rendered",
    }
)


def reduce_lint(report: LintReport) -> dict[str, Any]:
    """Project one lint report down to the numbers an instrument aggregates.

    The keys are the report's own field names, so a caller reads
    ``row["citations"]["uncited"]`` and not a flattened spelling of it.
    ``structures`` is the one that is not a field: the lint carries it under
    ``extra``, and it is lifted here to sit beside the rest.

    Only scalars and lists come out. ``LintReport.findings`` is a tuple, and a
    tuple anywhere in this payload deserialises as a list, so an analysis
    written to disk and read back would no longer equal the value in hand.

    Args:
        report: What :func:`ai_rfc.draft.lint.lint` returned.

    Returns:
        The projection, nested one level under the report's field names.
    """
    structures = report.extra.get("structures", {})
    return {
        "sections": {"missing": list(report.sections["missing"])},
        "abstract": {"word_count": report.abstract["word_count"]},
        "keywords": {"must_fraction": report.keywords["must_fraction"]},
        "blocks": {
            "figures": report.blocks["figures"],
            "tables": report.blocks["tables"],
        },
        "citations": {
            "tokens": report.citations["tokens"],
            "uncited": list(report.citations["uncited"]),
            "cited_fraction": report.citations["cited_fraction"],
        },
        "structures": {
            "defined": structures.get("defined", 0),
            "rendered": structures.get("rendered", 0),
        },
        "narration_count": len(report.narration),
        "finding_count": len(report.findings),
        # Without this every manifest-dependent metric above is indistinguishable
        # from a clean measurement: `lint` initialises `uncited` to `[]` and
        # `cited_fraction` to None, and only populates them when it was handed a
        # manifest. A reduction that dropped the reason would report "cited
        # everything" about a revision whose manifest it never read.
        "manifest_error": report.manifest_error,
    }


def revision_lints(workspace: Path) -> list[dict[str, Any]]:
    """Lint every revision a run tagged, each against its own frozen manifest.

    A frozen manifest that cannot be read is reported as a lint finding rather
    than raised: the draft at that tag is still worth measuring, and the lint
    has a parameter for exactly this. A tag missing from the draft repository
    is not handled here — that is the citation gate's finding, and hiding it
    would make an absent revision look like an empty one.

    Args:
        workspace: The run's workspace, holding ``revisions.yaml``, the
            checkpoint roots and the nested ``draft`` repository.

    Returns:
        One record per revision in revision-number order, carrying the tag,
        number, cluster id and kind from the revision map and the metrics
        :func:`reduce_lint` projects.

    Raises:
        GateError: If the revision map is malformed or a tag cannot be read
            out of the draft repository.
        OSError: If the revision map cannot be read.
    """
    draft_repo = workspace / "draft"
    checkpoints = workspace / "checkpoints"
    consolidations = workspace / "consolidations"
    rows: list[dict[str, Any]] = []
    # `load_revisions` returns its entries ordered by revision number.
    for entry in load_revisions(workspace / "revisions.yaml"):
        frozen_path = _checkpoint_dir(entry, checkpoints, consolidations)
        try:
            frozen, error = load(frozen_path / MANIFEST_FILE), None
        except (SchemaError, OSError) as failure:
            frozen, error = None, str(failure)
        name, text = draft_text(draft_repo, entry.tag)
        report = lint(
            text,
            manifest=frozen,
            manifest_error=error,
            source={"path": name, "ref": entry.tag},
        )
        rows.append(
            {
                "tag": entry.tag,
                "number": entry.number,
                "cluster_id": entry.cluster_id,
                "kind": entry.kind,
                **reduce_lint(report),
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
    text = _fmt(change)
    return f"+{text}" if change > 0 else text


def _measured(record: Mapping[str, Any], name: str) -> Any:
    """One metric's value, or None when its record never read a manifest.

    A revision whose frozen manifest could not be read has an empty ``uncited``
    and a zero ``defined`` because nothing was compared, not because nothing
    was wrong. Rendering those as numbers would report "no change" about
    something the instrument never measured.
    """
    if name in _MANIFEST_DEPENDENT and record.get("manifest_error"):
        return None
    return record.get(name)


def compare_lints(
    before: dict, after: dict, *, before_label: str, after_label: str
) -> str:
    """Render two reduced lint records side by side as a Markdown table.

    Both records are flattened to dotted metric names first, so a nested
    projection and a flat one compare the same way. Every cell goes through
    the campaign report's own escaper: a metric name carrying a pipe must not
    be able to add a column, and the values are agent-controlled prose in the
    end.

    A record carrying a ``manifest_error`` renders the metrics that needed the
    manifest as the em dash rather than as a number, and the error itself is a
    row of its own, so an unmeasured revision cannot be read as a clean one.

    Args:
        before: A reduced lint record, nested or flat.
        after: The record to compare it against.
        before_label: Column heading for ``before`` — usually a revision tag.
        after_label: Column heading for ``after``.

    Returns:
        The table, header row first, one row per metric either side carries.
    """
    first, second = _flatten(before), _flatten(after)
    header = f"| metric | {_cell(before_label)} | {_cell(after_label)} | delta |"
    lines = [header, _separator(_RAILS)]
    for name in sorted(set(first) | set(second)):
        before_value = _measured(first, name)
        after_value = _measured(second, name)
        lines.append(
            f"| {_cell(name)} | {_cell(_shown(before_value))} "
            f"| {_cell(_shown(after_value))} "
            f"| {_cell(_delta(before_value, after_value))} |"
        )
    return "\n".join(lines) + "\n"
