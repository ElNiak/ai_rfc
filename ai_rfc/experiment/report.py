"""Render a campaign aggregate as markdown, every formula named."""

from __future__ import annotations

import re
from typing import Any

from ai_rfc.driver import printable

from .markdown import cell, fmt, separator


def _code(value: Any) -> str:
    """One value as a Markdown code span that its own content cannot break.

    A run of backticks inside the value is fenced by a longer run outside it,
    per CommonMark; a leading or trailing backtick needs the padding space.

    :func:`~ai_rfc.driver.printable` runs first, so a character that would end
    the line is already a visible escape by the time the fence is measured. It
    is the predicate over the whole unprintable category, which is what this
    needs: neutralising ``\\n`` alone left CR — CommonMark's other line ending
    — and five further characters :meth:`str.splitlines` breaks on. Nothing
    else is escaped, because a code span's content is literal.

    Args:
        value: Any value; ``None`` renders as the em dash, never as a span.

    Returns:
        The fenced span, or ``"—"`` for ``None``.
    """
    if value is None:
        return "—"
    text = printable(str(value))
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _arm_rows(arms: dict[str, dict[str, Any]]) -> list[str]:
    header = (
        "| arm | runs | completed (mean / min) | artifacts mean | pass^k mean | integrity | "
        "bypass | errors c1/c2 | hand edits | cost total / mean | failure-cost share | "
        "cost per completed | tokens→first | AUC mean | timeouts | nonzero exits |"
    )
    rows = [header, separator(header)]
    for arm, s in arms.items():
        rows.append(
            f"| {cell(arm)} | {cell(s['runs'])} | {cell(s['completed_fraction_mean'])} / {cell(s['completed_fraction_min'])} | "
            f"{cell(s['artifacts_fraction_mean'])} | {cell(s['pass_k_mean'])} | {cell(s['integrity_rate'])} | "
            f"{cell(s['bypass_attempts'])} | {cell(s['errors_class1'])}/{cell(s['errors_class2'])} | {cell(s['hand_edits'])} | "
            f"{cell(fmt(s['cost_total'], 2))} / {cell(fmt(s['cost_mean'], 2))} | {cell(s['failure_cost_share'])} | "
            f"{cell(fmt(s['cost_per_completed_cluster'], 2))} | {cell(fmt(s['tokens_to_first_completion_mean'], 0))} | "
            f"{cell(s['auc_mean'])} | {cell(s['timed_out_runs'])} | {cell(s['nonzero_exit_runs'])} |"
        )
    return rows


