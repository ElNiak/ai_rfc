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
        "## Definitions",
        "",
        *[f"- **{key}**: {value}" for key, value in aggregate["definitions"].items()],
    ]
    return "\n".join(lines) + "\n"
