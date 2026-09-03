import json

from ai_rfc.experiment.audit import audit_campaign
from ai_rfc.experiment.driver import launch_pending
from ai_rfc.experiment.metrics import (
    _arm_summary,
    analyze_campaign,
    analyze_run,
    checkpoint_calls,
    surface,
    trajectory,
)
from ai_rfc.experiment.stream import parse_stream

from .conftest import COMPLETE_STEPS


def _run(campaign, write_scenario, scenarios):
    for run_id, payload in scenarios.items():
        write_scenario(campaign.profile_dir, run_id, payload)
    launch_pending(campaign, only=list(scenarios), report=lambda _: None)
    audit_campaign(campaign)


def test_complete_run_scores_full_completion(campaign, write_scenario):
    _run(
        campaign,
        write_scenario,
        {"A1": {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS}},
    )
    result = analyze_run(campaign, "A1")
    assert result["window_size"] == 1
    (cluster,) = result["clusters"]
    assert cluster["checkpoint"] and not cluster["pre_seeded"]
    assert cluster["revision_tag"] == "draft-test-fixture-00" and cluster["tag_exists"]
    assert cluster["artifacts"] and cluster["completed"]
    assert result["gates"]["clean"] and result["gates"]["manifest_exit"] == 0
    assert result["artifacts_fraction"] == 1.0 and result["completed_fraction"] == 1.0
    stats = result["claims"][cluster["cluster_id"]]
    assert stats["claim_count"] == 1 and stats["count_by_supported"] == {"inferred": 1}
    assert stats["unverified_anchors"] == 0 and stats["checked_fraction_by_req_class"]
    assert result["cost"]["total_cost_usd"] == 1.0 and result["cost"]["num_turns"] == 7
    assert result["trajectory"]["tokens_to_first_completion"] > 0
    assert 0.0 < result["trajectory"]["auc"] <= 1.0
    assert result["trajectory"]["points"][0]["cluster_id"] == cluster["cluster_id"]
    assert result["audit"]["integrity"] is True


def test_incomplete_run_scores_zero(campaign, write_scenario):
    steps = [
        {"kind": "claim", "id": "t:3.1", "section": "3.1"},
        {"kind": "checkpoint", "ordinal": 2},
    ]
    _run(campaign, write_scenario, {"B1": {"arm": "B", "cost": 0.3, "steps": steps}})
    result = analyze_run(campaign, "B1")
    (cluster,) = result["clusters"]
    assert cluster["checkpoint"]
    assert cluster["revision_tag"] is None and not cluster["artifacts"]
    assert result["completed_fraction"] == 0.0 and result["gates"]["clean"]
    assert result["trajectory"]["tokens_to_first_completion"] is None
    assert result["trajectory"]["auc"] == 0.0


def test_gate_failure_after_tagging_zeroes_completion(campaign, write_scenario):
    steps = COMPLETE_STEPS + [{"kind": "overstate", "id": "t:3.1"}]
    _run(campaign, write_scenario, {"C1": {"arm": "C", "cost": 0.9, "steps": steps}})
    result = analyze_run(campaign, "C1")
    (cluster,) = result["clusters"]
    assert cluster["artifacts"] and not cluster["completed"]
    assert result["gates"]["manifest_exit"] == 3 and not result["gates"]["clean"]
    assert result["artifacts_fraction"] == 1.0 and result["completed_fraction"] == 0.0
    assert result["audit"]["hand_edits"]["manifest.yaml"] == 3


