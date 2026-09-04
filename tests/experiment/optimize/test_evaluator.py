"""The evaluator end to end, every campaign driven by the fake claude.

Almost nothing here stubs the harness: each case freezes a real campaign,
launches the fake through the real runner, audits and analyzes the transcript
it wrote, and scores the workspace it left behind. Only the two things that
would cost money or half an hour are stubbed — the claim judge and, where the
build is not what is under test, the draft build.

The exception is the harness-fault predicate. Two of the three conditions it
decides on cannot be provoked through the fake, which always mounts its arm's
server and always leaves an auditable transcript, so those are put to the
predicate directly with synthetic run records, and one of them is also driven
end to end by taking the audit away from the first attempt only.
"""

import dataclasses
import os
import sys
from pathlib import Path

import pytest

from ai_rfc.draft.build import BuildReport
from ai_rfc.experiment.metrics import window_clusters
from ai_rfc.experiment.optimize.codec import decode, encode, seed_from_plugin
from ai_rfc.experiment.optimize.evaluator import (
    Evaluator,
    EvaluatorAbort,
    EvaluatorSettings,
    InterviewExample,
    LoopExample,
    _harness_fault,
    campaign_id_for,
    draft_build_report,
)
from ai_rfc.experiment.optimize.fixtures import build_interview_pristine
from ai_rfc.experiment.optimize.scoring import (
    ZERO_INCOMPLETE,
    ZERO_REGISTER_EDIT,
    ZERO_SIGNOFF_TRAP,
    Judgement,
)
from ai_rfc.schema import load
from ai_rfc.timeline.store import read_clusters

from ..conftest import (
    COMPLETE_STEPS,
    FAKE_CLAUDE,
    interview_good_steps,
    interview_trap_steps,
)

CLAIM_ONLY_STEPS = [
    {"kind": "claim", "id": "t:3.1", "section": "3.1"},
    {"kind": "checkpoint", "ordinal": 2},
]

#: A complete run whose claim anchors a file the graded cluster touched. The
#: shared steps anchor ``a.txt``, which belongs to the epoch cluster; the
#: in-window cluster these examples grade is the PR, whose file set is
#: ``b.txt`` alone. The scorer now requires the anchored path to be one the
#: cluster changed, so anchoring anywhere else earns no claims at all.
GRADED_STEPS = [
    {**step, "locator": "b.txt"} if step["kind"] == "claim" else step
    for step in COMPLETE_STEPS
]


def _judge(hunks):
    """Rate every claim a perfect fit, so the value tracks the other terms."""
    return [
        Judgement(claim_id=hunk.claim_id, score=1.0, rationale="stub") for hunk in hunks
    ]


def _clean_build(campaign, workspace):
    """A build that compiled with no nits, without running a toolchain."""
    return BuildReport(
        ref="HEAD",
        commit="0" * 40,
        draft="draft-test-fixture.md",
        source_sha256="0" * 64,
        date="2026-01-01",
        targets=("txt",),
        exit_code=0,
        argv=(),
        template={},
        refcache="",
        stages=(),
        diagnostics=(),
        broken_references=(),
        idnits={},
        outputs={},
    )


def _with(settings, **overrides):
    return dataclasses.replace(settings, **overrides)


def _analysis(**overrides):
    """A run record that clears every harness-fault condition."""
    return {
        "audit": {"integrity": True},
        "surface": {"intact": True, "mcp_servers": ["ai_rfc"]},
        **overrides,
    }


def _scenario(write_scenario, steps, **payload):
    """A ``pre_launch`` that plants one scenario for the campaign's only run."""

    def plant(campaign, example):
        write_scenario(
            campaign.profile_dir, "A1", {"arm": "A", "steps": steps, **payload}
        )

    return plant


def _interview_steps(builder, fixture):
    """Interview steps whose questions quote each claim's wording verbatim.

    The shared builders ask a generic question, which earns every term but
    ``verbatim``; the claim texts come from the manifest the fixture planted
    rather than from a second copy of them in this file.
    """
    claim_ids = [
        fixture.exact_claim,
        fixture.paraphrase_claim,
        fixture.correction_claim,
    ]
    manifest = load(fixture.pristine_dir / "manifest.yaml")
    texts = {claim.id: claim.text for claim in manifest.claims}
    steps = builder(claim_ids, fixture.quotes)
    for step in steps:
        if step["kind"] == "question_draft":
            step["text"] = f"You wrote: {texts[step['claim_ids'][0]]} Still right?"
    return steps


