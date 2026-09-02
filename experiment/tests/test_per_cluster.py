import json
import sys

import pytest

from experiment.config import CampaignConfig, init_campaign
from experiment.driver import launch_pending
from experiment.metrics import analyze_run
from experiment.per_cluster import next_cluster, surface_shortfall
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
    assert _session_cost(events, 0) == (2.0, 1, 0)

    # The next session is killed: the transcript is unchanged.
    assert _session_cost(events, 1) == (0.0, 1, 0)

    # A session that emits several results is summed, not sampled.
    _write_events(events, [2.0, 1.0, 0.5])
    assert _session_cost(events, 1) == (1.5, 3, 0)


def test_a_truncated_line_does_not_freeze_the_budget(tmp_path):
    """The kill this loop tolerates is the one that damages the transcript.

    A kill can truncate a line mid-write and the next session appends onto that
    tail, leaving one permanently unparseable line. Refusing the whole file
    there would freeze the accumulated spend, so the ceiling could never be
    reached again and only the wall clock would still bound the run — the
    budget failing open on precisely the interruption it exists for.
    """
    from experiment.per_cluster import _session_cost

    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"type": "result", "total_cost_usd": 2.0})
        + "\n"
        + '{"type": "result", "total_cost_u'  # killed mid-write
        + json.dumps({"type": "result", "total_cost_usd": 1.0})
        + "\n"
    )

    # The truncated fragment carries no newline, so the next session's first
    # line is appended onto it and the two become one garbled line. That
    # session's figure is genuinely unrecoverable — which is the damage, not
    # the defect.
    cost, seen, damaged = _session_cost(events, 0)
    assert damaged == 1, "the unreadable line must be counted, not hidden"
    assert (cost, seen) == (2.0, 1)

    # The defect is the freeze. Under a strict read every later call re-parses
    # from byte 0, hits the same garbled line and reports 0.0 forever, so spend
    # stops growing and the ceiling can never be reached again.
    with events.open("a") as handle:
        handle.write(json.dumps({"type": "result", "total_cost_usd": 4.0}) + "\n")

    assert _session_cost(events, seen) == (4.0, 2, 1), "spend must keep growing"


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
    monkeypatch.setattr(
        per_cluster, "_session_cost", lambda _p, seen: (1.0, seen + 1, 0)
    )

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
    monkeypatch.setattr(
        per_cluster, "_session_cost", lambda _p, seen: (0.25, seen + 1, 0)
    )

    per_cluster.run_per_cluster(per_cluster_campaign, _ref(per_cluster_campaign))
    assert given == [1.0, 0.75, 0.5]


def test_every_session_records_the_argv_it_actually_ran(
    per_cluster_campaign, monkeypatch
):
    """argv.json holds the whole-window vector, which no session executed."""
    import experiment.per_cluster as per_cluster

    _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(per_cluster, "window_clusters", lambda _ws: _clusters(2))
    monkeypatch.setattr(
        per_cluster, "_session_cost", lambda _p, seen: (0.1, seen + 1, 0)
    )

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


def _transcript(tmp_path, *servers):
    """A transcript whose init event announces ``servers``, as a session does."""
    path = tmp_path / "events.jsonl"
    init = {
        "type": "system",
        "subtype": "init",
        "mcp_servers": [{"name": n, "status": s} for n, s in servers],
    }
    path.write_text(json.dumps(init) + "\n")
    return path


def test_a_failed_server_is_named_for_the_arm_that_declared_it(tmp_path):
    """The exact shape a real run emitted while spending $5.90 on no tools.

    Its first event said ai_rfc had failed and nothing read it, so the run mined
    thirty-nine claims the schema rejects and exited 0 — the tools that validate
    every write being the ones that were missing.
    """
    events = _transcript(tmp_path, ("ai_rfc", "failed"))
    assert surface_shortfall("A", events) == (True, "ai_rfc=failed")


def test_a_connected_server_is_no_shortfall(tmp_path):
    events = _transcript(tmp_path, ("ai_rfc", "connected"))
    assert surface_shortfall("A", events) == (True, None)


def test_the_plugin_loading_path_counts_as_connected(tmp_path):
    """``--plugin-dir`` names the same server ``plugin:<plugin>:ai_rfc``."""
    events = _transcript(tmp_path, ("plugin:ai-rfc:ai_rfc", "connected"))
    assert surface_shortfall("A", events) == (True, None)


def test_an_arm_that_mounts_no_server_is_never_short(tmp_path):
    """B and C reach the substrate through the CLI, so no server is correct."""
    events = _transcript(tmp_path, ("ai_rfc", "failed"))
    assert surface_shortfall("B", events) == (True, None)
    assert surface_shortfall("C", events) == (True, None)