def test_analyze_campaign_aggregates_per_arm(campaign, write_scenario):
    _run(
        campaign,
        write_scenario,
        {
            "A1": {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
            "B1": {"arm": "B", "cost": 0.4, "exit_code": 3, "steps": []},
            "C1": {"arm": "C", "cost": 1.1, "steps": COMPLETE_STEPS},
        },
    )
    aggregate = analyze_campaign(campaign)
    assert set(aggregate["runs"]) == {"A1", "B1", "C1"}
    arms = aggregate["arms"]
    assert arms["A"]["completed_fraction_mean"] == 1.0
    assert arms["B"]["completed_fraction_mean"] == 0.0
    assert arms["B"]["failure_cost_share"] == 1.0
    assert arms["A"]["failure_cost_share"] == 0.0
    assert arms["A"]["pass_k_mean"] == 1.0
    assert arms["C"]["cost_per_completed_cluster"] == 1.1
    assert arms["A"]["integrity_rate"] == 1.0 and arms["A"]["runs"] == 1
    assert aggregate["definitions"]["completed"]
    stored = json.loads((campaign.analysis_dir / "aggregate.json").read_text())
    assert stored == aggregate


def test_trajectory_points_follow_checkpoint_calls():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"a"}],"usage":{"input_tokens":100,"output_tokens":10}}}\n'
        '{"type":"assistant","message":{"id":"m2","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ai_rfc checkpoint c0002-x"}}],"usage":{"input_tokens":50,"output_tokens":5}}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":false,"content":"{}"}]}}\n'
        '{"type":"assistant","message":{"id":"m3","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":30,"output_tokens":5}}}\n'
    )
    assert checkpoint_calls(events, "B") == [{"index": 1, "cluster_id": "c0002-x"}]
    result = trajectory(events, "B", {"c0002-x"}, window_size=2)
    assert result["total_tokens"] == 200
    assert result["points"] == [
        {
            "index": 1,
            "cluster_id": "c0002-x",
            "cumulative_tokens": 165,
            "completed_so_far": 1,
        }
    ]
    assert result["tokens_to_first_completion"] == 165
    assert abs(result["auc"] - 0.5 * (1 - 165 / 200)) < 1e-9
    assert trajectory(events, "B", set(), window_size=2)["auc"] == 0.0


def _synthetic_run(
    run_id: str, completed: bool, cost: float | None = 1.0, intact: bool = True
) -> dict:
    return {
        "run_id": run_id,
        "arm": run_id[0],
        "surface": {"intact": intact},
        "clusters": [{"cluster_id": "c1", "completed": completed, "artifacts": True}],
        "completed_fraction": 1.0 if completed else 0.0,
        "artifacts_fraction": 1.0,
        "gates": {"clean": completed},
        "cost": {"total_cost_usd": cost},
        "trajectory": {
            "auc": 0.5,
            "tokens_to_first_completion": 10 if completed else None,
        },
        "status": {"timed_out": False, "exit_code": 0},
        "audit": {
            "integrity": True,
            "bypass_attempts": {"count": 0},
            "errors": {"class1": 0, "class2": 0},
            "hand_edits": {"manifest.yaml": 0},
        },
    }


def test_pass_k_needs_every_repeat_and_is_undecided_until_then():
    """k=2 is a headline number; an unfinished arm must not read as a failure."""
    both = [_synthetic_run("A1", True), _synthetic_run("A2", True)]
    assert _arm_summary(both, ["c1"], repeats=2)["pass_k"] == {"c1": True}
    assert _arm_summary(both, ["c1"], repeats=2)["pass_k_mean"] == 1.0

    one_failed = [_synthetic_run("B1", True), _synthetic_run("B2", False)]
    assert _arm_summary(one_failed, ["c1"], repeats=2)["pass_k"] == {"c1": False}
    assert _arm_summary(one_failed, ["c1"], repeats=2)["pass_k_mean"] == 0.0

    half = _arm_summary([_synthetic_run("C1", True)], ["c1"], repeats=2)
    assert half["pass_k"] == {"c1": None}
    assert half["pass_k_mean"] is None


def test_a_run_with_no_result_event_has_unknown_cost_not_zero_cost():
    """An interrupted run must not be priced at $0.00 and averaged in.

    ``runner`` writes ``result.json`` as ``null`` when no terminal result event
    was captured, so ``total_cost_usd`` is None. Treating that as 0.0 pulls
    ``cost_mean`` down and understates ``failure_cost_share`` twice over, since
    a run producing nothing completed is exactly the kind that ends without a
    result event -- it lands in the numerator at zero and inflates nothing in
    the denominator.
    """
    priced = _synthetic_run("A1", True, cost=10.0)
    unpriced = _synthetic_run("A2", False, cost=None)
    summary = _arm_summary([priced, unpriced], ["c1"], repeats=2)

    assert summary["cost_total"] == 10.0
    assert summary["cost_mean"] == 10.0, "the unpriced run must not drag the mean"
    assert summary["runs_with_unknown_cost"] == 1
    # The failed run is unpriced, so no spend is attributable to failure yet.
    assert summary["failure_cost_share"] == 0.0
    # One completed cluster, priced; the unpriced run contributes neither side.
    assert summary["cost_per_completed_cluster"] == 10.0

    # A priced failure still counts, so the metric is not simply always zero.
    both_priced = [priced, _synthetic_run("A2", False, cost=6.0)]
    assert _arm_summary(both_priced, ["c1"], repeats=2)["failure_cost_share"] == 0.375
    assert _arm_summary(both_priced, ["c1"], repeats=2)["runs_with_unknown_cost"] == 0


