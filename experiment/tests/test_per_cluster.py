import json
import sys

import pytest

from experiment.config import CampaignConfig, init_campaign
from experiment.driver import launch_pending
from experiment.metrics import analyze_run
from experiment.per_cluster import next_cluster
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
        CampaignConfig(
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
            session_mode="per-cluster",
        )
    )


def test_the_mode_is_frozen_into_the_campaign_record(per_cluster_campaign):
    """A run must say how it was executed, not only what it produced."""
    stored = json.loads((per_cluster_campaign.dir / "campaign.json").read_text())
    assert stored["session_mode"] == "per-cluster"


def test_a_campaign_frozen_before_the_field_existed_still_loads(campaign):
    """The default is what keeps older campaign.json files readable."""
    assert campaign.session_mode == "single"


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
    launch_pending(per_cluster_campaign, report=lambda _: None)
    workspace = per_cluster_campaign.runs_dir / "A1" / "workspace"
    outstanding = next_cluster(workspace)
    assert outstanding is not None and outstanding["ordinal"] == 1


def _stub_spawn(per_cluster, monkeypatch, *, sessions_per_cluster: int):
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

    monkeypatch.setattr(per_cluster, "spawn", fake_spawn)
    monkeypatch.setattr(per_cluster, "cluster_artifacts", fake_artifacts)
    return calls


def test_one_session_is_spawned_per_outstanding_cluster(
    per_cluster_campaign, monkeypatch
):
    import experiment.per_cluster as per_cluster

    calls = _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(
        per_cluster,
        "window_clusters",
        lambda _ws: [{"ordinal": 1, "id": "c1"}, {"ordinal": 2, "id": "c2"}],
    )
    ref = _ref(per_cluster_campaign)
    exit_code, timed_out, sessions = per_cluster.run_per_cluster(
        per_cluster_campaign, ref
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
    import experiment.per_cluster as per_cluster

    calls = _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=99)
    monkeypatch.setattr(
        per_cluster,
        "window_clusters",
        lambda _ws: [{"ordinal": 1, "id": "c1"}, {"ordinal": 2, "id": "c2"}],
    )
    ref = _ref(per_cluster_campaign)
    exit_code, _, sessions = per_cluster.run_per_cluster(per_cluster_campaign, ref)
    assert exit_code != 0
    # Retried the first cluster, then stopped: never reached the second.
    assert sessions == per_cluster.ATTEMPTS_PER_CLUSTER
    assert calls["n"] == per_cluster.ATTEMPTS_PER_CLUSTER


def test_a_half_finished_cluster_is_named_before_it_is_retried(
    per_cluster_campaign, monkeypatch
):
    """A checkpoint without its tag cannot simply be redone.

    write_checkpoint is write-once and raises when the directory exists, so the
    retry an unfinished cluster gets may spend a whole session rediscovering
    that. Whether the agent recovers is not knowable from here; what is fixable
    is the silence, so the state is named before the attempt rather than after
    two of them.
    """
    import experiment.per_cluster as per_cluster

    _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=99)
    monkeypatch.setattr(
        per_cluster, "window_clusters", lambda _ws: [{"ordinal": 1, "id": "c1"}]
    )
    monkeypatch.setattr(
        per_cluster,
        "cluster_artifacts",
        lambda _ws, _row: {
            "artifacts": False,
            "pre_seeded": False,
            "checkpoint": True,
            "revision_tag": None,
            "tag_exists": False,
        },
    )
    lines: list[str] = []

    per_cluster.run_per_cluster(
        per_cluster_campaign, _ref(per_cluster_campaign), report=lines.append
    )

    assert any("checkpoint present" in line for line in lines), lines


def test_an_untouched_cluster_is_not_described_as_half_finished(
    per_cluster_campaign, monkeypatch
):
    import experiment.per_cluster as per_cluster

    _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(
        per_cluster, "window_clusters", lambda _ws: [{"ordinal": 1, "id": "c1"}]
    )
    lines: list[str] = []

    per_cluster.run_per_cluster(
        per_cluster_campaign, _ref(per_cluster_campaign), report=lines.append
    )

    assert not any("checkpoint present" in line for line in lines), lines


def _ref(campaign):
    from experiment.runner import run_ref

    ref = run_ref(campaign, campaign.run_order[0])
    ref.run_dir.mkdir(parents=True, exist_ok=True)
    ref.workspace.mkdir(parents=True, exist_ok=True)
    return ref


