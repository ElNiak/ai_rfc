import dataclasses
import json
import shlex
import time
from pathlib import Path

import pytest

from ai_rfc.driver import DriverError
from ai_rfc.driver.record import INTERRUPTED
from ai_rfc.driver.session import EVENTS_FILE, GUARD_FILE, prepare_argv, session_env
from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.runner import (
    RESULT_FILE,
    launch,
    load_status,
    run_ref,
    session_spec,
)
from ai_rfc.experiment.workspace import copy_workspace

from .conftest import COMPLETE_STEPS, FAKE_CLAUDE

# Re-exported so pytest can resolve it as a fixture here: pytest looks up a
# fixture's own dependencies in the requesting module's namespace, not the
# defining one. Its dependency ``wide_pristine`` needs no re-export — it lives
# in this directory's conftest, which every module here sees.
from .test_per_cluster import per_cluster_campaign  # noqa: F401


def _ready(campaign, run_id):
    ref = run_ref(campaign, run_id)
    ref.run_dir.mkdir(parents=True)
    copy_workspace(campaign.pristine_dir, ref.workspace)
    return ref


def test_launch_streams_events_and_records_status(campaign, write_scenario):
    ref = _ready(campaign, "A1")
    write_scenario(
        campaign.profile_dir, "A1", {"arm": "A", "cost": 1.25, "steps": COMPLETE_STEPS}
    )
    status = launch(campaign, ref)
    assert status.complete and status.exit_code == 0 and not status.timed_out
    assert status.claude_version == "fake-claude 0.0.0"
    events = (ref.run_dir / EVENTS_FILE).read_text().splitlines()
    assert json.loads(events[0])["subtype"] == "init"
    assert json.loads((ref.run_dir / RESULT_FILE).read_text())["total_cost_usd"] == 1.25
    assert load_status(ref.run_dir) == status
    argv = json.loads((ref.run_dir / "argv.json").read_text())
    assert argv[:2] == [str(FAKE_CLAUDE), "-p"]
    assert "--append-system-prompt-file" in argv
    env = json.loads((ref.run_dir / "env.json").read_text())
    assert env["PATH"].startswith(str(campaign.bin_dir))
    assert env["AI_RFC_WORKSPACE"] == str(ref.workspace)
    assert env["AI_RFC_CONFIG"] == str(ref.workspace / "recon.yaml")
    assert env["AI_RFC_TOOLCHAIN"] == campaign.toolchain
    assert set(env) == {
        "CLAUDE_CONFIG_DIR",
        "AI_RFC_WORKSPACE",
        "AI_RFC_CONFIG",
        "AI_RFC_TOOLCHAIN",
        "PATH",
        "HOME",
        "USER",
        "LANG",
    }
    prompt = (ref.run_dir / "prompt.md").read_text()
    assert "ai_rfc_cluster_next" in prompt and "ordinals 2 through 2" in prompt
    calls = json.loads((campaign.profile_dir / "fake-calls" / "A1.json").read_text())
    assert calls["cwd"] == str(ref.workspace)
    assert any(
        p.name.startswith("c0002-") and not (p / "harness.json").exists()
        for p in (ref.workspace / "checkpoints").iterdir()
    )


def test_per_cluster_prompt_record_names_the_template_not_a_whole_window_task(
    per_cluster_campaign, write_scenario  # noqa: F811
):
    write_scenario(
        per_cluster_campaign.profile_dir,
        "A1",
        {"arm": "A", "cost": 1.0, "steps": COMPLETE_STEPS},
    )
    ref = run_ref(per_cluster_campaign, "A1")
    copy_workspace(per_cluster_campaign.pristine_dir, ref.workspace)
    launch(per_cluster_campaign, ref, report=lambda _: None)
    prompt = (ref.run_dir / "prompt.md").read_text()
    assert "rendered per session from prompts/task.tmpl.md" in prompt
    assert "ordinals 1 through 2" not in prompt and "$low" in prompt


def test_launch_refuses_a_campaign_whose_task_template_was_never_frozen(
    per_cluster_campaign,  # noqa: F811
):
    per_cluster_campaign.task_template.unlink()
    ref = run_ref(per_cluster_campaign, "A1")
    copy_workspace(per_cluster_campaign.pristine_dir, ref.workspace)
    with pytest.raises(ExperimentError) as excinfo:
        launch(per_cluster_campaign, ref, report=lambda _: None)
    assert str(per_cluster_campaign.task_template) in str(excinfo.value)