@pytest.fixture
def settings(tmp_path, panther_repo, plugin_root, toolchain_record):
    """Evaluator settings whose campaigns all launch the fake claude."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    return EvaluatorSettings(
        root=tmp_path / "root",
        profile_dir=profile_dir,
        python=sys.executable,
        claude_bin=str(FAKE_CLAUDE),
        model="fake-model",
        effort="high",
        timeout_s=900,
        panther_repo=panther_repo,
        toolchain=toolchain_record,
        source_plugin_root=plugin_root,
        seed=seed_from_plugin(plugin_root),
        judge=_judge,
        build=_clean_build,
        log=lambda _: None,
    )


@pytest.fixture
def candidate(settings):
    """The seed bundle, encoded exactly as a backend would propose it."""
    return encode(settings.seed)


@pytest.fixture
def loop_example(pristine):
    """The fixture target's single in-window cluster."""
    (cluster,) = window_clusters(pristine)
    return LoopExample(id="loop-1", pristine_dir=pristine, cluster_id=cluster["id"])


@pytest.fixture
def interview_fixture(tmp_path, panther_repo, template_repo):
    template, commit = template_repo
    return build_interview_pristine(
        tmp_path / "interview",
        panther_repo=panther_repo,
        template=template,
        template_commit=commit,
    )


def test_a_run_without_an_audit_record_is_a_harness_fault(pristine, loop_example):
    """Unmeasured, not unsuccessful.

    Left to score_loop this is a plain zero under its own harness reason,
    scored once and fed to the backend as a verdict on the candidate. Caught
    here it is retried instead, and eventually stops the optimization.
    """
    fault = _harness_fault(_analysis(audit=None), loop_example, pristine)

    assert fault is not None and "audit" in fault


def test_a_run_that_never_mounted_its_tool_surface_is_a_harness_fault(
    pristine, loop_example
):
    analysis = _analysis(surface={"intact": False, "mcp_servers": []})

    fault = _harness_fault(analysis, loop_example, pristine)

    assert fault is not None and "tool surface" in fault


def test_a_window_that_is_not_the_scored_cluster_is_a_harness_fault(
    pristine, loop_example
):
    """The out-of-window cluster is real, so this is not an unknown-id check."""
    outside = next(
        row["id"]
        for row in read_clusters(pristine / "timeline")
        if row["id"] != loop_example.cluster_id
    )
    elsewhere = dataclasses.replace(loop_example, cluster_id=outside)

    fault = _harness_fault(_analysis(), elsewhere, pristine)

    assert fault is not None and outside in fault
    assert _harness_fault(_analysis(), loop_example, pristine) is None


def test_an_audit_that_went_missing_is_retried_rather_than_scored(
    settings, candidate, loop_example, write_scenario, monkeypatch
):
    """The end-to-end half of the audit-missing condition.

    The fake always leaves an auditable transcript, so the record is taken
    away from the first attempt's analysis only. What is asserted is the
    difference the predicate makes: a second campaign, and a graded score
    rather than the zero score_loop would have returned.
    """
    from ai_rfc.experiment.optimize import evaluator as module

    analyze = module.analyze_run
    seen = []

    def losing_the_first_audit(campaign, run_id):
        seen.append(campaign.id)
        analysis = analyze(campaign, run_id)
        return {**analysis, "audit": None} if len(seen) == 1 else analysis

    monkeypatch.setattr(module, "analyze_run", losing_the_first_audit)
    settings = _with(settings, pre_launch=_scenario(write_scenario, GRADED_STEPS))

    value, info = Evaluator(settings)(candidate, loop_example)

    assert value > 0.0 and info["reason"] is None
    assert len(seen) == 2 and seen[0] != seen[1]
    assert info["campaign_id"] == seen[1]


