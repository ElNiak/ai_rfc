import json

from experiment.audit import audit_campaign
from experiment.matrix import execute
from experiment.metrics import (
    _arm_summary,
    analyze_campaign,
    analyze_run,
    checkpoint_calls,
    trajectory,
)
from experiment.stream import parse_stream

from .conftest import COMPLETE_STEPS


def _run(campaign, write_scenario, scenarios):
    for run_id, payload in scenarios.items():
        write_scenario(campaign.profile_dir, run_id, payload)
    execute(campaign, only=list(scenarios), report=lambda _: None)
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
    assert result["gates"]["manifest_exit"] == 2 and not result["gates"]["clean"]
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
        '{"type":"assistant","message":{"id":"m2","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"arfc checkpoint c0002-x"}}],"usage":{"input_tokens":50,"output_tokens":5}}}\n'
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


def _synthetic_run(run_id: str, completed: bool, cost: float = 1.0) -> dict:
    return {
        "run_id": run_id,
        "arm": run_id[0],
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