def test_launch_refuses_a_campaign_with_no_toolchain(campaign):
    """A campaign frozen before the build gate existed (`toolchain=None`, kept
    as `Campaign`'s default so old campaigns still load for `audit`) must not
    be launched: `session_env` would silently omit `AI_RFC_TOOLCHAIN` and the
    session would run with no build gate at all."""
    no_toolchain = dataclasses.replace(campaign, toolchain=None)
    ref = _ready(no_toolchain, "A1")
    with pytest.raises(ExperimentError) as excinfo:
        launch(no_toolchain, ref, report=lambda _: None)
    assert "toolchain" in str(excinfo.value)
    assert not (ref.run_dir / "prompt.md").exists()
    assert load_status(ref.run_dir) is None


def test_arm_a_mounts_mcp_and_has_no_bash(campaign):
    ref_a = _ready(campaign, "A1")
    argv = prepare_argv(session_spec(campaign, ref_a), ref_a.run_dir)
    assert "--mcp-config" in argv and (ref_a.run_dir / "ai_rfc.json").exists()
    assert "Bash" not in argv[argv.index("--tools") + 1].split(",")
    ref_b = _ready(campaign, "B1")
    argv_b = prepare_argv(session_spec(campaign, ref_b), ref_b.run_dir)
    assert "--mcp-config" not in argv_b
    assert "Bash(ai-rfc *)" in argv_b[argv_b.index("--allowedTools") + 1]
    env_b = session_env(session_spec(campaign, ref_b))
    assert env_b["CLAUDE_CONFIG_DIR"] == str(campaign.profile_dir)
    assert env_b["AI_RFC_TOOLCHAIN"] == campaign.toolchain
    no_toolchain = dataclasses.replace(campaign, toolchain=None)
    assert "AI_RFC_TOOLCHAIN" not in session_env(session_spec(no_toolchain, ref_b))


