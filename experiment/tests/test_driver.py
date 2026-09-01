import pytest

from experiment import ExperimentError
from experiment.driver import launch_pending, pending_runs
from experiment.runner import load_status, run_ref

from .conftest import COMPLETE_STEPS


def _scenarios(campaign, write_scenario):
    write_scenario(
        campaign.profile_dir, "A1", {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS}
    )
    write_scenario(
        campaign.profile_dir,
        "B1",
        {"arm": "B", "cost": 0.4, "exit_code": 3, "steps": []},
    )
    write_scenario(
        campaign.profile_dir, "C1", {"arm": "C", "cost": 1.1, "steps": COMPLETE_STEPS}
    )


def test_execute_follows_the_frozen_order_and_resumes(campaign, write_scenario):
    _scenarios(campaign, write_scenario)
    assert pending_runs(campaign) == list(campaign.run_order)
    lines = []
    statuses = launch_pending(campaign, report=lines.append)
    assert [s.run_id for s in statuses] == list(campaign.run_order)
    assert {s.run_id: s.exit_code for s in statuses} == {"A1": 0, "B1": 3, "C1": 0}
    assert pending_runs(campaign) == []
    again = launch_pending(campaign, report=lines.append)
    assert again == statuses
    assert sum("skipping" in line for line in lines) == 3
    for run_id in campaign.run_order:
        ref = run_ref(campaign, run_id)
        assert (ref.workspace / "pristine.sha256").exists()
        assert load_status(ref.run_dir).run_id == run_id


def test_execute_only_runs_the_requested_subset(campaign, write_scenario):
    _scenarios(campaign, write_scenario)
    statuses = launch_pending(campaign, only=["C1"], report=lambda _: None)
    assert [s.run_id for s in statuses] == ["C1"]
    assert pending_runs(campaign) == [r for r in campaign.run_order if r != "C1"]
    with pytest.raises(ExperimentError):
        launch_pending(campaign, only=["Z9"], report=lambda _: None)


def test_execute_refuses_a_run_dir_without_status(campaign, write_scenario):
    _scenarios(campaign, write_scenario)
    (campaign.runs_dir / campaign.run_order[0]).mkdir(parents=True)
    with pytest.raises(ExperimentError) as excinfo:
        launch_pending(campaign, report=lambda _: None)
    assert "without a status record" in str(excinfo.value)


def test_execute_refuses_a_pristine_that_moved_since_the_campaign_froze(
    campaign, write_scenario
):
    """The run order is frozen against one template; ordinals must not shift.

    init_campaign digests every file in the pristine, timeline included, but
    nothing compared it again — so a template regenerated between runs of one
    campaign silently renumbered the clusters a frozen order refers to.
    """
    _scenarios(campaign, write_scenario)
    clusters = campaign.pristine_dir / "timeline" / "clusters.jsonl"
    clusters.write_text(clusters.read_text() + "\n")

    with pytest.raises(ExperimentError) as excinfo:
        launch_pending(campaign, report=lambda _: None)

    message = str(excinfo.value)
    assert "pristine" in message and "clusters.jsonl" in message