def _clusters(count: int):
    return [{"ordinal": n, "id": f"c{n}"} for n in range(1, count + 1)]


def _write_events(path, costs):
    path.write_text(
        "".join(
            json.dumps({"type": "result", "total_cost_usd": c}) + "\n" for c in costs
        )
    )


def test_a_killed_session_is_not_charged_the_previous_ones_cost(tmp_path):
    """A session killed on its cap emits no result event.

    Taking the transcript's tail would then re-read the previous session's
    event, charging its cost twice — overstating the run and writing the wrong
    figure into the per-session record, on exactly the path this design exists
    to tolerate.
    """
    from experiment.per_cluster import _session_cost

    events = tmp_path / "events.jsonl"

    _write_events(events, [2.0])
    assert _session_cost(events, 0) == (2.0, 1)

    # The next session is killed: the transcript is unchanged.
    assert _session_cost(events, 1) == (0.0, 1)

    # A session that emits several results is summed, not sampled.
    _write_events(events, [2.0, 1.0, 0.5])
    assert _session_cost(events, 1) == (1.5, 3)


def test_the_budget_caps_the_run_not_each_session(per_cluster_campaign, monkeypatch):
    """Otherwise sixty-nine clusters could spend sixty-nine times the flag.

    A budget's whole job is the pathological case, so a mode where it silently
    became per-session would have removed the only thing standing between a
    looping agent and the card.
    """
    import experiment.per_cluster as per_cluster

    calls = _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(per_cluster, "window_clusters", lambda _ws: _clusters(10))
    # Each session spends the whole $1.00 campaign budget.
    monkeypatch.setattr(per_cluster, "_session_cost", lambda _p, seen: (1.0, seen + 1))

    ref = _ref(per_cluster_campaign)
    exit_code, _, sessions = per_cluster.run_per_cluster(per_cluster_campaign, ref)
    assert sessions == 1 and calls["n"] == 1
    assert exit_code != 0


def test_each_session_is_given_only_what_the_run_has_left(
    per_cluster_campaign, monkeypatch
):
    """The cap holds by construction, not only by the loop's check."""
    import experiment.per_cluster as per_cluster

    given: list[float] = []

    def capture(campaign, ref, task=None, budget_usd=None):
        given.append(budget_usd)
        return ["fake"]

    _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(per_cluster, "window_clusters", lambda _ws: _clusters(3))
    monkeypatch.setattr(per_cluster, "prepare_run_argv", capture)
    monkeypatch.setattr(per_cluster, "_session_cost", lambda _p, seen: (0.25, seen + 1))

    per_cluster.run_per_cluster(per_cluster_campaign, _ref(per_cluster_campaign))
    assert given == [1.0, 0.75, 0.5]


def test_every_session_records_the_argv_it_actually_ran(
    per_cluster_campaign, monkeypatch
):
    """argv.json holds the whole-window vector, which no session executed."""
    import experiment.per_cluster as per_cluster

    _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(per_cluster, "window_clusters", lambda _ws: _clusters(2))
    monkeypatch.setattr(per_cluster, "_session_cost", lambda _p, seen: (0.1, seen + 1))

    ref = _ref(per_cluster_campaign)
    per_cluster.run_per_cluster(per_cluster_campaign, ref)
    rows = [
        json.loads(line)
        for line in (ref.run_dir / per_cluster.SESSIONS_FILE).read_text().splitlines()
    ]
    assert [r["ordinal"] for r in rows] == [1, 2]
    assert [r["cumulative_cost_usd"] for r in rows] == [0.1, 0.2]
    assert all(r["argv"] for r in rows)


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
    launch_pending(per_cluster_campaign, report=lambda _: None)
    run_dir = per_cluster_campaign.runs_dir / "A1"

    events = parse_stream((run_dir / EVENTS_FILE).read_text(errors="replace"))
    sessions = len(result_events(events))
    final = json.loads((run_dir / RESULT_FILE).read_text())
    assert final["total_cost_usd"] == pytest.approx(1.0 * sessions)

    analyzed = analyze_run(per_cluster_campaign, "A1")
    assert analyzed["cost"]["total_cost_usd"] == pytest.approx(1.0 * sessions)