def test_every_run_mounts_its_arms_guard(campaign):
    """Without this the arms are capability-identical: --allowedTools does not
    confine a built-in tool (spike S0, CLI 2.1.247)."""
    expected = {
        "A1": (),
        "B1": ("ai-rfc ",),
        "C1": (
            "python -m ai_rfc",
            "git ",
            "sqlite3 ",
        ),
    }
    for run_id, families in expected.items():
        ref = _ready(campaign, run_id)
        argv = prepare_argv(session_spec(campaign, ref), ref.run_dir)
        settings = Path(argv[argv.index("--settings") + 1])
        assert settings == ref.run_dir / GUARD_FILE
        hook = json.loads(settings.read_text())["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Bash"
        # The hook command is a shell string, so assert on how a shell reads it:
        # a family carrying a space must survive as one argument.
        parsed = shlex.split(hook["hooks"][0]["command"])
        assert parsed[0] == campaign.python
        assert parsed[1].endswith("/guard.py") and Path(parsed[1]).exists()
        assert tuple(parsed[2:]) == families


def test_launch_times_out_and_kills_the_process_group(campaign, write_scenario):
    ref = _ready(campaign, "C1")
    write_scenario(campaign.profile_dir, "C1", {"arm": "C", "sleep": 30, "steps": []})
    short = dataclasses.replace(campaign, timeout_s=1)
    started = time.monotonic()
    status = launch(short, ref)
    assert status.timed_out and status.exit_code is None and not status.complete
    assert time.monotonic() - started < 40
    assert (ref.run_dir / RESULT_FILE).read_text() == "null\n"


def test_launch_records_a_nonzero_exit(campaign, write_scenario):
    ref = _ready(campaign, "B1")
    write_scenario(
        campaign.profile_dir, "B1", {"arm": "B", "exit_code": 3, "steps": []}
    )
    status = launch(campaign, ref)
    assert status.complete and status.exit_code == 3


def test_launch_refuses_to_relaunch(campaign, write_scenario):
    ref = _ready(campaign, "B1")
    write_scenario(campaign.profile_dir, "B1", {"arm": "B", "steps": []})
    launch(campaign, ref)
    with pytest.raises(ExperimentError):
        launch(campaign, ref)
    assert load_status(run_ref(campaign, "C1").run_dir) is None


def test_a_run_can_be_pointed_at_a_different_frozen_prompt(campaign):
    ref = _ready(campaign, "A1")
    other = campaign.prompts_dir / f"consolidation-{ref.arm}.md"
    argv = prepare_argv(session_spec(campaign, ref, prompt_file=other), ref.run_dir)
    assert str(other) in argv
    assert f"arm-{ref.arm}.md" not in " ".join(argv)


def test_the_default_prompt_is_still_the_arm_prompt(campaign):
    ref = _ready(campaign, "A1")
    argv = prepare_argv(session_spec(campaign, ref), ref.run_dir)
    assert f"arm-{ref.arm}.md" in " ".join(argv)


def test_launch_refuses_a_run_whose_transcript_is_already_held(
    campaign, write_scenario
):
    """A campaign run directory is claimed by its transcript, at the first spawn.

    The campaign path mints deterministic ids (`A1`), so two launches of one
    campaign reach for the same directory by construction -- and until the
    first session's transcript became an exclusive create, the second launch
    truncated the first's `events.jsonl` and then spent a fresh budget beside
    it. The state built here is what a live first launch leaves: a transcript
    with lines in it and no `status.json` yet, which the relaunch guard above
    does not see.

    Three things are asserted rather than one, because three separate
    mechanisms have to hold: the refusal names the transcript it is refusing
    over and how to release it, the held lines are still there, and the fake
    `claude` was never called -- nothing was spent.
    """
    ref = _ready(campaign, "A1")
    write_scenario(
        campaign.profile_dir, "A1", {"arm": "A", "cost": 1.25, "steps": COMPLETE_STEPS}
    )
    transcript = ref.run_dir / EVENTS_FILE
    held = '{"type": "system", "subtype": "init"}\n{"type": "result"}\n'
    transcript.write_text(held)

    with pytest.raises(DriverError) as raised:
        launch(campaign, ref, report=lambda _: None)

    message = str(raised.value)
    assert str(transcript) in message, message
    assert f"mv {ref.run_dir} {ref.run_dir}{INTERRUPTED}" in message, message
    assert transcript.read_text() == held
    assert not (campaign.profile_dir / "fake-calls" / f"{ref.run_id}.json").exists()
    assert load_status(ref.run_dir) is None


def test_a_refused_relaunch_leaves_the_holders_sidecars_exactly_as_they_were(
    campaign, write_scenario
):
    """The claim has to come before ``prepare_argv``, not after it.

    ``prepare_argv`` **writes** ``guard.json`` (``driver/session.py:305``), and
    the refusal used to happen four files later, at the spawn. So a second
    launch of one run id rewrote the holder's ``guard.json``, ``argv.json``,
    ``env.json`` and ``prompt.md`` before declining — and a ``guard.json`` the
    holder's own agent had tampered with, which is the tampering
    ``guard_sha256`` exists to catch, was restored to pristine bytes on the
    way past. The audit then passed a run it should have failed.

    Bytes *and* mtime, because a rewrite with identical content is still a
    rewrite past a digest that was taken before it.
    """
    ref = _ready(campaign, "A1")
    write_scenario(
        campaign.profile_dir, "A1", {"arm": "A", "cost": 1.25, "steps": COMPLETE_STEPS}
    )
    # What a live first launch leaves: sidecars written, transcript held, no
    # status record yet.
    prepare_argv(session_spec(campaign, ref), ref.run_dir)
    guard = ref.run_dir / GUARD_FILE
    tampered = '{"hooks": {}}\n'
    guard.write_text(tampered)
    before = guard.stat().st_mtime_ns
    (ref.run_dir / EVENTS_FILE).write_text('{"type": "result"}\n')

    with pytest.raises(DriverError):
        launch(campaign, ref, report=lambda _: None)

    assert guard.read_text() == tampered
    assert guard.stat().st_mtime_ns == before
    assert not (campaign.profile_dir / "fake-calls" / f"{ref.run_id}.json").exists()


def test_a_zero_byte_transcript_covers_nothing_and_reports_no_damage(tmp_path):
    """The claim leaves an empty ``events.jsonl`` between claim and spawn.

    A reader that took an empty file for a damaged one would report ``cannot
    adjudicate`` for every run in that window, so the detector's answer for
    this exact state is pinned here rather than assumed.
    """
    from ai_rfc.driver.coverage import read_transcript

    empty = tmp_path / "events.jsonl"
    empty.write_text("")

    assert read_transcript(empty) == ([], None)
