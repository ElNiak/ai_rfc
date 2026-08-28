"""Render a campaign aggregate as markdown, every formula named."""

from __future__ import annotations

from typing import Any


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _arm_rows(arms: dict[str, dict[str, Any]]) -> list[str]:
    header = (
        "| arm | runs | completed (mean / min) | artifacts mean | pass^k mean | integrity | "
        "bypass | errors c1/c2 | hand edits | cost total / mean | failure-cost share | "
        "cost per completed | tokens→first | AUC mean | timeouts | nonzero exits |"
    )
    rows = [header, "|" + "---|" * 16]
    for arm, s in arms.items():
        rows.append(
            f"| {arm} | {s['runs']} | {_fmt(s['completed_fraction_mean'])} / {_fmt(s['completed_fraction_min'])} | "
            f"{_fmt(s['artifacts_fraction_mean'])} | {_fmt(s['pass_k_mean'])} | {_fmt(s['integrity_rate'])} | "
            f"{s['bypass_attempts']} | {s['errors_class1']}/{s['errors_class2']} | {s['hand_edits']} | "
            f"{_fmt(s['cost_total'], 2)} / {_fmt(s['cost_mean'], 2)} | {_fmt(s['failure_cost_share'])} | "
            f"{_fmt(s['cost_per_completed_cluster'], 2)} | {_fmt(s['tokens_to_first_completion_mean'], 0)} | "
            f"{_fmt(s['auc_mean'])} | {s['timed_out_runs']} | {s['nonzero_exit_runs']} |"
        )
    return rows


def _run_rows(runs: dict[str, dict[str, Any]]) -> list[str]:
    rows = [
        "| run | arm | exit | timed out | completed/window | artifacts | gates m/c | cost | turns | tokens | duration ms | integrity | bypass | errors c1/c2 |",
        "|" + "---|" * 14,
    ]
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
            f"| {run_id} | {r['arm']} | {_fmt(r['status']['exit_code'])} | {_fmt(r['status']['timed_out'])} | "
            f"{completed}/{r['window_size']} | {artifacts} | {r['gates']['manifest_exit']}/{r['gates']['citation_exit']} | "
            f"{_fmt(r['cost'].get('total_cost_usd'), 2)} | {_fmt(r['cost'].get('num_turns'))} | {tokens} | "
            f"{_fmt(r['cost'].get('duration_ms'))} | {_fmt(audit.get('integrity'))} | "
            f"{_fmt((audit.get('bypass_attempts') or {}).get('count'))} | "
            f"{_fmt((audit.get('errors') or {}).get('class1'))}/{_fmt((audit.get('errors') or {}).get('class2'))} |"
        )
    return rows


def _cluster_rows(arms: dict[str, dict[str, Any]]) -> list[str]:
    names = list(arms)
    cluster_ids: list[str] = []
    for s in arms.values():
        for cluster_id in s["pass_k"]:
            if cluster_id not in cluster_ids:
                cluster_ids.append(cluster_id)
    rows = ["| cluster | " + " | ".join(names) + " |", "|" + "---|" * (len(names) + 1)]
    for cluster_id in cluster_ids:
        marks = " | ".join(
            "✓" if arms[a]["pass_k"].get(cluster_id) else "✗" for a in names
        )
        rows.append(f"| {cluster_id} | {marks} |")
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
        f"- target: `{aggregate['target']}`, window {aggregate['window']}",
        f"- model: `{aggregate['model']}`, effort `{aggregate['effort']}`, harness `{aggregate['claude_version']}`",
        f"- git: PANTHER `{git.get('panther')}`, ai_rfc `{git.get('ai_rfc')}`",
        f"- parity pre-run: {aggregate.get('parity_pre_run')}",
        f"- run order: {', '.join(aggregate['run_order'])}",
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
