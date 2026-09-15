import json

from ai_rfc.driver.arms import arm_profile
from ai_rfc.driver.enforcement import bash_prefixes
from ai_rfc.driver.stream import parse_stream
from ai_rfc.experiment import metrics
from ai_rfc.experiment.audit import audit_campaign
from ai_rfc.experiment.campaign_runs import launch_pending
from ai_rfc.experiment.metrics import (
    _arm_summary,
    analyze_campaign,
    analyze_run,
    checkpoint_calls,
    surface,
    trajectory,
)
from ai_rfc.server.testing import git as _vcs

from .conftest import COMPLETE_STEPS, append_untagged_revision


def _one_bash_call(command: str) -> list[dict]:
    """A one-event transcript whose only tool call runs ``command``."""
    event = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": command},
                }
            ],
        },
    }
    return parse_stream(f"{json.dumps(event)}\n")


def test_the_checkpoint_matcher_reads_the_guard_prefix():
    """The trajectory's matcher is the third reader of one declaration.

    A separate test from the audit's, because the two fail differently: this
    one drops every point from the trajectory while the audit's reclassifies
    a legitimate call as an integrity violation.
    """
    (prefix,) = bash_prefixes(arm_profile("B"))
    assert checkpoint_calls(_one_bash_call(f"{prefix}checkpoint c0002-x"), "B") == [
        {"index": 0, "cluster_id": "c0002-x"}
    ]
    assert checkpoint_calls(_one_bash_call("ai_rfc checkpoint c0002-x"), "B") == []


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


def test_the_run_record_carries_a_lint_row_per_revision(campaign, write_scenario):
    _run(campaign, write_scenario, {"A1": {"steps": COMPLETE_STEPS}})
    result = analyze_run(campaign, "A1")
    assert [r["tag"] for r in result["quality"]["revisions"]] == [
        "draft-test-fixture-00"
    ]
    # Default is build-off, so no clone and no make in the test suite.
    assert result["quality"]["build"] is None


def _recording_build(calls):
    """Stand in for ``final_build``, recording the arguments it was handed."""

    def recording(workspace, toolchain_path, out):
        calls.append({"workspace": workspace, "toolchain": toolchain_path, "out": out})
        return {"exit_code": 0}

    return recording


def test_a_requested_build_is_routed_outside_the_run_directory(
    campaign, write_scenario, monkeypatch
):
    """Where the build would land, and that it is skipped unless asked for.

    The build is recorded rather than run: the campaign fixture freezes a real
    toolchain, so an unrecorded call would clone the draft repository and run
    make. Recording is also the only way to read the output root, which is the
    one thing the mtime guard below cannot observe — it watches ``workspace/``
    and the path this pins against is ``runs/<id>/draft-build``, its sibling.
    """
    _run(campaign, write_scenario, {"A1": {"steps": COMPLETE_STEPS}})
    calls = []
    monkeypatch.setattr(metrics, "final_build", _recording_build(calls))

    assert analyze_run(campaign, "A1")["quality"]["build"] is None
    assert calls == []

    result = analyze_run(campaign, "A1", build=True)
    assert result["quality"]["build"] == {"exit_code": 0}
    (call,) = calls
    assert call["workspace"] == campaign.runs_dir / "A1" / "workspace"
    # The `str` path the campaign froze, passed through as it is stored.
    assert call["toolchain"] == campaign.toolchain
    assert call["out"] == campaign.analysis_dir / "A1" / "draft-build"
    assert campaign.runs_dir not in call["out"].parents


