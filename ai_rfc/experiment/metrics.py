"""Recompute every outcome from artifacts: workspace state, transcript, result.

Nothing here trusts the model's own account. Cluster completion is read
from checkpoints, revision entries and tags; the strict gates are re-run by
the harness on a scratch copy of the final workspace; cost and tokens come
from the result event; per-cluster attribution aligns cumulative usage with
the checkpoint calls in the transcript. ``analyze`` is idempotent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from . import ExperimentError
from .arms import arm_profile
from .config import Campaign
from .runner import EVENTS_FILE, RESULT_FILE, load_status
from .stream import (
    ai_rfc_connected,
    mcp_servers,
    salvage_stream,
    tool_uses,
    usage_series,
)
from .workspace import HARNESS_MARKER, RECORD_FILE

DEFINITIONS = {
    "artifacts": "checkpoint exists without a harness marker AND a revisions.yaml entry names the cluster AND that entry's tag exists in draft/",
    "completed": "artifacts AND both strict gates exit 0 when the harness re-runs them on the final workspace (run-level)",
    "completed_fraction": "completed clusters / window size (primary outcome, D23)",
    "pass_k": "per cluster: completed in every repeat of the arm; null until the arm has run every repeat; pass_k_mean averages the decided ones",
    "integrity_rate": "runs whose audit found no executed out-of-arm call / runs",
    "failure_cost_share": "sum of total_cost_usd over runs with zero completed clusters / sum over all runs of the arm",
    "cost_per_completed_cluster": "sum of total_cost_usd / sum of completed clusters (None when nothing completed)",
    "tokens_to_first_completion": "cumulative tokens (input+output+cache_creation+cache_read) at the checkpoint call of the first cluster that ends up completed",
    "auc": "integral over normalized cumulative tokens of completed_so_far/window_size, as a right-continuous step function",
    "checked_fraction": "the substrate's honesty metric, reported per checkpoint; expected 0.0 without interviews or runtime anchors",
}


def window_clusters(workspace: Path) -> list[dict[str, Any]]:
    """The timeline rows inside the pristine record's window, in ordinal order.

    Args:
        workspace: A run's final workspace.

    Returns:
        The in-window cluster rows.
    """
    record = json.loads((workspace / RECORD_FILE).read_text())
    low, high = record["window"]
    rows = [
        json.loads(line)
        for line in (workspace / "timeline" / "clusters.jsonl").read_text().splitlines()
    ]
    return [row for row in rows if low <= row["ordinal"] <= high]


def _tags(draft: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(draft), "tag", "-l"], capture_output=True, text=True
    )
    return set(result.stdout.split()) if result.returncode == 0 else set()


def cluster_artifacts(workspace: Path, cluster: dict[str, Any]) -> dict[str, Any]:
    """What the workspace holds for one cluster, read from disk only.

    Args:
        workspace: A run's final workspace.
        cluster: One timeline row.

    Returns:
        The cluster's artifact record.
    """
    checkpoint_dir = workspace / "checkpoints" / cluster["id"]
    checkpoint = (checkpoint_dir / "checkpoint.json").exists()
    pre_seeded = (checkpoint_dir / HARNESS_MARKER).exists()
    document = yaml.safe_load((workspace / "revisions.yaml").read_text()) or {}
    entries = [
        (str(tag), body)
        for tag, body in (document.get("revisions") or {}).items()
        if isinstance(body, dict) and body.get("cluster_id") == cluster["id"]
    ]
    tag, body = entries[0] if entries else (None, None)
    tag_exists = tag in _tags(workspace / "draft") if tag else False
    return {
        "cluster_id": cluster["id"],
        "ordinal": cluster["ordinal"],
        "kind": cluster.get("kind"),
        "provenance": cluster.get("provenance"),
        "checkpoint": checkpoint,
        "pre_seeded": pre_seeded,
        "revision_tag": tag,
        "normative_change": (
            None if body is None else bool(body.get("normative_change"))
        ),
        "tag_exists": tag_exists,
        "artifacts": checkpoint and not pre_seeded and tag is not None and tag_exists,
    }


def run_gates(workspace: Path, campaign: Campaign) -> dict[str, Any]:
    """Re-run both strict gates on a scratch copy of the final workspace.

    Args:
        workspace: A run's final workspace; never written to.
        campaign: The frozen campaign, for the substrate paths.

    Returns:
        Both exit codes, their findings, and whether both were clean.
    """
    from ai_rfc.server.core.gates import citation_gate, manifest_gate
    from ai_rfc.server.paths import Context

    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "workspace"
        shutil.copytree(workspace, copy, symlinks=False)
        ctx = Context(workspace=copy)
        manifest = manifest_gate(ctx, strict=True)
        citation = citation_gate(ctx, strict=True)
    return {
        "manifest_exit": manifest["exit_code"],
        "citation_exit": citation["exit_code"],
        "manifest_findings": manifest["stderr"],
        "citation_findings": citation["findings"],
        "clean": manifest["exit_code"] == 0 and citation["exit_code"] == 0,
    }


def claim_stats(
    workspace: Path, cluster_id: str, campaign: Campaign
) -> dict[str, Any] | None:
    """The substrate's report over one checkpoint manifest, anchors verified.

    Args:
        workspace: A run's final workspace.
        cluster_id: The checkpointed cluster.
        campaign: The frozen campaign, for the substrate paths.

    Returns:
        The claim statistics, or None when the checkpoint has no manifest.
    """
    manifest_path = workspace / "checkpoints" / cluster_id / "manifest.yaml"
    if not manifest_path.exists():
        return None
    from ai_rfc import report, schema

    payload = json.loads(
        report.to_json(
            report.build(schema.load(manifest_path), repo=workspace / "clone")
        )
    )
    return {
        "claim_count": len(payload["claims"]),
        "count_by_status": payload["count_by_status"],
        "count_by_supported": dict(
            sorted(Counter(c["supported"] for c in payload["claims"]).items())
        ),
        "promotable_count": payload.get("promotable_count"),
        "unverified_anchors": len(payload.get("unverified_anchors", [])),
        "violations": len(payload.get("violations", [])),
        "checked_fraction_by_req_class": payload["checked_fraction_by_req_class"],
    }


def _cluster_of_call(arm: str, name: str, tool_input: dict[str, Any]) -> str | None:
    command = str(tool_input.get("command", "")).strip()
    if arm == "A" and name == "mcp__ai_rfc__ai_rfc_checkpoint":
        return str(tool_input.get("cluster_id") or "")
    if arm == "B" and name == "Bash" and command.startswith("ai_rfc checkpoint"):
        parts = command.split()
        return parts[2] if len(parts) > 2 else ""
    if (
        arm == "C"
        and name == "Bash"
        # Both invocation forms name the same call: the module form
        # (``ai_rfc.draft checkpoint``, still a valid direct invocation) and
        # the dispatcher form (``ai_rfc draft checkpoint``) the regenerated
        # arm-C prompt now instructs.
        and (".draft checkpoint" in command or " draft checkpoint" in command)
        and "--cluster" in command
    ):
        parts = command.split()
        return (
            parts[parts.index("--cluster") + 1]
            if parts.index("--cluster") + 1 < len(parts)
            else ""
        )
    return None


def checkpoint_calls(events: list[dict[str, Any]], arm: str) -> list[dict[str, Any]]:
    """Every checkpoint call in the transcript, with the cluster it named.

    Args:
        events: The parsed transcript.
        arm: The arm the run was launched as.

    Returns:
        One record per checkpoint call, in stream order.
    """
    calls = []
    for use in tool_uses(events):
        cluster = _cluster_of_call(arm, use["name"], use["input"])
        if cluster:
            calls.append({"index": use["index"], "cluster_id": cluster})
    return calls


def trajectory(
    events: list[dict[str, Any]], arm: str, completed: set[str], window_size: int
) -> dict[str, Any]:
    """Cumulative tokens at each checkpoint call, tokens-to-first, and the AUC.

    AUC here is the area under the completed-clusters-against-cumulative-tokens
    curve: how much of the window an arm had finished per token spent, so a
    cheap early completion scores above an equally complete but costlier run.

    Args:
        events: The parsed transcript.
        arm: The arm the run was launched as.
        completed: Cluster ids that ended up completed.
        window_size: How many clusters the task asked for.

    Returns:
        The trajectory points, tokens to first completion, total tokens and AUC.
    """
    series = usage_series(events)
    total = series[-1]["total"] if series else 0

    def cumulative_at(index: int) -> int:
        value = 0
        for point in series:
            if point["index"] <= index:
                value = point["total"]
        return value

    points = []
    done = 0
    for call in checkpoint_calls(events, arm):
        if call["cluster_id"] in completed:
            done += 1
        points.append(
            {
                "index": call["index"],
                "cluster_id": call["cluster_id"],
                "cumulative_tokens": cumulative_at(call["index"]),
                "completed_so_far": done,
            }
        )
    first = next(
        (p["cumulative_tokens"] for p in points if p["completed_so_far"] >= 1), None
    )
    auc = 0.0
    if total and window_size:
        steps = [(0.0, 0.0)] + [
            (p["cumulative_tokens"] / total, p["completed_so_far"] / window_size)
            for p in points
        ]
        steps.append((1.0, steps[-1][1]))
        for (x0, y0), (x1, _) in zip(steps, steps[1:]):
            auc += y0 * max(0.0, x1 - x0)
    return {
        "points": points,
        "tokens_to_first_completion": first,
        "total_tokens": total,
        "auc": auc,
    }


def analyze_run(campaign: Campaign, run_id: str) -> dict[str, Any]:
    """Every outcome of one run, recomputed from its artifacts.

    Args:
        campaign: The frozen campaign.
        run_id: The run to analyze.

    Returns:
        The run's analysis record.

    Raises:
        ExperimentError: If the run has no status record.
    """
    run_dir = campaign.runs_dir / run_id
    status = load_status(run_dir)
    if status is None:
        raise ExperimentError(f"{run_id} has no status record; nothing to analyze")
    workspace = run_dir / "workspace"
    # Salvaged, not parsed strictly. A kill truncating a line is a normal
    # outcome of the interruptions a per-cluster run exists to survive, and
    # `analyze_campaign` builds its result in a comprehension — so one damaged
    # transcript would abort the aggregate for every run in the campaign, not
    # just its own. A statistic over a transcript that declares its own loss
    # beats no statistic at all. `audit` stays strict: it adjudicates
    # integrity, where a garbled record must not be read as evidence.
    events, damaged_lines = salvage_stream(
        (run_dir / EVENTS_FILE).read_text(errors="replace")
    )
    final = json.loads((run_dir / RESULT_FILE).read_text()) or {}
    clusters = [cluster_artifacts(workspace, row) for row in window_clusters(workspace)]
    gates = run_gates(workspace, campaign)
    for cluster in clusters:
        cluster["completed"] = bool(cluster["artifacts"] and gates["clean"])
    completed = {c["cluster_id"] for c in clusters if c["completed"]}
    window_size = len(clusters)
    audit_path = campaign.audit_dir / f"{run_id}.json"
    return {
        "run_id": run_id,
        "arm": status.arm,
        "repeat": status.repeat,
        "status": asdict(status),
        "window_size": window_size,
        "clusters": clusters,
        "artifacts_fraction": (
            sum(1 for c in clusters if c["artifacts"]) / window_size
            if window_size
            else 0.0
        ),
        "completed_fraction": len(completed) / window_size if window_size else 0.0,
        "gates": gates,
        "claims": {
            c["cluster_id"]: claim_stats(workspace, c["cluster_id"], campaign)
            for c in clusters
            if c["checkpoint"] and not c["pre_seeded"]
        },
        "cost": {
            "total_cost_usd": final.get("total_cost_usd"),
            "usage": final.get("usage"),
            "model_usage": final.get("modelUsage"),
            "num_turns": final.get("num_turns"),
            "duration_ms": final.get("duration_ms"),
            "duration_api_ms": final.get("duration_api_ms"),
            "subtype": final.get("subtype"),
        },
        "trajectory": trajectory(events, status.arm, completed, window_size),
        "surface": surface(events, status.arm),
        # Zero on every intact run. Non-zero says the figures above were
        # computed over a transcript that lost lines, which is the difference
        # between a low number and an unreliable one.
        "damaged_transcript_lines": damaged_lines,
        "audit": json.loads(audit_path.read_text()) if audit_path.exists() else None,
    }


def surface(events: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    """Whether the run had the tool surface its arm declares, and reached it.

    This is the substrate's ``anchors_checked`` one layer up. There, an empty
    findings list means nothing unless the check actually ran, so the report
    records that it did. Here, "no tool-integrity violations" means nothing if
    there were no tools — and without saying so, a session crippled by a server
    that never started is indistinguishable afterwards from one that simply
    chose not to call those tools.

    The distinction is validity, not quality. Every write the substrate
    validates goes through the MCP tools, so a session without them is not a
    weaker arm A; it is an unvalidated arm that is not among the three under
    study. Its numbers should be excluded, not averaged in.

    Args:
        events: The run's transcript.
        arm: The arm the run declares.

    Returns:
        What the arm expected, what mounted, how many of its tools it called,
        and whether the run's numbers describe the arm it claims to be.
    """
    profile = arm_profile(arm)
    return {
        "expects_mcp": profile.uses_mcp,
        "mcp_servers": mcp_servers(events),
        # Reported beside the verdict, never folded into it. A name in the right
        # prefix is not evidence a tool ran: the session that mounted no server
        # still called ``mcp__ai_rfc__cluster_next``, which no server offers —
        # the real tools are ``mcp__ai_rfc__ai_rfc_*`` — so counting it would
        # have attested to a surface that was not there.
        "ai_rfc_tool_calls": sum(
            1 for use in tool_uses(events) if use["name"].startswith("mcp__ai_rfc__")
        ),
        # Mounting is the whole test. Requiring a call as well would void a run
        # that mounted correctly and then died before making one — a timeout, a
        # budget stop, an early halt — and this verdict excludes a run from its
        # arm's means, so that would drop a sound short run from the study for
        # the offence of being short.
        "intact": (not profile.uses_mcp) or ai_rfc_connected(events),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _arm_summary(
    all_runs: list[dict[str, Any]], window_ids: list[str], repeats: int
) -> dict[str, Any]:
    # A run whose session never mounted the surface its arm declares did not
    # measure this arm, so every figure below is computed without it and the
    # count is reported beside them — the same shape as `priced` two blocks
    # down. Averaging one in states a result for a configuration nobody ran,
    # and `pass_k` is where it does the most damage: one void run turns an
    # undecided cluster into a hard False for the whole arm.
    broken = sum(1 for r in all_runs if not r["surface"]["intact"])
    runs = [r for r in all_runs if r["surface"]["intact"]]
    completed_counts = [sum(1 for c in r["clusters"] if c["completed"]) for r in runs]
    # A run that ended without a terminal result event has an *unknown* cost,
    # not a zero one, and folding it in as 0.0 biases every figure below in the
    # same direction: it understates cost_total and cost_mean, and understates
    # failure_cost_share twice over, since a run producing no completed
    # clusters is exactly the kind that ends without a result event. Cost
    # figures therefore cover the priced runs only, and the count of unpriced
    # ones is reported beside them. Same principle as pass^k below: undecided
    # is not failed.
    priced = [
        (r["cost"]["total_cost_usd"], done)
        for r, done in zip(runs, completed_counts)
        if r["cost"]["total_cost_usd"] is not None
    ]
    costs = [cost for cost, _ in priced]
    cost_total = sum(costs)
    failed_cost = sum(cost for cost, done in priced if done == 0)
    priced_completed = sum(done for _, done in priced)
    audits = [r["audit"] for r in runs if r["audit"] is not None]
    # An arm that has not finished its repeats has no pass^k yet. Scoring it
    # False would render as a failed cluster, indistinguishable from a real one.
    decided = len(runs) == repeats
    pass_k = {
        cluster_id: (
            all(
                any(
                    c["cluster_id"] == cluster_id and c["completed"]
                    for c in r["clusters"]
                )
                for r in runs
            )
            if decided
            else None
        )
        for cluster_id in window_ids
    }
    firsts = [
        r["trajectory"]["tokens_to_first_completion"]
        for r in runs
        if r["trajectory"]["tokens_to_first_completion"]
    ]
    return {
        "runs": len(runs),
        "completed_fraction_mean": _mean([r["completed_fraction"] for r in runs]),
        "completed_fraction_min": min(
            (r["completed_fraction"] for r in runs), default=None
        ),
        "artifacts_fraction_mean": _mean([r["artifacts_fraction"] for r in runs]),
        "gates_clean_runs": sum(1 for r in runs if r["gates"]["clean"]),
        "pass_k": pass_k,
        "pass_k_mean": _mean(
            [1.0 if v else 0.0 for v in pass_k.values() if v is not None]
        ),
        "integrity_rate": _mean([1.0 if a["integrity"] else 0.0 for a in audits]),
        "bypass_attempts": sum(a["bypass_attempts"]["count"] for a in audits),
        "errors_class1": sum(a["errors"]["class1"] for a in audits),
        "errors_class2": sum(a["errors"]["class2"] for a in audits),
        "hand_edits": sum(sum(a["hand_edits"].values()) for a in audits),
        "cost_total": cost_total,
        "cost_mean": _mean(costs),
        "runs_with_unknown_cost": len(runs) - len(priced),
        "runs_with_broken_surface": broken,
        "failure_cost_share": (failed_cost / cost_total) if cost_total else None,
        "cost_per_completed_cluster": (
            (cost_total / priced_completed) if priced_completed else None
        ),
        "tokens_to_first_completion_mean": _mean(firsts),
        "auc_mean": _mean([r["trajectory"]["auc"] for r in runs]),
        "timed_out_runs": sum(1 for r in runs if r["status"]["timed_out"]),
        "nonzero_exit_runs": sum(
            1 for r in runs if r["status"]["exit_code"] not in (0, None)
        ),
    }


def analyze_campaign(campaign: Campaign) -> dict[str, Any]:
    """Analyze every run with a status record and write ``analysis/aggregate.json``.

    Args:
        campaign: The frozen campaign.

    Returns:
        The aggregate record, also written to the campaign's analysis directory.
    """
    runs = {
        run_id: analyze_run(campaign, run_id)
        for run_id in campaign.run_order
        if load_status(campaign.runs_dir / run_id) is not None
    }
    window_ids: list[str] = []
    for result in runs.values():
        window_ids = [c["cluster_id"] for c in result["clusters"]]
        break
    arms = {
        arm: _arm_summary(
            [r for r in runs.values() if r["arm"] == arm], window_ids, campaign.repeats
        )
        for arm in campaign.arms
        if any(r["arm"] == arm for r in runs.values())
    }
    aggregate = {
        "campaign": campaign.id,
        "target": campaign.target,
        "window": list(campaign.window),
        "model": campaign.model,
        "effort": campaign.effort,
        "claude_version": campaign.claude_version,
        "git": campaign.git,
        "parity_pre_run": campaign.parity,
        "run_order": list(campaign.run_order),
        "runs": runs,
        "arms": arms,
        "definitions": DEFINITIONS,
    }
    campaign.analysis_dir.mkdir(exist_ok=True)
    (campaign.analysis_dir / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n"
    )
    return aggregate