def test_a_complete_loop_run_is_graded_against_the_candidate_it_froze(
    settings, candidate, loop_example, write_scenario
):
    settings = _with(settings, pre_launch=_scenario(write_scenario, GRADED_STEPS))
    evaluator = Evaluator(settings)

    value, info = evaluator(candidate, loop_example)

    assert value > 0.0
    assert info["reason"] is None
    assert info["anchored"] == ["t:3.1"]
    assert info["judgements"] == [
        {"claim_id": "t:3.1", "score": 1.0, "rationale": "stub"}
    ]
    assert info["example_id"] == "loop-1" and info["kind"] == "loop"
    # Only reachable because the evaluator tells the scorer where the run
    # wrote; analyze_run records the outcomes and not the directory.
    assert info["final_summary"] == "done"
    campaign_dir = Path(info["campaign_dir"])
    assert campaign_dir.parent == settings.root / "campaigns"
    assert campaign_dir.name == campaign_id_for(0, loop_example, info["candidate_sha"])
    frozen = (campaign_dir / "prompts" / "loop.tmpl.md").read_text()
    assert frozen == decode(candidate, seed=settings.seed).loop
    assert Path(info["run_dir"]) == campaign_dir / "runs" / "A1"
    assert evaluator.evaluations == 1


def test_a_run_that_never_tagged_a_revision_scores_zero_incomplete(
    settings, candidate, loop_example, write_scenario
):
    settings = _with(settings, pre_launch=_scenario(write_scenario, CLAIM_ONLY_STEPS))

    value, info = Evaluator(settings)(candidate, loop_example)

    assert value == 0.0
    assert info["reason"] == ZERO_INCOMPLETE
    assert info["gates_clean"] is True


def test_a_hand_edited_register_scores_zero_under_its_own_reason(
    settings, candidate, loop_example, write_scenario
):
    """The overstate step edits ``manifest.yaml``, which is the register.

    The gate it dirties is read after the register-edit precondition, so the
    named reason is the edit rather than the failed gate.
    """
    steps = GRADED_STEPS + [{"kind": "overstate", "id": "t:3.1"}]
    settings = _with(settings, pre_launch=_scenario(write_scenario, steps))

    value, info = Evaluator(settings)(candidate, loop_example)

    assert value == 0.0
    assert info["reason"] == ZERO_REGISTER_EDIT
    assert info["register_edits"] == 1


def test_a_candidate_missing_a_slot_is_rejected_before_any_campaign_exists(
    settings, candidate, loop_example
):
    broken = candidate.replace("{{cluster_next}}", "the next cluster")
    evaluator = Evaluator(settings)

    value, info = evaluator(broken, loop_example)

    assert value == 0.0
    assert info["error_type"] == "codec"
    assert info["failed_checks"] == ["loop: missing slot {{cluster_next}}"]
    assert info["kind"] == "loop" and info["example_id"] == "loop-1"
    assert not (settings.root / "campaigns").exists()
    assert evaluator.evaluations == 1


def test_the_interview_task_earns_every_term_it_is_offered(
    settings, candidate, interview_fixture, write_scenario
):
    example = InterviewExample(id="int-1", fixture=interview_fixture)
    steps = _interview_steps(interview_good_steps, interview_fixture)
    settings = _with(settings, pre_launch=_scenario(write_scenario, steps))

    value, info = Evaluator(settings)(candidate, example)

    assert info["reason"] is None, info
    assert value == 1.0
    assert info["kind"] == "interview"
    assert "final_summary" not in info


def test_signing_off_a_paraphrase_scores_zero(
    settings, candidate, interview_fixture, write_scenario
):
    example = InterviewExample(id="int-1", fixture=interview_fixture)
    steps = _interview_steps(interview_trap_steps, interview_fixture)
    settings = _with(settings, pre_launch=_scenario(write_scenario, steps))

    value, info = Evaluator(settings)(candidate, example)

    assert value == 0.0
    assert info["reason"] == ZERO_SIGNOFF_TRAP
    assert info["signed_off"] == [interview_fixture.paraphrase_claim]


def test_every_evaluation_gets_its_own_campaign_across_evaluators(
    settings, candidate, loop_example, write_scenario
):
    settings = _with(settings, pre_launch=_scenario(write_scenario, GRADED_STEPS))
    first = Evaluator(settings)

    _, one = first(candidate, loop_example)
    _, two = first(candidate, loop_example)
    _, three = Evaluator(settings)(candidate, loop_example)

    ids = [one["campaign_id"], two["campaign_id"], three["campaign_id"]]
    assert len(set(ids)) == 3
    assert ids[2] == campaign_id_for(2, loop_example, one["candidate_sha"])
    assert sorted(p.name for p in (settings.root / "campaigns").iterdir()) == sorted(
        ids
    )