def test_analyze_campaign_passes_the_build_request_through(
    campaign, write_scenario, monkeypatch
):
    """``analyze_campaign`` reaches the build only through this keyword.

    What this pins is the keyword, at the level of the function. Without it a
    mutant dropping ``build=build`` from the comprehension is invisible here;
    and the default is asserted too, since a mutant flipping it to True would
    otherwise just run builds across the suite without failing anything.

    Not a claim about the CLI. The ``analyze`` verb's ``--build`` flag is what
    reaches this keyword in practice, and a flag that parses without being
    routed would leave both assertions below green — ``test_cli_campaign.py``
    owns that half.
    """
    _run(campaign, write_scenario, {"A1": {"steps": COMPLETE_STEPS}})
    calls = []
    monkeypatch.setattr(metrics, "final_build", _recording_build(calls))

    assert analyze_campaign(campaign)["runs"]["A1"]["quality"]["build"] is None
    assert calls == []

    aggregate = analyze_campaign(campaign, build=True)
    assert [call["out"] for call in calls] == [
        campaign.analysis_dir / "A1" / "draft-build"
    ]
    assert aggregate["runs"]["A1"]["quality"]["build"] == {"exit_code": 0}


def test_analyze_does_not_write_inside_the_run_workspace(campaign, write_scenario):
    """R5 guard: passes today, and must keep passing. Not a RED.

    What it discriminates after this task is the nested draft repository:
    ``rglob`` reaches ``workspace/draft/.git``, and reading a revision now runs
    ``git ls-tree`` and ``git show`` in there. Those are plumbing reads, but a
    lock file or a rewritten index would be caught here.
    """
    _run(campaign, write_scenario, {"A1": {"steps": COMPLETE_STEPS}})
    ws = campaign.runs_dir / "A1" / "workspace"
    before = {p: p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()}
    analyze_run(campaign, "A1")
    assert {p: p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()} == before


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