def _run_rows(runs: dict[str, dict[str, Any]]) -> list[str]:
    header = (
        "| run | arm | exit | timed out | completed/window | artifacts | "
        "gates m/c | cost | turns | tokens | duration ms | integrity | bypass | "
        "errors c1/c2 |"
    )
    rows = [header, separator(header)]
    for run_id, r in runs.items():
        usage = r["cost"].get("usage") or {}
        tokens = sum(
            int(usage.get(k, 0) or 0)
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        audit = r.get("audit") or {}
        completed = sum(1 for c in r["clusters"] if c.get("completed"))
        artifacts = sum(1 for c in r["clusters"] if c.get("artifacts"))
        rows.append(
            f"| {cell(run_id)} | {cell(r['arm'])} | {cell(r['status']['exit_code'])} | {cell(r['status']['timed_out'])} | "
            f"{cell(completed)}/{cell(r['window_size'])} | {cell(artifacts)} | {cell(r['gates']['manifest_exit'])}/{cell(r['gates']['citation_exit'])} | "
            f"{cell(fmt(r['cost'].get('total_cost_usd'), 2))} | {cell(r['cost'].get('num_turns'))} | {cell(tokens)} | "
            f"{cell(r['cost'].get('duration_ms'))} | {cell(audit.get('integrity'))} | "
            f"{cell((audit.get('bypass_attempts') or {}).get('count'))} | "
            f"{cell((audit.get('errors') or {}).get('class1'))}/{cell((audit.get('errors') or {}).get('class2'))} |"
        )
    return rows


def _count(value: Any) -> int | None:
    """How many, or None when there was nothing to count.

    ``len`` of an unmeasured metric raises, and a zero written in its place
    would read as a measured emptiness — which is the one thing this report
    must never say about something nothing looked at.

    Args:
        value: A list the analysis measured, or None.

    Returns:
        Its length, or None.
    """
    return None if value is None else len(value)


def _quality_run_rows(runs: dict[str, dict[str, Any]]) -> list[str]:
    """One row per run: whether its revision map enumerated, and its build.

    Split from the per-revision table below because these are facts about the
    map rather than about any revision in it. A run whose map would not load
    has no revisions to put in a row, and it is exactly the run a reader most
    needs to see.

    Each of the two instruments gets a status column and an error column
    beside it, for the same reason: the three measured build columns are null
    on a run that was never asked for a build, on a campaign that froze no
    toolchain, and on a build that could not start, and only the status tells
    those apart.

    Args:
        runs: The aggregate's ``runs``.

    Returns:
        The header, the separator, and one row per run.
    """
    header = (
        "| run | revisions | map | map error | build | build error | "
        "build exit | build findings | broken refs |"
    )
    rows = [header, separator(header)]
    for run_id, result in runs.items():
        # Guarded: an aggregate archived before the instrument existed carries
        # runs with no `quality` at all, and it still has to render.
        quality = result.get("quality") or {}
        status = quality.get("revisions_status")
        # Not `len(revisions)`. The list is empty both for a run that recorded
        # no revision and for a map nothing could enumerate, and only the
        # first of those is a count of zero.
        counted = len(quality.get("revisions") or ()) if status == "read" else None
        build = quality.get("build") or {}
        rows.append(
            f"| {cell(run_id)} | {cell(counted)} | {cell(status)} "
            f"| {cell(quality.get('revisions_error'))} "
            f"| {cell(quality.get('build_status'))} "
            f"| {cell(quality.get('build_error'))} "
            f"| {cell(build.get('exit_code'))} "
            f"| {cell(_count(build.get('findings')))} "
            f"| {cell(_count(build.get('broken_references')))} |"
        )
    return rows


def _quality_revision_rows(runs: dict[str, dict[str, Any]]) -> list[str]:
    """One row per revision, over every run that enumerated its map.

    The two status columns are what make most of the dashes readable: a
    metric is ``None`` because no frozen manifest fed it, or because the
    draft repository yielded no text at the tag, and the statuses say which.
    The columns a manifest does not feed stay real in the first of those
    cases, so an unmeasured row is still worth reading.

    The statuses do not explain every dash, and the exception is the
    ``cited`` column alone: a frozen manifest that loaded and declares no
    claims leaves ``cited_fraction`` null on a row whose two statuses both
    read ``read``, because :func:`~ai_rfc.draft.lint._citations` will not
    divide by a claim count of zero. That dash is honest rather than a
    failure to measure — a fraction over no claims is not a number — and it
    is the one dash a reader has to read off the ``cited`` column's meaning
    instead of off a status beside it.

    Args:
        runs: The aggregate's ``runs``.

    Returns:
        The header, the separator, and one row per revision.
    """
    header = (
        "| run | tag | rev | kind | manifest | draft | cited | findings | "
        "narration | abstract words | manifest error | draft error |"
    )
    rows = [header, separator(header)]
    for run_id, result in runs.items():
        for revision in (result.get("quality") or {}).get("revisions") or ():
            # Two of these metrics are reached by bracket *through* a nested
            # dict, so this loop depends on how `quality._unmeasured` nulls a
            # row: it recurses into a mapping and returns `{key: None}`, where
            # for a list it returns a bare `None`. A simplification that nulled
            # one level would still pass through `revision_lints`, which only
            # splats the result and sets one key on it, and would turn every
            # unmeasured row into a `TypeError` here. `_unmeasured`'s own
            # docstring gives a different reason for the recursion, so the
            # dependency is written down where it is relied on, and
            # `test_an_unmeasured_row_renders_because_its_nesting_survives`
            # pins it.
            rows.append(
                f"| {cell(run_id)} | {cell(revision['tag'])} "
                f"| {cell(revision['number'])} | {cell(revision['kind'])} "
                f"| {cell(revision['manifest_status'])} "
                f"| {cell(revision['draft_status'])} "
                f"| {cell(revision['citations']['cited_fraction'])} "
                f"| {cell(revision['finding_count'])} "
                f"| {cell(revision['narration_count'])} "
                f"| {cell(revision['abstract']['word_count'])} "
                f"| {cell(revision['manifest_error'])} "
                f"| {cell(revision['draft_error'])} |"
            )
    return rows


def _pass_mark(value: Any) -> str:
    """Three-way: passed, failed, or not yet decided by enough repeats."""
    if value is None:
        return "—"
    return "✓" if value else "✗"


def _cluster_rows(arms: dict[str, dict[str, Any]]) -> list[str]:
    names = list(arms)
    cluster_ids: list[str] = []
    for s in arms.values():
        for cluster_id in s["pass_k"]:
            if cluster_id not in cluster_ids:
                cluster_ids.append(cluster_id)
    header = "| cluster | " + " | ".join(cell(name) for name in names) + " |"
    rows = [header, separator(header)]
    for cluster_id in cluster_ids:
        marks = " | ".join(_pass_mark(arms[a]["pass_k"].get(cluster_id)) for a in names)
        rows.append(f"| {cell(cluster_id)} | {marks} |")
    return rows


def render_report(aggregate: dict[str, Any]) -> str:
    """The human-facing summary of ``aggregate.json``; every number traces to it.

    Args:
        aggregate: The record ``metrics.analyze_campaign`` produced.

    Returns:
        The markdown report.
    """
    git = aggregate.get("git") or {}
    lines = [
        # The heading, not a span: `_code` would change how it renders. A
        # campaign id is never charset-validated, and a line ending inside one
        # ends the heading, so everything after it parses as fresh markdown.
        f"# Campaign {printable(str(aggregate['campaign']))}",
        "",
        f"- target: {_code(aggregate['target'])}, window {aggregate['window']}",
        f"- model: {_code(aggregate['model'])}, effort {_code(aggregate['effort'])}, harness {_code(aggregate['claude_version'])}",
        # An archived aggregate also carries `panther`; it is read straight
        # past rather than printed, because the value it holds described this
        # package's own root under PANTHER's label.
        f"- git: ai_rfc {_code(git.get('ai_rfc'))}",
        f"- parity pre-run: {aggregate.get('parity_pre_run')}",
        f"- run order: {', '.join(_code(r) for r in aggregate['run_order'])}",
        "",
        "## Per arm",
        "",
        *_arm_rows(aggregate["arms"]),
        "",
        "## Per run",
        "",
        *_run_rows(aggregate["runs"]),
        "",
        "## Per cluster (pass^k)",
        "",
        *_cluster_rows(aggregate["arms"]),
        "",
        "## Quality",
        "",
        *_quality_run_rows(aggregate["runs"]),
        "",
        *_quality_revision_rows(aggregate["runs"]),
        "",
        "## Definitions",
        "",
        *[f"- **{key}**: {value}" for key, value in aggregate["definitions"].items()],
    ]
    return "\n".join(lines) + "\n"