def _events_with(server_status: str | None, ai_rfc_calls: int) -> list[dict]:
    """A transcript announcing a server and making that many ai_rfc calls."""
    servers = (
        [] if server_status is None else [{"name": "ai_rfc", "status": server_status}]
    )
    events: list[dict] = [{"type": "system", "subtype": "init", "mcp_servers": servers}]
    for n in range(ai_rfc_calls):
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"t{n}",
                            "name": "mcp__ai_rfc__ai_rfc_checkpoint",
                            "input": {},
                        }
                    ]
                },
            }
        )
    return events


def test_an_arm_that_never_got_its_tools_is_not_intact():
    """The run measured a different arm, so its numbers describe nothing.

    Every write the substrate validates goes through these tools, so a session
    without them is not a weaker arm A — it is an unvalidated arm that is not
    among the three under study.
    """
    result = surface(_events_with("failed", ai_rfc_calls=0), "A")
    assert result["intact"] is False
    assert result["mcp_servers"] == {"ai_rfc": "failed"}
    assert result["ai_rfc_tool_calls"] == 0


def test_a_run_that_mounted_but_died_before_calling_is_still_intact():
    """Mounting is the test; a short run is not a void one.

    A run killed on its cap before its first tool call had the surface its arm
    declares. Voiding it would drop a sound run from the arm's means for the
    offence of being short.
    """
    assert surface(_events_with("connected", ai_rfc_calls=0), "A")["intact"] is True


def test_a_call_in_the_right_prefix_is_not_evidence_of_a_surface():
    """The failed run invented `mcp__ai_rfc__cluster_next`, which no server has.

    Real tools are `mcp__ai_rfc__ai_rfc_*`. Counting a name by its prefix would
    have let a hallucinated call attest to a server that never started, so the
    count is reported beside the verdict and never folded into it.
    """
    result = surface(_events_with("failed", ai_rfc_calls=2), "A")
    assert result["ai_rfc_tool_calls"] == 2
    assert result["intact"] is False


def test_a_cli_arm_is_intact_without_any_server():
    """B and C reach the substrate through the CLI, so no server is correct."""
    for arm in ("B", "C"):
        assert surface(_events_with(None, ai_rfc_calls=0), arm)["intact"] is True


def test_a_broken_surface_is_excluded_from_every_figure_not_just_counted():
    """Counting a void run while still averaging it in is the worse of both.

    The run never had the tools under study, so it measured a different arm.
    Every figure here must read as though it had not run — most of all
    ``pass_k``, where folding it in turns an undecided cluster into a hard
    False, which renders identically to a real failure.
    """
    sound = _synthetic_run("A1", True, cost=2.0)
    void = _synthetic_run("A2", False, cost=6.0, intact=False)

    summary = _arm_summary([sound, void], ["c1"], repeats=2)
    alone = _arm_summary([sound], ["c1"], repeats=2)

    assert summary["runs_with_broken_surface"] == 1
    assert summary["runs"] == 1, "the void run is not one of this arm's runs"
    assert summary["completed_fraction_mean"] == alone["completed_fraction_mean"] == 1.0
    assert summary["cost_mean"] == alone["cost_mean"] == 2.0
    assert summary["cost_total"] == 2.0, "void spend is not this arm's spend"
    assert summary["failure_cost_share"] == 0.0
    # One sound run of two repeats leaves the arm undecided, not failed.
    assert summary["pass_k"] == {"c1": None}
    assert summary["pass_k_mean"] is None


def test_an_arm_whose_runs_were_all_void_reports_no_figures():
    """Nothing measured this arm, so it must not read as a zero-scoring one."""
    void = _synthetic_run("A1", True, cost=3.0, intact=False)

    summary = _arm_summary([void], ["c1"], repeats=1)

    assert summary["runs"] == 0 and summary["runs_with_broken_surface"] == 1
    assert summary["completed_fraction_mean"] is None
    assert summary["cost_total"] == 0 and summary["cost_mean"] is None