def test_one_untagged_revision_does_not_abort_the_whole_aggregate(
    campaign, write_scenario
):
    """R23 at the level the comprehension makes it matter.

    ``runs`` is built in a dict comprehension, so a revision the draft
    repository never got would take down the aggregate for every other run in
    the campaign and not just its own. A test that exercised ``analyze_run``
    alone could not see that: the damaged run would report its row and the
    aggregate would still be the thing that never got built.
    """
    _run(
        campaign,
        write_scenario,
        {
            "A1": {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
            "C1": {"arm": "C", "cost": 1.1, "steps": COMPLETE_STEPS},
        },
    )
    append_untagged_revision(
        campaign.runs_dir / "A1" / "workspace",
        "draft-test-fixture-09",
        "c0009-never-ran",
    )
    aggregate = analyze_campaign(campaign)

    assert set(aggregate["runs"]) == {"A1", "C1"}
    damaged = aggregate["runs"]["A1"]["quality"]["revisions"]
    intact = aggregate["runs"]["C1"]["quality"]["revisions"]
    assert [r["draft_status"] for r in damaged] == ["read", "unreadable"]
    assert [r["draft_status"] for r in intact] == ["read"]
    assert damaged[1]["citations"]["tokens"] is None
    assert intact[0]["citations"]["tokens"] is not None
    # D-33 again: the nulled row must survive the round trip like any other.
    stored = json.loads((campaign.analysis_dir / "aggregate.json").read_text())
    assert stored == aggregate


def test_one_unreadable_revision_map_does_not_abort_the_whole_aggregate(
    campaign, write_scenario
):
    """R27 through the comprehension, which ``analyze_run`` alone cannot reach.

    ``revisions.yaml`` is in ``audit.STATE_FILES`` because arms hand-edit it,
    so a map in a shape ``load_revisions`` refuses is agent-reachable. Before
    R27 one such run took the aggregate down for every other run in the
    campaign.
    """
    _run(
        campaign,
        write_scenario,
        {
            "A1": {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
            "C1": {"arm": "C", "cost": 1.1, "steps": COMPLETE_STEPS},
        },
    )
    # The malformation is chosen, not arbitrary: a tag that carries no
    # two-digit revision suffix is one `load_revisions` refuses and
    # `ledger._entries` tolerates, so what fails here is R27's path alone. The
    # form to avoid is a NON-NULL scalar such as `revisions: hello`, which dies
    # in `ledger._entries` with an `AttributeError` before `quality` is ever
    # built — a separate, older defect R27 does not reach. A bare `revisions:`
    # is not that form: it parses to None, `_entries` does
    # `(document.get("revisions") or {})` and tolerates it, so it is one more
    # R27 case.
    revisions = campaign.runs_dir / "A1" / "workspace" / "revisions.yaml"
    revisions.write_text(
        "revisions:\n"
        "  nope:\n"
        "    cluster_id: c0001-x\n"
        "    checkpoint_manifest_sha256: x\n"
        "    normative_change: true\n"
    )
    aggregate = analyze_campaign(campaign)

    assert set(aggregate["runs"]) == {"A1", "C1"}
    damaged = aggregate["runs"]["A1"]["quality"]
    intact = aggregate["runs"]["C1"]["quality"]
    assert damaged["revisions"] == []
    assert damaged["revisions_status"] == "unreadable"
    assert "nope" in damaged["revisions_error"]
    # The sibling's map still enumerates. A campaign whose every map were
    # damaged would prove nothing about either side.
    assert [r["tag"] for r in intact["revisions"]] == ["draft-test-fixture-00"]
    assert intact["revisions_status"] == "read"
    assert intact["revisions_error"] is None
    stored = json.loads((campaign.analysis_dir / "aggregate.json").read_text())
    assert stored == aggregate


def test_a_deleted_revision_map_does_not_abort_the_whole_aggregate(
    campaign, write_scenario
):
    """R29 through the comprehension, where the regression actually bit.

    Deleting the file is the state an arm with shell access can leave, and
    before R29 it raised ``OSError`` out of the instrument arm and took every
    other run's analysis with it. ``analyze_run`` alone cannot show that.
    """
    _run(
        campaign,
        write_scenario,
        {
            "A1": {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
            "C1": {"arm": "C", "cost": 1.1, "steps": COMPLETE_STEPS},
        },
    )
    (campaign.runs_dir / "A1" / "workspace" / "revisions.yaml").unlink()
    aggregate = analyze_campaign(campaign)

    assert set(aggregate["runs"]) == {"A1", "C1"}
    damaged = aggregate["runs"]["A1"]["quality"]
    intact = aggregate["runs"]["C1"]["quality"]
    assert damaged["revisions"] == []
    assert damaged["revisions_status"] == "missing"
    assert "revisions.yaml" in damaged["revisions_error"]
    # The sibling still enumerates, so the empty list above is this run's
    # condition and not something the analysis does to every run.
    assert [r["tag"] for r in intact["revisions"]] == ["draft-test-fixture-00"]
    assert intact["revisions_status"] == "read"
    stored = json.loads((campaign.analysis_dir / "aggregate.json").read_text())
    assert stored == aggregate


def test_trajectory_points_follow_checkpoint_calls():
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"a"}],"usage":{"input_tokens":100,"output_tokens":10}}}\n'
        '{"type":"assistant","message":{"id":"m2","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ai-rfc checkpoint c0002-x"}}],"usage":{"input_tokens":50,"output_tokens":5}}}\n'
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


def test_checkpoint_calls_recognizes_either_arm_c_invocation_form():
    """The regenerated arm-C prompt now says the dispatcher form (space);
    the module form (dot) is still a valid, separately-reachable invocation,
    so both must be recognized as the same checkpoint call.
    """
    events = parse_stream(
        '{"type":"assistant","message":{"id":"m1","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"python -m ai_rfc.draft checkpoint /w/manifest.yaml --timeline /w/timeline --cluster c0002-a --out /w/checkpoints"}}]}}\n'
        '{"type":"assistant","message":{"id":"m2","content":[{"type":"tool_use","id":"t2","name":"Bash","input":{"command":"python -m ai_rfc draft checkpoint /w/manifest.yaml --timeline /w/timeline --cluster c0003-b --out /w/checkpoints"}}]}}\n'
    )
    assert checkpoint_calls(events, "C") == [
        {"index": 0, "cluster_id": "c0002-a"},
        {"index": 1, "cluster_id": "c0003-b"},
    ]


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


