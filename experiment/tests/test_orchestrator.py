import json
import sys

import pytest

from experiment.config import CampaignRequest, init_campaign
from experiment.matrix import execute
from experiment.metrics import analyze_run
from experiment.orchestrator import next_cluster
from experiment.runner import EVENTS_FILE, RESULT_FILE
from experiment.stream import parse_stream, result_events

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE, fixture_target


@pytest.fixture
def wide_pristine(fixture_workspace, panther_repo, template_repo, tmp_path):
    """A pristine workspace whose window holds both fixture clusters.

    The shared fixture windows one cluster, which cannot distinguish a run of
    one session per cluster from a run of one session — the very thing these
    tests exist to check.
    """
    from experiment.workspace import prepare

    template, commit = template_repo
    return prepare(
        fixture_target(fixture_workspace, window=(1, 2)),
        root=tmp_path / "root",
        panther_repo=panther_repo,
        template=template,
        template_commit=commit,
    )


@pytest.fixture
def per_cluster_campaign(wide_pristine, panther_repo, plugin_root, tmp_path):
    """A one-arm campaign executed as one agent session per cluster."""
    return init_campaign(
        CampaignRequest(
            root=tmp_path / "root",
            campaign_id="per-cluster",
            pristine_dir=wide_pristine,
            arms=("A",),
            repeats=1,
            seed=7,
            model="fake-model",
            effort="high",
            budget_usd=1.0,
            timeout_s=900,
            panther_repo=panther_repo,
            plugin_root=plugin_root,
            python=sys.executable,
            claude_bin=str(FAKE_CLAUDE),
            parity={"passed": True, "summary": "test"},
            sessions="per-cluster",
        )
    )


def test_the_mode_is_frozen_into_the_campaign_record(per_cluster_campaign):
    """A run must say how it was executed, not only what it produced."""
    stored = json.loads((per_cluster_campaign.dir / "campaign.json").read_text())
    assert stored["sessions"] == "per-cluster"


def test_a_campaign_frozen_before_the_field_existed_still_loads(campaign):
    """The default is what keeps older campaign.json files readable."""
    assert campaign.sessions == "single"


def test_next_cluster_is_read_from_the_workspace_not_remembered(
    per_cluster_campaign, write_scenario
):
    """Progress between sessions is the workspace, so it survives a kill.

    The fixture's scenario completes the second cluster, so the first is still
    outstanding afterwards — which is what says the answer came off disk rather
    than from a counter.
    """
    write_scenario(
        per_cluster_campaign.profile_dir,
        "A1",
        {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
    )
    execute(per_cluster_campaign, report=lambda _: None)
    workspace = per_cluster_campaign.runs_dir / "A1" / "workspace"
    outstanding = next_cluster(workspace)
    assert outstanding is not None and outstanding["ordinal"] == 1


def _stub_spawn(orchestrator, monkeypatch, *, sessions_per_cluster: int):
    """Drive the loop with a spawn that finishes clusters in ordinal order.

    The fake claude replays a scenario pinned to one hardcoded cluster, so it
    cannot stand in for an agent working through a window. The loop's control
    flow is what these tests are about, so it is driven directly: after N
    sessions, clusters up to ordinal N // sessions_per_cluster are finished.
    Setting that unreachably high models a cluster that never finishes.
    """
    calls = {"n": 0}

    def fake_spawn(*_args, **_kwargs):
        calls["n"] += 1
        return 0, False

    def fake_artifacts(_workspace, cluster):
        needed = cluster["ordinal"] * sessions_per_cluster
        return {"artifacts": calls["n"] >= needed, "pre_seeded": False}

    monkeypatch.setattr(orchestrator, "spawn", fake_spawn)
    monkeypatch.setattr(orchestrator, "cluster_artifacts", fake_artifacts)
    return calls


def test_one_session_is_spawned_per_outstanding_cluster(
    per_cluster_campaign, monkeypatch
):
    import experiment.orchestrator as orchestrator

    calls = _stub_spawn(orchestrator, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(
        orchestrator,
        "window_clusters",
        lambda _ws: [{"ordinal": 1, "id": "c1"}, {"ordinal": 2, "id": "c2"}],
    )
    spec = _spec(per_cluster_campaign)
    exit_code, timed_out, sessions = orchestrator.run_per_cluster(
        per_cluster_campaign, spec
    )
    assert (exit_code, timed_out) == (0, False)
    assert sessions == 2 and calls["n"] == 2


def test_a_cluster_that_will_not_finish_halts_rather_than_being_skipped(
    per_cluster_campaign, monkeypatch
):
    """Later clusters' prose builds on earlier prose.

    Skipping one and continuing would leave a draft with a hole in it, which is
    worse than a short draft, and the gap would not be visible in the result.
    """
    import experiment.orchestrator as orchestrator

    calls = _stub_spawn(orchestrator, monkeypatch, sessions_per_cluster=99)
    monkeypatch.setattr(
        orchestrator,
        "window_clusters",
        lambda _ws: [{"ordinal": 1, "id": "c1"}, {"ordinal": 2, "id": "c2"}],
    )
    spec = _spec(per_cluster_campaign)
    exit_code, _, sessions = orchestrator.run_per_cluster(per_cluster_campaign, spec)
    assert exit_code != 0
    # Retried the first cluster, then stopped: never reached the second.
    assert sessions == orchestrator.ATTEMPTS_PER_CLUSTER
    assert calls["n"] == orchestrator.ATTEMPTS_PER_CLUSTER


def _spec(campaign):
    from experiment.runner import run_spec

    spec = run_spec(campaign, campaign.run_order[0])
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    spec.workspace.mkdir(parents=True, exist_ok=True)
    return spec


def test_a_single_session_run_records_exactly_what_it_always_did(
    per_cluster_campaign, write_scenario
):
    """Nothing downstream may be able to tell how the run was executed.

    The transcript is appended to across sessions and the result events folded,
    so the audit and the metrics read what they read for a single-session run —
    except that the cost is the run's rather than its last cluster's.
    """
    write_scenario(
        per_cluster_campaign.profile_dir,
        "A1",
        {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
    )
    execute(per_cluster_campaign, report=lambda _: None)
    run_dir = per_cluster_campaign.runs_dir / "A1"

    events = parse_stream((run_dir / EVENTS_FILE).read_text(errors="replace"))
    sessions = len(result_events(events))
    final = json.loads((run_dir / RESULT_FILE).read_text())
    assert final["total_cost_usd"] == pytest.approx(1.0 * sessions)

    analyzed = analyze_run(per_cluster_campaign, "A1")
    assert analyzed["cost"]["total_cost_usd"] == pytest.approx(1.0 * sessions)
