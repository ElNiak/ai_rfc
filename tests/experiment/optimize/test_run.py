"""The optimizer wiring, and one whole optimization against the fake claude.

The spec reader and the failures it reports run anywhere. Everything that
touches the backend is skipped where ``gepa`` is not importable, which is the
interpreter the rest of the harness runs on: it installs only under the
``optimize`` extra, on 3.11. Run those with that environment's interpreter::

    SSLKEYLOGFILE= .superpowers/venv-optimize/bin/python \\
        -m pytest tests/experiment/optimize -q -p no:cacheprovider

The Stage 1 case is the point of the module: a real optimization, with real
campaigns driven by the fake agent, and a reflection LM that echoes the seed
back so that not one model call is paid for.
"""

import dataclasses
import importlib.util
import json
import sys
from types import SimpleNamespace

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.metrics import window_clusters
from ai_rfc.experiment.optimize.codec import decode, encode, seed_from_plugin
from ai_rfc.experiment.optimize.evaluator import (
    Evaluator,
    EvaluatorAbort,
    EvaluatorSettings,
    InterviewExample,
    LoopExample,
)
from ai_rfc.experiment.optimize.fixtures import build_interview_pristine, sidecar_path
from ai_rfc.experiment.optimize.judge import RUBRIC
from ai_rfc.experiment.optimize.run import (
    BACKGROUND,
    OBJECTIVE,
    RunSettings,
    SeedEchoLM,
    gepa_config,
    load_examples,
    run,
)

from ..conftest import FAKE_CLAUDE, interview_good_steps
from .test_evaluator import GRADED_STEPS, _clean_build, _interview_steps, _judge

requires_gepa = pytest.mark.skipif(
    importlib.util.find_spec("gepa") is None,
    reason="gepa is installed by the optimize extra, which needs Python 3.11",
)


@pytest.fixture
def interview_fixture(tmp_path, panther_repo, template_repo):
    template, commit = template_repo
    return build_interview_pristine(
        tmp_path / "interview",
        panther_repo=panther_repo,
        template=template,
        template_commit=commit,
    )


@pytest.fixture
def loop_example(pristine):
    (cluster,) = window_clusters(pristine)
    return LoopExample(id="loop-1", pristine_dir=pristine, cluster_id=cluster["id"])