def _workspace_with_revisions(tmp_path, revisions_yaml: str):
    """A workspace the ledger can read, holding the given revision map.

    ``cluster_artifacts`` reads ``ledger.clusters``, which needs the timeline
    rows, a checkpoint record per cluster and the draft repository's tags, so a
    bare ``revisions.yaml`` would fail these tests for a reason that has
    nothing to do with what kind of revision an entry is.
    """
    workspace = tmp_path / "workspace"
    (workspace / "timeline").mkdir(parents=True)
    (workspace / "timeline" / "clusters.jsonl").write_text(
        '{"id": "c1", "ordinal": 1}\n{"id": "c9", "ordinal": 9}\n'
    )
    for cluster_id in ("c1", "c9"):
        checkpoint = workspace / "checkpoints" / cluster_id
        checkpoint.mkdir(parents=True)
        (checkpoint / "checkpoint.json").write_text("{}\n")
    (workspace / "revisions.yaml").write_text(revisions_yaml)
    draft = workspace / "draft"
    draft.mkdir()
    _vcs(draft, "init", "-q")
    _vcs(draft, "config", "user.email", "t@t")
    _vcs(draft, "config", "user.name", "t")
    (draft / "draft-t.md").write_text("prose\n")
    _vcs(draft, "add", "draft-t.md")
    _vcs(draft, "commit", "-qm", "revision", date="2026-09-03T00:00:00+00:00")
    for tag in ("draft-t-01", "draft-t-02"):
        _vcs(draft, "tag", tag)
    return workspace


def test_a_consolidation_is_not_mistaken_for_its_cluster_s_revision(tmp_path):
    """Regression (R7): the ledger already filters, and must keep doing so.

    Both entries name c1; only the cluster round is c1's own work. The
    consolidation is listed first so that ``kind``, not file order, is what
    decides: on a raw first-match join this fixture returns the consolidation.
    The recorder can produce this order — it refuses a second ``kind: cluster``
    entry for one cluster but never requires a first one, so a consolidation
    naming c1 can be recorded before c1's own round is.
    """
    from ai_rfc.experiment.metrics import cluster_artifacts

    workspace = _workspace_with_revisions(
        tmp_path,
        "revisions:\n"
        "  draft-t-01:\n"
        "    cluster_id: c1\n"
        f"    checkpoint_manifest_sha256: {'a' * 64}\n"
        "    normative_change: false\n"
        "    note: 'the consolidation'\n"
        "    kind: consolidation\n"
        "    checkpoint: consolidations/01\n"
        "  draft-t-02:\n"
        "    cluster_id: c1\n"
        f"    checkpoint_manifest_sha256: {'a' * 64}\n"
        "    normative_change: true\n"
        "    note: 'the cluster round'\n",
    )
    artifacts = cluster_artifacts(workspace, {"id": "c1", "ordinal": 1})
    # The agent's own account of a round is summary's to report, not metrics';
    # what metrics must keep straight is whose tag and whose verdict these are.
    assert artifacts["revision_tag"] == "draft-t-02"
    assert artifacts["normative_change"] is True
    assert artifacts["tag_exists"] and artifacts["artifacts"]


def test_a_cluster_with_only_a_consolidation_is_not_complete(tmp_path):
    """Regression (R7): a consolidation alone must not read as a finished round."""
    from ai_rfc.experiment.metrics import cluster_artifacts

    workspace = _workspace_with_revisions(
        tmp_path,
        "revisions:\n"
        "  draft-t-01:\n"
        "    cluster_id: c9\n"
        f"    checkpoint_manifest_sha256: {'a' * 64}\n"
        "    normative_change: false\n"
        "    note: 'consolidated'\n"
        "    kind: consolidation\n"
        "    checkpoint: consolidations/01\n",
    )
    assert not cluster_artifacts(workspace, {"id": "c9", "ordinal": 9})["artifacts"]
