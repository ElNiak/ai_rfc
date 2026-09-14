import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_rfc.driver import DriverError
from ai_rfc.driver.render import SLOT_TABLES, arm_prompt, task_template_path
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


def _init(tmp_path, pristine, plugin_root, **overrides):
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
        plugin_root=plugin_root,
        python="/venv/bin/python",
        claude_bin="/bin/echo",
        parity={"passed": True, "summary": "38 passed"},
        toolchain=toolchain,
    )
    kwargs.update(overrides)
    return init_campaign(CampaignConfig(**kwargs))


def test_init_campaign_freezes_everything(tmp_path, pristine, plugin_root):
    campaign = _init(tmp_path, pristine, plugin_root)
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
    shim = campaign.bin_dir / "ai-rfc"
    assert shim.exists() and shim.stat().st_mode & 0o111
    assert "/venv/bin/python" in shim.read_text()
    assert stored["parity"] == {"passed": True, "summary": "38 passed"}
    # The harness no longer takes a PANTHER checkout, so it records none: a
    # `git describe` of its own root under a `panther` label named the wrong
    # repository, and `panther_repo` held this package's root, not PANTHER's.
    assert "panther" not in stored["git"] and stored["git"]["ai_rfc"]
    assert "panther_repo" not in stored
    assert campaign.split_run_id("B2") == ("B", 2)