@pytest.fixture
def evaluator_settings(tmp_path, panther_repo, plugin_root, toolchain_record):
    """Evaluator settings whose root the caller points at its optimization."""
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    return EvaluatorSettings(
        root=tmp_path / "unused",
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


def _settings(**overrides):
    """Run settings whose every field the caller may replace."""
    defaults = dict(
        name="opt",
        stage="fake",
        examples=(),
        max_evals=40,
        max_token_cost=None,
        reflection_lm="anthropic/claude-sonnet-4-6",
    )
    return RunSettings(**{**defaults, **overrides})


def test_a_spec_round_trips_both_kinds_of_example(interview_fixture, pristine):
    spec = {
        "examples": [
            {
                "kind": "loop",
                "id": "loop-1",
                "pristine_dir": str(pristine),
                "cluster_id": "c0002-pr-abcdef",
                "budget_usd": 6.0,
            },
            {
                "kind": "interview",
                "id": "int-1",
                "pristine_dir": str(interview_fixture.pristine_dir),
            },
        ]
    }

    loop, interview = load_examples(spec)

    assert loop == LoopExample(
        id="loop-1",
        pristine_dir=pristine,
        cluster_id="c0002-pr-abcdef",
        budget_usd=6.0,
    )
    # The fixture is rebuilt from the sidecar, not carried in the spec, so
    # what the planted answers were survives the optimization's own restart.
    assert interview == InterviewExample(id="int-1", fixture=interview_fixture)
    assert interview.budget_usd == 2.0


def test_a_spec_with_no_examples_is_refused():
    with pytest.raises(ExperimentError, match="examples"):
        load_examples({"examples": []})


def test_an_entry_of_an_unknown_kind_names_the_kind():
    with pytest.raises(ExperimentError, match="unknown kind 'sweep'"):
        load_examples({"examples": [{"kind": "sweep", "id": "x"}]})


def test_an_entry_missing_a_field_names_the_field_and_its_position(pristine):
    spec = {
        "examples": [
            {
                "kind": "loop",
                "id": "a",
                "pristine_dir": str(pristine),
                "cluster_id": "c",
            },
            {"kind": "loop", "id": "b", "pristine_dir": str(pristine)},
        ]
    }

    with pytest.raises(ExperimentError, match="example 1: missing 'cluster_id'"):
        load_examples(spec)


def test_an_interview_entry_without_its_sidecar_is_refused(interview_fixture):
    sidecar_path(interview_fixture.pristine_dir).unlink()
    spec = {
        "examples": [
            {
                "kind": "interview",
                "id": "int-1",
                "pristine_dir": str(interview_fixture.pristine_dir),
            }
        ]
    }

    with pytest.raises(ExperimentError, match=r"interview\.json"):
        load_examples(spec)


def test_an_entry_whose_budget_is_not_a_number_names_the_entry(pristine):
    spec = {
        "examples": [
            {
                "kind": "loop",
                "id": "a",
                "pristine_dir": str(pristine),
                "cluster_id": "c",
                "budget_usd": "as much as it takes",
            }
        ]
    }

    with pytest.raises(ExperimentError, match="example 0:"):
        load_examples(spec)


def test_the_task_description_keeps_the_rubric_and_the_answers_out_of_itself(
    interview_fixture,
):
    """The proposer reads these two strings; neither may carry the answers.

    ``BACKGROUND`` describes the interview task so that a rewrite can teach a
    session how to earn sign-off. Naming which claim the author confirmed, or
    quoting a line of the planted transcript, would instead teach it the one
    answer this fixture has. Handing over the judge's rubric is the same
    mistake at the loop task: the proposer would write to the grader's own
    wording rather than to what makes a claim well evidenced.
    """
    described = OBJECTIVE + BACKGROUND

    for quote in interview_fixture.quotes.values():
        assert quote not in described
    for claim_id in (
        interview_fixture.exact_claim,
        interview_fixture.paraphrase_claim,
        interview_fixture.correction_claim,
    ):
        assert claim_id not in described
    for line in RUBRIC.splitlines():
        assert len(line.strip()) < 20 or line.strip() not in described


@requires_gepa
def test_the_config_is_built_from_keys_the_backend_accepts(tmp_path):
    """An unknown key raises where the engine is constructed, not at launch."""
    from gepa.gepa_launcher import GEPAConfig

    settings = _settings(
        name="run-name",
        root=tmp_path / "experiments",
        examples=(
            LoopExample(id="a", pristine_dir=tmp_path, cluster_id="c1"),
            LoopExample(id="b", pristine_dir=tmp_path, cluster_id="c2"),
        ),
        max_evals=40,
        max_token_cost=2.5,
        reflection_lm_kwargs={"reasoning_effort": "high"},
        seed=7,
        stop_at_score=0.9,
    )

    config = gepa_config(settings, tmp_path / "gepa", tmp_path / "evals")

    assert config.engine == "gepa"
    assert config.name == "run-name"
    assert config.max_evals == 40
    assert config.max_token_cost == 2.5
    assert config.max_concurrency == 1
    assert config.stop_at_score == 0.9
    assert config.run_dir == str(tmp_path / "gepa")
    assert config.output_dir == str(tmp_path / "evals")

    parsed = GEPAConfig(**config.engine_config)

    assert parsed.reflection.reflection_lm == "anthropic/claude-sonnet-4-6"
    assert parsed.reflection.reflection_lm_kwargs == {"reasoning_effort": "high"}
    assert parsed.reflection.reflection_minibatch_size == 2
    assert parsed.engine.max_workers == 1
    assert parsed.engine.seed == 7
    assert parsed.engine.frontier_type == "hybrid"
    assert parsed.engine.raise_on_exception is True


@requires_gepa
def test_the_echoed_seed_parses_back_out_of_the_reply(plugin_root):
    """Put through the installed proposer's own parser, not an imitation."""
    from gepa.strategies.instruction_proposal import InstructionProposalSignature

    seed = seed_from_plugin(plugin_root)
    candidate = encode(seed)

    reply = SeedEchoLM(candidate)("a reflection prompt this LM never reads")
    parsed = InstructionProposalSignature.output_extractor(reply.strip())

    assert parsed["new_instruction"] == candidate.strip()
    assert decode(parsed["new_instruction"], seed=seed) == seed


@requires_gepa
def test_a_candidate_carrying_its_own_fences_survives_the_parser(plugin_root):
    """Only the outermost pair is cut, so a fenced example in a body lives.

    The packaged seed has no fenced block, but a proposal that adds one to a
    skill is the ordinary case, and losing the text inside it would silently
    truncate every later candidate.
    """
    from gepa.strategies.instruction_proposal import InstructionProposalSignature

    seed = seed_from_plugin(plugin_root)
    fenced = dataclasses.replace(
        seed, rfc_style=seed.rfc_style + "\nFor example:\n\n```\nMUST NOT\n```\n"
    )
    candidate = encode(fenced)

    reply = SeedEchoLM(candidate)("ignored")
    parsed = InstructionProposalSignature.output_extractor(reply.strip())

    assert parsed["new_instruction"] == candidate.strip()
    assert decode(parsed["new_instruction"], seed=seed) == fenced


@requires_gepa
@pytest.mark.slow
def test_stage_one_optimizes_end_to_end_without_a_model_call(
    tmp_path, evaluator_settings, loop_example, interview_fixture, write_scenario
):
    """One whole optimization: two examples, real campaigns, no paid call.

    Every evaluation freezes its own campaign on the proposed loop template
    and drives the fake agent through it, so what is exercised is the wiring
    between the backend and the harness rather than a stand-in for it. The
    seed cannot be beaten — the fake ignores the prompt entirely, so every
    proposal scores what the seed scored and none is an improvement.
    """
    interview_example = InterviewExample(id="int-1", fixture=interview_fixture)
    steps = _interview_steps(interview_good_steps, interview_fixture)

    def plant(campaign, example):
        write_scenario(
            campaign.profile_dir,
            "A1",
            {
                "arm": "A",
                "steps": steps if example.kind == "interview" else GRADED_STEPS,
            },
        )

    candidate = encode(evaluator_settings.seed)
    settings = _settings(
        name="stage-one",
        root=tmp_path / "experiments",
        examples=(loop_example, interview_example),
        # Enough for one whole round: the seed on the selection set, the
        # current candidate on a minibatch, and the proposal on that same
        # minibatch. At four the budget runs out before the proposal is ever
        # scored, and nothing between the reflection and the codec is tried.
        max_evals=6,
        reflection_lm=SeedEchoLM(candidate),
    )
    evaluator = Evaluator(
        dataclasses.replace(
            evaluator_settings, root=settings.directory, pre_launch=plant
        )
    )

    result = run(settings, evaluator)

    assert evaluator.evaluations >= len(settings.examples)
    assert result.best_candidate == candidate
    campaigns = sorted((settings.directory / "campaigns").iterdir())
    assert len(campaigns) >= evaluator.evaluations
    for campaign in campaigns:
        frozen = (campaign / "prompts" / "loop.tmpl.md").read_text()
        assert frozen == decode(candidate, seed=evaluator.settings.seed).loop
    # Each id carries its candidate's digest, and the echoed proposal comes
    # back from the parser without the seed's trailing newline, so two
    # distinct digests is the evidence that a proposal was scored at all and
    # not merely parsed.
    assert len({campaign.name.rsplit("-", 1)[1] for campaign in campaigns}) == 2

    record = json.loads((settings.directory / "result.json").read_text())
    assert record["best_candidate"] == candidate
    assert record["stage"] == "fake"
    assert record["evaluations"] == evaluator.evaluations
    assert set(record["best_example_scores"]) == {"loop-1", "int-1"}
    assert "SeedEchoLM" in record["settings"]["reflection_lm"]
    assert record["ai_rfc"]


@requires_gepa
@pytest.mark.slow
def test_an_evaluator_abort_propagates_out_of_run(tmp_path, plugin_root, loop_example):
    """A harness abort must stop the run rather than burn out the budget.

    This is what ``raise_on_exception=True`` in :func:`~.run.gepa_config`
    buys: with it False, gepa would instead catch ``EvaluatorAbort`` at the
    evaluator wrapper and score the call 0.0, spending the rest of the eval
    budget on a run that should have stopped.
    """

    class AbortsOnFirstCall:
        """Stands in for :class:`Evaluator`; aborts before scoring anything."""

        def __init__(self, seed):
            self.settings = SimpleNamespace(seed=seed)

        def __call__(self, candidate, example):
            raise EvaluatorAbort("the harness cannot be reached")

    settings = _settings(
        name="abort",
        root=tmp_path / "experiments",
        examples=(loop_example,),
    )

    with pytest.raises(EvaluatorAbort):
        run(settings, AbortsOnFirstCall(seed_from_plugin(plugin_root)))
