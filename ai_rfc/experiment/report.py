"""Render a campaign aggregate as markdown, every formula named."""

from __future__ import annotations

import re
from typing import Any


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _code(value: Any) -> str:
    """One value as a Markdown code span that its own content cannot break.

    A run of backticks inside the value is fenced by a longer run outside it,
    per CommonMark; a leading or trailing backtick needs the padding space.

    Args:
        value: Any value; ``None`` renders as the em dash, never as a span.

    Returns:
        The fenced span, or ``"—"`` for ``None``.
    """
    if value is None:
        return "—"
    text = str(value).replace("\n", " ")
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _cell(value: Any) -> str:
    """One value as a table cell that cannot add a column or a row.

    Args:
        value: Any value.

    Returns:
        The cell text with pipes escaped and newlines flattened.
    """
    return str(_fmt(value)).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _separator(header: str) -> str:
    """The ``---`` row for a markdown table, sized from its own header.

    Counting the header's columns keeps the two in step; a hand-written width
    silently misrenders the table when a column is added.
    """
    return "|" + "---|" * (header.count("|") - 1)


def _arm_rows(arms: dict[str, dict[str, Any]]) -> list[str]:
    header = (
        "| arm | runs | completed (mean / min) | artifacts mean | pass^k mean | integrity | "
        "bypass | errors c1/c2 | hand edits | cost total / mean | failure-cost share | "
        "cost per completed | tokens→first | AUC mean | timeouts | nonzero exits |"
    )
    rows = [header, _separator(header)]
    for arm, s in arms.items():
        rows.append(
            f"| {_cell(arm)} | {_cell(s['runs'])} | {_cell(s['completed_fraction_mean'])} / {_cell(s['completed_fraction_min'])} | "
            f"{_cell(s['artifacts_fraction_mean'])} | {_cell(s['pass_k_mean'])} | {_cell(s['integrity_rate'])} | "
            f"{_cell(s['bypass_attempts'])} | {_cell(s['errors_class1'])}/{_cell(s['errors_class2'])} | {_cell(s['hand_edits'])} | "
            f"{_cell(_fmt(s['cost_total'], 2))} / {_cell(_fmt(s['cost_mean'], 2))} | {_cell(s['failure_cost_share'])} | "
            f"{_cell(_fmt(s['cost_per_completed_cluster'], 2))} | {_cell(_fmt(s['tokens_to_first_completion_mean'], 0))} | "
            f"{_cell(s['auc_mean'])} | {_cell(s['timed_out_runs'])} | {_cell(s['nonzero_exit_runs'])} |"
        )
    return rows


def _run_rows(runs: dict[str, dict[str, Any]]) -> list[str]:
    header = (
        "| run | arm | exit | timed out | completed/window | artifacts | "
        "gates m/c | cost | turns | tokens | duration ms | integrity | bypass | "
        "errors c1/c2 |"
    )
    rows = [header, _separator(header)]
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
            f"| {_cell(run_id)} | {_cell(r['arm'])} | {_cell(r['status']['exit_code'])} | {_cell(r['status']['timed_out'])} | "
            f"{_cell(completed)}/{_cell(r['window_size'])} | {_cell(artifacts)} | {_cell(r['gates']['manifest_exit'])}/{_cell(r['gates']['citation_exit'])} | "
            f"{_cell(_fmt(r['cost'].get('total_cost_usd'), 2))} | {_cell(r['cost'].get('num_turns'))} | {_cell(tokens)} | "
            f"{_cell(r['cost'].get('duration_ms'))} | {_cell(audit.get('integrity'))} | "
            f"{_cell((audit.get('bypass_attempts') or {}).get('count'))} | "
            f"{_cell((audit.get('errors') or {}).get('class1'))}/{_cell((audit.get('errors') or {}).get('class2'))} |"
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
    header = "| cluster | " + " | ".join(_cell(name) for name in names) + " |"
    rows = [header, "|" + "---|" * (len(names) + 1)]
    for cluster_id in cluster_ids:
        marks = " | ".join(_pass_mark(arms[a]["pass_k"].get(cluster_id)) for a in names)
        rows.append(f"| {_cell(cluster_id)} | {marks} |")
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
        f"# Campaign {aggregate['campaign']}",
        "",
        f"- target: {_code(aggregate['target'])}, window {aggregate['window']}",
        f"- model: {_code(aggregate['model'])}, effort {_code(aggregate['effort'])}, harness {_code(aggregate['claude_version'])}",
        # An archived aggregate also carries `panther`; it is read straight
        # past rather than printed, because the value it holds described this
        # package's own root under PANTHER's label.
        f"- git: ai_rfc {_code(git.get('ai_rfc'))}",
        f"- parity pre-run: {aggregate.get('parity_pre_run')}",
        f"- run order: {_cell(', '.join(aggregate['run_order']))}",
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