def test_an_interpreter_path_cannot_forge_a_command_in_the_shim(
    tmp_path, pristine, plugin_root
):
    """``--python`` is an operator's string and lands inside a ``sh`` script.

    Its default is :data:`sys.executable`, which is why this had never been
    seen; its value is whatever was typed. The body wrote ``exec "{python}"``,
    and inside a double-quoted word ``sh`` still expands ``$(...)``, a
    backtick and a backslash — so an interpreter under a directory whose name
    contains a substitution ran it, every time any session invoked the shim.

    Measured before the fix, on this very shape: exit 126 with the marker
    written. The assertion is both halves, because a shim that merely refused
    to run would also write no marker and would tell nobody why.
    """
    home = tmp_path / "py$(touch forged)"
    home.mkdir()
    interpreter = home / "python"
    # A wrapper rather than a symlink: a symlinked interpreter resolves its
    # prefix from the link's own directory and would not import ai_rfc.
    interpreter.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    interpreter.chmod(0o755)

    campaign = _init(tmp_path, pristine, plugin_root, python=str(interpreter))
    # ``cwd`` is where the forged ``touch`` would land: a directory name
    # cannot hold a ``/``, so the payload has to be relative, and asserting on
    # it means saying where it would appear.
    done = subprocess.run(
        [str(campaign.bin_dir / "ai-rfc"), "--help"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert not (tmp_path / "forged").exists()
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("usage: ai-rfc <verb> [args]")


def test_init_campaign_refuses_to_overwrite(tmp_path, pristine, plugin_root):
    _init(tmp_path, pristine, plugin_root)
    with pytest.raises(ExperimentError):
        _init(tmp_path, pristine, plugin_root)


def test_load_campaign_round_trips(tmp_path, pristine, plugin_root):
    campaign = _init(tmp_path, pristine, plugin_root)
    loaded = load_campaign(campaign.dir)
    assert loaded == campaign
    with pytest.raises(ExperimentError):
        load_campaign(tmp_path / "nowhere")


def test_load_campaign_reads_a_record_written_before_the_retirement(
    tmp_path, pristine, plugin_root
):
    """An archived campaign keeps its ``panther_repo`` and ``git.panther``.

    A recording is not edited to match a later retirement, so the loader must
    read the shape it was written in. Both retired keys are put back exactly
    as ``docs/experiments/2026-08-31-pilot-aioquic/campaign.json`` carries
    them; the loader drops them rather than resurrecting a field to hold
    them, which is what the second assertion pins.
    """
    campaign = _init(tmp_path, pristine, plugin_root)
    path = campaign.dir / "campaign.json"
    archived = json.loads(path.read_text())
    archived["panther_repo"] = "/somewhere/PANTHER"
    archived["git"] = dict(archived["git"], panther="v1.1.3-839-g226608938")
    path.write_text(json.dumps(archived, indent=2, sort_keys=True) + "\n")

    loaded = load_campaign(campaign.dir)

    assert loaded.id == campaign.id
    # Dropped, not resurrected into a field that would then be written back.
    assert not hasattr(loaded, "panther_repo")
    # Inside `git` it survives untouched: the loader reads a recording, it
    # does not edit one.
    assert loaded.git["panther"] == "v1.1.3-839-g226608938"


def test_git_describe_names_a_commit(panther_repo):
    assert git_describe(panther_repo) and " " not in git_describe(panther_repo)


def test_init_campaign_freezes_an_absolute_claude_binary(
    tmp_path, pristine, plugin_root
):
    """A run's PATH excludes the user's bin dirs, so a bare name would not resolve."""
    campaign = _init(tmp_path, pristine, plugin_root, claude_bin="echo")
    assert Path(campaign.claude_bin).is_absolute()
    assert Path(campaign.claude_bin).exists()

    with pytest.raises(ExperimentError) as excinfo:
        _init(
            tmp_path,
            pristine,
            plugin_root,
            campaign_id="no-such-binary",
            claude_bin="definitely-not-a-real-binary-xyz",
        )
    assert "cannot find the claude binary" in str(excinfo.value)


def test_init_refuses_without_a_verified_toolchain(
    pristine, tmp_path, plugin_root, monkeypatch
):
    from ai_rfc import toolchain as toolchain_module

    record = tmp_path / "toolchain.json"
    record.write_text('{"template_home": "/t"}\n')
    monkeypatch.setattr(
        toolchain_module,
        "verify",
        lambda record, runner=None: (False, ("refcache digest differs",)),
    )
    with pytest.raises(ExperimentError) as excinfo:
        _init(tmp_path, pristine, plugin_root, toolchain=record)
    assert "refcache digest differs" in str(excinfo.value)
    with pytest.raises(ExperimentError) as excinfo:
        _init(tmp_path, pristine, plugin_root, toolchain=None)
    assert "needs a verified toolchain" in str(excinfo.value)


def test_init_records_the_toolchain_digest(
    pristine, tmp_path, plugin_root, monkeypatch
):
    from ai_rfc import toolchain as toolchain_module

    record = tmp_path / "toolchain.json"
    record.write_text('{"template_home": "/t"}\n')
    monkeypatch.setattr(
        toolchain_module, "verify", lambda record, runner=None: (True, ())
    )
    campaign = _init(tmp_path, pristine, plugin_root, toolchain=record)
    assert campaign.toolchain == str(record) and campaign.template_home == "/t"
    assert campaign.toolchain_sha256 == hashlib.sha256(record.read_bytes()).hexdigest()
    stored = json.loads((campaign.dir / "campaign.json").read_text())
    assert stored["toolchain_sha256"] == campaign.toolchain_sha256


def test_a_campaign_frozen_before_the_toolchain_fields_existed_still_loads(
    tmp_path, pristine, plugin_root
):
    """The three new fields default to None, so a pre-existing campaign.json loads.

    Mirrors test_per_cluster.py's precedent for session_mode; that file is
    outside this fix round's file list, so this is the equivalent coverage
    for toolchain/toolchain_sha256/template_home, kept here instead.
    """
    campaign = _init(tmp_path, pristine, plugin_root)
    stored_path = campaign.dir / "campaign.json"
    payload = json.loads(stored_path.read_text())
    for key in ("toolchain", "toolchain_sha256", "template_home"):
        del payload[key]
    stored_path.write_text(json.dumps(payload))
    loaded = load_campaign(campaign.dir)
    assert loaded.toolchain is None
    assert loaded.toolchain_sha256 is None
    assert loaded.template_home is None


def test_campaign_init_cli_needs_a_toolchain_when_none_is_provisioned(
    tmp_path, pristine, capsys
):
    """`--toolchain` omitted, and no ``<root>/tools/toolchain.json`` exists.

    cli.py's default resolution must leave ``CampaignConfig.toolchain`` as
    ``None`` (mirroring ``workspace prepare``'s guard) so the brief's "needs
    a verified toolchain" message is reachable from the CLI, rather than
    always resolving to a path that then fails for an unrelated reason.
    """
    from ai_rfc.experiment import cli

    code = cli.main(
        [
            "campaign",
            "init",
            "--root",
            str(tmp_path / "root"),
            "--id",
            "x",
            "--baseline",
            str(pristine),
            "--claude",
            "/bin/echo",
            "--skip-parity",
        ]
    )
    assert code == 1
    assert "needs a verified toolchain" in capsys.readouterr().err


def test_render_task_reads_the_template_it_is_given(tmp_path):
    template = tmp_path / "task.tmpl.md"
    template.write_text("Ordinals $low..$high, FROZEN COPY.\n")
    assert render_task((3, 3), template=template) == "Ordinals 3..3, FROZEN COPY.\n"


def test_init_freezes_the_task_template_beside_the_rendering(
    pristine, tmp_path, plugin_root
):
    from ai_rfc.experiment.config import TASK_TEMPLATE

    campaign = _init(tmp_path, pristine, plugin_root)
    frozen = campaign.prompts_dir / "task.tmpl.md"
    assert frozen.read_bytes() == TASK_TEMPLATE.read_bytes()
    assert campaign.task_template == frozen
    assert (
        campaign.prompt_sha256["task.tmpl.md"]
        == hashlib.sha256(frozen.read_bytes()).hexdigest()
    )
    assert "task.md" in campaign.prompt_sha256


def test_init_freezes_the_template_and_the_profile_it_was_given(
    tmp_path, pristine, plugin_root
):
    """What an optimizer proposes is what the campaign is pinned to."""
    template = "{{preamble}}\n\n{{guidance}}\n"
    campaign = _init(
        tmp_path,
        pristine,
        plugin_root,
        arms=("A",),
        loop_template=template,
        task_profile="interview",
    )
    loaded = load_campaign(campaign.dir)
    assert loaded.task_profile == "interview"
    assert loaded.loop_template_sha256 == hashlib.sha256(template.encode()).hexdigest()
    frozen = campaign.prompts_dir / "loop.tmpl.md"
    assert frozen.read_text() == template
    assert campaign.prompt_sha256["loop.tmpl.md"] == loaded.loop_template_sha256
    assert (campaign.prompts_dir / "arm-A.md").read_text() == arm_prompt(
        "A", plugin_root, profile="interview"
    )
    assert (campaign.prompts_dir / "task.md").read_text() == render_task(
        (2, 2), profile="interview"
    )
    assert (
        campaign.task_template.read_bytes()
        == task_template_path("interview").read_bytes()
    )


def test_a_campaign_without_a_template_records_no_template_digest(
    tmp_path, pristine, plugin_root
):
    campaign = _init(tmp_path, pristine, plugin_root)
    assert campaign.loop_template_sha256 is None
    assert campaign.task_profile == "loop"
    assert not (campaign.prompts_dir / "loop.tmpl.md").exists()
    assert "loop.tmpl.md" not in campaign.prompt_sha256


def test_a_campaign_frozen_before_the_profile_fields_existed_still_loads(
    tmp_path, pristine, plugin_root
):
    campaign = _init(tmp_path, pristine, plugin_root)
    stored_path = campaign.dir / "campaign.json"
    payload = json.loads(stored_path.read_text())
    for key in ("task_profile", "loop_template_sha256"):
        del payload[key]
    stored_path.write_text(json.dumps(payload))
    loaded = load_campaign(campaign.dir)
    assert loaded.task_profile == "loop"
    assert loaded.loop_template_sha256 is None


def test_a_campaign_defaults_to_consolidating_every_ten_clusters(
    tmp_path, pristine, plugin_root
):
    campaign = _init(tmp_path, pristine, plugin_root)
    assert campaign.consolidate_every == 10
    stored = json.loads((campaign.dir / "campaign.json").read_text())
    assert stored["consolidate_every"] == 10


def test_a_chosen_consolidation_interval_survives_the_round_trip(
    tmp_path, pristine, plugin_root
):
    """Only a non-default value can distinguish wiring from a lucky default.

    Both dataclasses default to 10, so a campaign built without an override
    says nothing about whether init_campaign threaded the ask into the
    record. Task 8's ``--consolidate-every`` rests entirely on that thread.
    """
    campaign = _init(tmp_path, pristine, plugin_root, consolidate_every=3)
    assert campaign.consolidate_every == 3
    stored = json.loads((campaign.dir / "campaign.json").read_text())
    assert stored["consolidate_every"] == 3
    assert load_campaign(campaign.dir).consolidate_every == 3


def test_a_campaign_frozen_before_consolidation_existed_still_loads(
    tmp_path, pristine, plugin_root
):
    """load_campaign splats the frozen JSON into the dataclass.

    A field without a default would make every existing campaign.json
    unloadable - the finished MARK campaign included.
    """
    campaign = _init(tmp_path, pristine, plugin_root)
    stored_path = campaign.dir / "campaign.json"
    payload = json.loads(stored_path.read_text())
    del payload["consolidate_every"]
    stored_path.write_text(json.dumps(payload))
    assert load_campaign(campaign.dir).consolidate_every == 10


def test_the_interview_profile_is_one_arm_and_one_session(
    tmp_path, pristine, plugin_root
):
    """Only arm A carries the tools, and the fixture has no cluster left."""
    with pytest.raises(ExperimentError) as excinfo:
        _init(
            tmp_path,
            pristine,
            plugin_root,
            arms=("A", "B"),
            task_profile="interview",
        )
    assert "interview" in str(excinfo.value)
    assert not (tmp_path / "root" / "campaigns" / "pilot-test").exists()

    with pytest.raises(ExperimentError):
        _init(
            tmp_path,
            pristine,
            plugin_root,
            arms=("A",),
            task_profile="interview",
            session_mode="per-cluster",
        )
    assert not (tmp_path / "root" / "campaigns" / "pilot-test").exists()


def test_a_template_that_leaves_a_slot_unfilled_freezes_nothing(
    tmp_path, pristine, plugin_root
):
    """A campaign is frozen once, so a bad proposal must not half-build one.

    Both refusals are the renderer's, so both carry its error: the campaign
    hands the proposal to :mod:`ai_rfc.driver.render` and lets it judge.
    """
    with pytest.raises(DriverError):
        _init(
            tmp_path,
            pristine,
            plugin_root,
            loop_template="{{nonesuch}}\n",
        )
    assert not (tmp_path / "root" / "campaigns" / "pilot-test").exists()
    with pytest.raises(DriverError):
        _init(tmp_path, pristine, plugin_root, task_profile="nope")
    assert not (tmp_path / "root" / "campaigns" / "pilot-test").exists()


def test_the_profile_dir_override_is_what_the_campaign_records(
    tmp_path, pristine, plugin_root
):
    elsewhere = tmp_path / "shared-profile"
    campaign = _init(tmp_path, pristine, plugin_root, profile_dir=elsewhere)
    assert campaign.profile_dir == elsewhere
    assert load_campaign(campaign.dir).profile_dir == elsewhere
    assert (
        _init(
            tmp_path,
            pristine,
            plugin_root,
            campaign_id="default-profile",
        ).profile_dir
        == tmp_path / "root" / "profile"
    )


def test_verify_toolchain_false_leaves_the_toolchain_unverified(
    tmp_path, pristine, plugin_root, monkeypatch
):
    from ai_rfc import toolchain as toolchain_module

    verified: list[Path] = []

    def _verify(record, runner=None):
        verified.append(record)
        return True, ()

    monkeypatch.setattr(toolchain_module, "verify", _verify)
    campaign = _init(tmp_path, pristine, plugin_root, verify_toolchain=False)
    assert verified == []
    assert campaign.toolchain == str(tmp_path / "toolchain.json")
    _init(tmp_path, pristine, plugin_root, campaign_id="verified-campaign")
    assert verified == [tmp_path / "toolchain.json"]


def test_init_renders_every_arm_from_a_proposed_loop_template(
    tmp_path, pristine, plugin_root
):
    """The optimizer's path: the frozen arm prompts say what the proposal says."""
    template = "{{preamble}}\n\n{{guidance}}\n"
    campaign = _init(tmp_path, pristine, plugin_root, loop_template=template)
    for arm in "ABC":
        assert (campaign.prompts_dir / f"arm-{arm}.md").read_text() == arm_prompt(
            arm, plugin_root, template=template
        )
    frozen = (campaign.prompts_dir / "arm-A.md").read_text()
    assert frozen.startswith(SLOT_TABLES["A"]["preamble"])
    assert "## Preconditions" not in frozen
    assert (campaign.prompts_dir / "loop.tmpl.md").read_text() == template


def test_a_campaign_freezes_a_consolidation_prompt_per_arm(
    tmp_path, pristine, plugin_root
):
    campaign = _init(tmp_path, pristine, plugin_root)
    for arm in campaign.arms:
        frozen = campaign.prompts_dir / f"consolidation-{arm}.md"
        assert frozen.is_file()
        digest = hashlib.sha256(frozen.read_text().encode()).hexdigest()
        assert campaign.prompt_sha256[f"consolidation-{arm}.md"] == digest


def test_a_campaign_freezes_the_consolidation_task_template(
    tmp_path, pristine, plugin_root
):
    campaign = _init(tmp_path, pristine, plugin_root)
    frozen = campaign.consolidation_task_template
    assert frozen.is_file()
    packaged = task_template_path("consolidation")
    assert frozen.read_bytes() == packaged.read_bytes()
    assert (
        campaign.prompt_sha256[frozen.name]
        == hashlib.sha256(packaged.read_bytes()).hexdigest()
    )