def test_a_session_that_never_announced_is_not_judged(tmp_path):
    """No init event is a session too young to have said, not a fault."""
    path = tmp_path / "events.jsonl"
    path.write_text("")
    assert surface_shortfall("A", path) == (False, None)
    assert surface_shortfall("A", tmp_path / "absent.jsonl") == (False, None)


def test_a_silent_first_session_does_not_forfeit_the_guard(
    per_cluster_campaign, monkeypatch
):
    """ "Cannot tell yet" must not spend the one check the window gets.

    A first session that writes no readable transcript has not said what it
    mounted. Judging once *per session* would skip the check there and never
    return to it, leaving the remaining clusters unguarded — so the verdict is
    taken on the first session that can actually be judged.
    """
    import experiment.per_cluster as per_cluster

    _stub_spawn(per_cluster, monkeypatch, sessions_per_cluster=1)
    monkeypatch.setattr(
        per_cluster,
        "window_clusters",
        lambda _ws: [{"ordinal": 1, "id": "c1"}, {"ordinal": 2, "id": "c2"}],
    )
    verdicts = iter([(False, None), (True, "ai_rfc=failed")])
    monkeypatch.setattr(
        per_cluster, "surface_shortfall", lambda _arm, _path: next(verdicts)
    )
    lines: list[str] = []

    exit_code, _, sessions = per_cluster.run_per_cluster(
        per_cluster_campaign, _ref(per_cluster_campaign), report=lines.append
    )

    assert exit_code == 1, "the second session's verdict must still stop the run"
    assert sessions == 2
    assert any("declares the ai_rfc tool surface" in line for line in lines), lines


def test_a_per_cluster_run_reports_through_the_launcher(
    per_cluster_campaign, monkeypatch
):
    """launch() never forwarded report, so the loop's lines split onto stdout.

    Entering at launch_pending rather than run_per_cluster is the whole point:
    the loop already accepted a report callable, and only the launcher failed
    to hand one over.
    """
    import experiment.per_cluster as per_cluster

    state = {"done": False}

    def fake_spawn(*_args, events_path, **_kwargs):
        events_path.write_text(
            json.dumps({"type": "result", "subtype": "success"}) + "\n"
        )
        state["done"] = True
        return 0, False

    monkeypatch.setattr(per_cluster, "spawn", fake_spawn)
    monkeypatch.setattr(
        per_cluster,
        "cluster_artifacts",
        lambda _ws, _row: {"artifacts": state["done"], "pre_seeded": False},
    )
    monkeypatch.setattr(
        per_cluster, "window_clusters", lambda _ws: [{"ordinal": 1, "id": "c1"}]
    )
    lines: list[str] = []

    launch_pending(per_cluster_campaign, report=lines.append)

    # "attempt" and not "cluster": the campaign is *named* per-cluster, so its
    # id lands in the transcript path the driver reports and would match a
    # laxer needle whether or not the loop's own lines ever arrived.
    assert any("attempt" in line for line in lines), lines


def test_window_progress_counts_only_the_work_this_run_can_do(
    tmp_path, monkeypatch
):
    """Pre-seeded clusters are a baseline's work, not this run's.

    Counting them would report progress the run did not make, and the
    denominator would stop meaning "remaining".
    """
    import experiment.per_cluster as per_cluster

    rows = [{"ordinal": n, "id": f"c{n}"} for n in (1, 2, 3, 4)]
    artifacts = {
        "c1": {"artifacts": False, "pre_seeded": True},
        "c2": {"artifacts": True, "pre_seeded": False},
        "c3": {"artifacts": False, "pre_seeded": False},
        "c4": {"artifacts": True, "pre_seeded": False},
    }
    monkeypatch.setattr(per_cluster, "window_clusters", lambda _ws: rows)
    monkeypatch.setattr(
        per_cluster, "cluster_artifacts", lambda _ws, row: artifacts[row["id"]]
    )

    row, position, done, total = per_cluster.window_progress(tmp_path)

    assert row["id"] == "c3"
    # c1 is pre-seeded: neither numerator nor denominator.
    assert (position, done, total) == (2, 2, 3)
    # c4 is finished but sits after c3, so position is not done + 1.
    assert position != done + 1


def test_next_cluster_still_returns_just_the_row(tmp_path, monkeypatch):
    """The wrapper keeps the signature its existing callers use."""
    import experiment.per_cluster as per_cluster

    monkeypatch.setattr(
        per_cluster, "window_clusters", lambda _ws: [{"ordinal": 1, "id": "c1"}]
    )
    monkeypatch.setattr(
        per_cluster,
        "cluster_artifacts",
        lambda _ws, _row: {"artifacts": False, "pre_seeded": False},
    )

    assert per_cluster.next_cluster(tmp_path)["id"] == "c1"
