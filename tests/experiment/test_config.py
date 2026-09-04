import hashlib
import json
from pathlib import Path

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.config import (
    CampaignConfig,
    git_describe,
    init_campaign,
    load_campaign,
    render_task,
    run_order,
)


def test_run_order_is_seeded_and_covers_every_block():
    order = run_order(("A", "B", "C"), 2, seed=20260826)
    assert len(order) == 6 and len(set(order)) == 6
    assert {o[0] for o in order[:3]} == {"A", "B", "C"}
    assert {o[0] for o in order[3:]} == {"A", "B", "C"}
    assert all(o[1:] == "1" for o in order[:3]) and all(o[1:] == "2" for o in order[3:])
    assert order == run_order(("A", "B", "C"), 2, seed=20260826)
    assert order != run_order(("A", "B", "C"), 2, seed=1)


def test_render_task_states_the_window():
    text = render_task((2, 11))
    assert "ordinals 2 through 11" in text and "$" not in text


@pytest.fixture
def pristine(tmp_path: Path) -> Path:
    root = tmp_path / "pristine" / "fixture-w02-02"
    root.mkdir(parents=True)
    (root / "pristine.sha256").write_text("00  manifest.yaml\n")
    (root / "pristine.json").write_text(
        json.dumps(
            {
                "target": "fixture",
                "window": [2, 2],
                "clone_head": "x",
                "draft_head": "y",
            }
        )
    )
    return root


def _init(tmp_path, pristine, panther_repo, plugin_root, **overrides):
    # The module's six pre-existing tests call _init(...) with no toolchain, and
    # init_campaign now refuses without one; this default keeps them working
    # without touching every call site. verify() is monkeypatched to (True, ())
    # by the autouse fixture in conftest.py, so this minimal record is enough.
    toolchain = tmp_path / "toolchain.json"
    if not toolchain.exists():
        toolchain.write_text('{"template_home": "/t"}\n')
    kwargs = dict(
        root=tmp_path / "root",
        campaign_id="pilot-test",
        pristine_dir=pristine,
        arms=("A", "B", "C"),
        repeats=2,
        seed=7,
        model="claude-opus-5",
        effort="high",
        budget_usd=25.0,
        timeout_s=7200,
        panther_repo=panther_repo,
        plugin_root=plugin_root,
        python="/venv/bin/python",
        claude_bin="/bin/echo",
        parity={"passed": True, "summary": "38 passed"},
        toolchain=toolchain,
    )
    kwargs.update(overrides)
    return init_campaign(CampaignConfig(**kwargs))


def test_init_campaign_freezes_everything(
    tmp_path, pristine, panther_repo, plugin_root
):
    campaign = _init(tmp_path, pristine, panther_repo, plugin_root)
    assert campaign.dir == tmp_path / "root" / "campaigns" / "pilot-test"
    stored = json.loads((campaign.dir / "campaign.json").read_text())
    assert stored["run_order"] == list(campaign.run_order)
    assert stored["window"] == [2, 2] and stored["target"] == "fixture"
    assert stored["pristine_sha256"] == "00  manifest.yaml\n"
    for arm in "ABC":
        prompt = campaign.prompts_dir / f"arm-{arm}.md"
        assert prompt.exists() and stored["prompt_sha256"][f"arm-{arm}.md"]
    assert (campaign.prompts_dir / "task.md").read_text() == render_task((2, 2))
    for pair in ("A-B", "A-C", "B-C"):
        assert (
            (campaign.prompts_dir / f"diff-{pair}.patch")
            .read_text()
            .startswith("--- arm-")
        )
    shim = campaign.bin_dir / "ai_rfc"
    assert shim.exists() and shim.stat().st_mode & 0o111
    assert "/venv/bin/python" in shim.read_text()
    assert stored["parity"] == {"passed": True, "summary": "38 passed"}
    assert stored["git"]["panther"] and stored["git"]["ai_rfc"]
    assert campaign.split_run_id("B2") == ("B", 2)


def test_init_campaign_refuses_to_overwrite(
    tmp_path, pristine, panther_repo, plugin_root
):
    _init(tmp_path, pristine, panther_repo, plugin_root)
    with pytest.raises(ExperimentError):
        _init(tmp_path, pristine, panther_repo, plugin_root)


def test_load_campaign_round_trips(tmp_path, pristine, panther_repo, plugin_root):
    campaign = _init(tmp_path, pristine, panther_repo, plugin_root)
    loaded = load_campaign(campaign.dir)
    assert loaded == campaign
    with pytest.raises(ExperimentError):
        load_campaign(tmp_path / "nowhere")


def test_git_describe_names_a_commit(panther_repo):
    assert git_describe(panther_repo) and " " not in git_describe(panther_repo)


def test_init_campaign_freezes_an_absolute_claude_binary(
    tmp_path, pristine, panther_repo, plugin_root
):
    """A run's PATH excludes the user's bin dirs, so a bare name would not resolve."""
    campaign = _init(tmp_path, pristine, panther_repo, plugin_root, claude_bin="echo")
    assert Path(campaign.claude_bin).is_absolute()
    assert Path(campaign.claude_bin).exists()

    with pytest.raises(ExperimentError) as excinfo:
        _init(
            tmp_path,
            pristine,
            panther_repo,
            plugin_root,
            campaign_id="no-such-binary",
            claude_bin="definitely-not-a-real-binary-xyz",
        )
    assert "cannot find the claude binary" in str(excinfo.value)


def test_init_refuses_without_a_verified_toolchain(
    pristine, tmp_path, panther_repo, plugin_root, monkeypatch
):
    from ai_rfc.experiment import toolchain as toolchain_module

    record = tmp_path / "toolchain.json"
    record.write_text('{"template_home": "/t"}\n')
    monkeypatch.setattr(
        toolchain_module,
        "verify",
        lambda record, runner=None: (False, ("refcache digest differs",)),
    )
    with pytest.raises(ExperimentError) as excinfo:
        _init(tmp_path, pristine, panther_repo, plugin_root, toolchain=record)
    assert "refcache digest differs" in str(excinfo.value)
    with pytest.raises(ExperimentError) as excinfo:
        _init(tmp_path, pristine, panther_repo, plugin_root, toolchain=None)
    assert "toolchain" in str(excinfo.value)


def test_init_records_the_toolchain_digest(
    pristine, tmp_path, panther_repo, plugin_root, monkeypatch
):
    from ai_rfc.experiment import toolchain as toolchain_module

    record = tmp_path / "toolchain.json"
    record.write_text('{"template_home": "/t"}\n')
    monkeypatch.setattr(
        toolchain_module, "verify", lambda record, runner=None: (True, ())
    )
    campaign = _init(tmp_path, pristine, panther_repo, plugin_root, toolchain=record)
    assert campaign.toolchain == str(record) and campaign.template_home == "/t"
    assert campaign.toolchain_sha256 == hashlib.sha256(record.read_bytes()).hexdigest()
    stored = json.loads((campaign.dir / "campaign.json").read_text())
    assert stored["toolchain_sha256"] == campaign.toolchain_sha256