def test_one_harness_fault_is_retried_and_the_retry_is_graded(
    settings, candidate, loop_example, write_scenario
):
    plant = _scenario(write_scenario, GRADED_STEPS)
    seen = []

    def flaky(campaign, example):
        seen.append(campaign.id)
        if len(seen) == 1:
            raise RuntimeError("the profile was busy")
        plant(campaign, example)

    evaluator = Evaluator(_with(settings, pre_launch=flaky))

    value, info = evaluator(candidate, loop_example)

    assert value > 0.0 and info["reason"] is None
    assert len(seen) == 2 and seen[0] != seen[1]
    assert info["campaign_id"] == seen[1]


def test_two_faults_return_a_harness_zero_and_a_second_pair_aborts(
    settings, candidate, loop_example
):
    def always_fails(campaign, example):
        raise RuntimeError("the profile was busy")

    evaluator = Evaluator(_with(settings, pre_launch=always_fails))

    value, info = evaluator(candidate, loop_example)

    assert value == 0.0
    assert info["error_type"] == "harness"
    assert "the profile was busy" in info["detail"]
    assert info["kind"] == "loop" and info["example_id"] == "loop-1"
    assert info["candidate_sha"]
    # Both attempts froze their own campaign; the faulted one stays as
    # evidence rather than being reused.
    assert len(list((settings.root / "campaigns").iterdir())) == 2

    with pytest.raises(EvaluatorAbort):
        evaluator(candidate, loop_example)


def test_a_graded_evaluation_resets_the_consecutive_fault_count(
    settings, candidate, loop_example, write_scenario
):
    plant = _scenario(write_scenario, GRADED_STEPS)
    broken = []

    def sometimes(campaign, example):
        if broken:
            raise RuntimeError("the profile was busy")
        plant(campaign, example)

    evaluator = Evaluator(_with(settings, pre_launch=sometimes))

    broken.append(True)
    assert evaluator(candidate, loop_example)[1]["error_type"] == "harness"
    broken.clear()
    assert evaluator(candidate, loop_example)[1]["reason"] is None
    broken.append(True)
    assert evaluator(candidate, loop_example)[1]["error_type"] == "harness"


def test_the_materialized_plugin_is_reused_for_the_same_candidate(
    settings, candidate, loop_example, write_scenario
):
    settings = _with(settings, pre_launch=_scenario(write_scenario, GRADED_STEPS))
    evaluator = Evaluator(settings)
    evaluator(candidate, loop_example)

    (materialized,) = list((settings.root / "plugins").iterdir())
    stamp = materialized / "candidate.sha256"
    skill = materialized / "skills" / "ai-rfc-reconstruction-loop" / "SKILL.md"
    # Backdated rather than compared across two writes a second apart: equal
    # mtimes would pass a re-materializing implementation by coincidence.
    for path in (skill, stamp):
        os.utime(path, (1_000_000_000, 1_000_000_000))

    evaluator(candidate, loop_example)

    assert int(skill.stat().st_mtime) == 1_000_000_000
    assert int(stamp.stat().st_mtime) == 1_000_000_000


def test_a_different_candidate_gets_its_own_plugin_root(
    settings, candidate, loop_example, write_scenario
):
    settings = _with(settings, pre_launch=_scenario(write_scenario, GRADED_STEPS))
    evaluator = Evaluator(settings)
    other = encode(
        dataclasses.replace(
            settings.seed,
            evidence_hygiene=settings.seed.evidence_hygiene + "\nOne more line.\n",
        )
    )

    evaluator(candidate, loop_example)
    evaluator(other, loop_example)

    assert len(list((settings.root / "plugins").iterdir())) == 2


def test_the_default_build_compiles_the_revision_the_session_tagged(
    settings, candidate, loop_example, write_scenario
):
    settings = _with(
        settings, build=None, pre_launch=_scenario(write_scenario, GRADED_STEPS)
    )

    value, info = Evaluator(settings)(candidate, loop_example)

    assert info["reason"] is None
    assert value > 0.0
    report = Path(info["run_dir"]) / "draft-build" / "build" / "build-report.json"
    assert report.exists()
    assert '"ref": "draft-test-fixture-00"' in report.read_text()


def test_the_default_build_returns_nothing_without_a_toolchain(campaign):
    without = dataclasses.replace(campaign, toolchain=None)

    assert draft_build_report(without, campaign.pristine_dir) is None
