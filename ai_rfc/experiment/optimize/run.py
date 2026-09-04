"""The search backend, wired to the evaluator and told what it is optimizing.

Everything below this module measures; this one searches. It hands GEPA the
seed encoding, the examples to measure it on, and a description of the task
written for a reflection LM that will never see the harness — then keeps what
the search found in one file beside the run's own artifacts.

``gepa`` is imported inside functions only. It pulls ``litellm``, which does
not import on the interpreter the rest of the harness runs on, so the package
stays loadable there and the optimizer runs in a 3.11 environment of its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .. import ExperimentError
from ..config import git_describe
from .codec import encode
from .evaluator import Evaluator, Example, InterviewExample, LoopExample
from .fixtures import load_interview_fixture

if TYPE_CHECKING:  # pragma: no cover - the harness interpreter has no gepa
    from gepa.optimize_anything import (  # type: ignore[import-not-found]
        GEPAResult,
        OptimizeAnythingConfig,
    )

#: Everything one optimization writes lands under ``<root>/optimize/<name>/``.
OPTIMIZE_DIR = "optimize"
#: The backend's own workspace: its state, log and candidate tree.
GEPA_DIR = "gepa"
#: The eval server's per-evaluation records.
EVALS_DIR = "evals"
#: What the search found, written by :func:`run`.
RESULT_FILE = "result.json"
#: The key :func:`load_examples` reads its entries from.
EXAMPLES_KEY = "examples"

OBJECTIVE = """Rewrite the four skill texts an agent reads while
reconstructing a specification from a repository's own history, so that a
session working one cluster of that history at a time ends with more
requirement claims that are anchored to the code the cluster actually changed,
judged by a reviewer to be implemented by it, and cited in the prose the
session writes — while never asserting more than its evidence supports, and
spending fewer turns doing it."""

BACKGROUND = """The candidate carries four sections, each introduced by its
own `<<<AI_RFC_SECTION name>>>` line, all four required and in this order.
`loop` is the per-cluster task template the harness renders for every session;
its `{{slot}}` placeholders are filled in by the harness, and every one of them
must survive a rewrite unchanged in name and in number of occurrences.
`evidence-hygiene` governs how a claim is tied to the evidence that supports it
and what may be asserted without one. `interviewing` governs how an author is
questioned and when their answer may be recorded as agreement. `rfc-style`
governs the prose the draft is written in. No section may carry a frontmatter
block, and none may shrink below a quarter or grow past twice its current
length; a proposal that breaks any of this is rejected unmeasured, with the
broken rules named.

A session is measured on one cluster at a time. Two preconditions come first
and each scores zero alone: the session must finish the cluster it was given —
claiming, checkpointing, writing prose and tagging a revision — and it must not
have edited the claim register or the interview transcripts by hand, nor run
outside the tools it was offered. Past those, the value is a weighted sum of
four terms, scaled by how much of the cluster's file set the new claims reach.
The heaviest term is how well an independent reviewer judges each newly
anchored claim to be implemented by the code that cluster changed; then whether
those claims are cited in the prose the session wrote; then how cleanly the
tagged draft compiles; and last, and lightest, how few turns the session took.
An anchor onto a file the cluster did not change earns nothing, so one
well-evidenced claim is worth more than several asserted ones.

The interview task is scored on its own terms. Its workspace is planted with
three claims and one transcript of the author answering them, recorded before
any question existed. The session must draft exactly one question per claim,
quote the claim's own wording inside it, record an answer against the
transcript, and anchor the claim to that interview. Sign-off is the trap: a
claim may be signed off only when the author's recorded answer repeats its
wording word for word, and signing off one they paraphrased or corrected scores
zero however good the rest of the session was. So does altering the transcript.
"""


@dataclass(frozen=True)
class RunSettings:
    """One optimization: what to search, how far, and where it writes.

    Attributes:
        name: Names the run to the backend and its directory on disk.
        stage: ``"fake"`` for a rehearsal against the fake agent, ``"pilot"``
            for a run that spends money.
        root: Experiments root; this optimization owns
            ``<root>/optimize/<name>/``.
        examples: What every candidate is measured on. With no separate
            validation set the backend both scores and selects on these.
        max_evals: Cap on evaluator calls. Every candidate is scored on every
            example, so size it off ``len(examples)``.
        max_token_cost: USD cap on the backend's own proposer spend, or None.
        reflection_lm: A LiteLLM model id, or any callable taking the
            reflection prompt and returning the reply.
        reflection_lm_kwargs: Passed to the model id; ignored for a callable.
        seed: The backend's own RNG seed, for a reproducible search.
        stop_at_score: Stop once a candidate reaches this; the score's
            ceiling is 1.0.
        test_examples: Scored once at the end for an unbiased number, outside
            the search and outside the budget.
    """

    name: str
    stage: str
    root: Path
    examples: tuple[Example, ...]
    max_evals: int
    max_token_cost: float | None
    reflection_lm: str | Callable[[str], str]
    reflection_lm_kwargs: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    stop_at_score: float = 1.0
    test_examples: tuple[Example, ...] = ()

    @property
    def directory(self) -> Path:
        """Where this optimization's own artifacts are written."""
        return self.root / OPTIMIZE_DIR / self.name


class SeedEchoLM:
    """Reflection LM for Stage 1: always proposes the seed candidate.

    A rehearsal exists to prove the loop, not to improve anything, so the
    proposal has to be a candidate the codec accepts without a model being
    called for it. GEPA parses a reflection reply with
    ``InstructionProposalSignature.output_extractor``
    (``gepa/strategies/instruction_proposal.py:125``): it takes everything
    between the first and the last ``` fence, drops a leading language
    specifier, and strips the result, having already stripped the raw reply
    (``gepa/proposer/reflective_mutation/reflection_lm.py:186``). A single
    fenced block therefore round-trips the seed exactly, up to the surrounding
    whitespace both ends drop — and any fences inside the seed survive, since
    only the outermost pair is cut.
    """

    def __init__(self, seed_candidate: str) -> None:
        """Hold the text every reflection will propose.

        Args:
            seed_candidate: The encoded bundle to echo back.
        """
        self._seed = seed_candidate

    def __call__(self, prompt: str) -> str:
        """Return the seed as a fenced block, ignoring the prompt.

        Args:
            prompt: The reflection prompt, unread.

        Returns:
            The reply the proposal parser reads the seed back out of.
        """
        return "```\n" + self._seed.strip() + "\n```"


def log(message: str) -> None:
    """Route one line to the backend's feedback, or to standard output.

    ``gepa``'s own log captures into the feedback its proposer reads, but only
    inside an evaluator call; outside one it warns and discards. Lines from
    the optimization itself therefore go to standard output instead of
    vanishing.

    Args:
        message: The line to record.
    """
    try:
        from gepa.optimize_anything import get_log_context
        from gepa.optimize_anything import log as backend_log
    except ImportError:
        print(message)
        return
    try:
        get_log_context()
    except RuntimeError:
        print(message)
        return
    backend_log(message)


def gepa_config(
    settings: RunSettings, run_dir: Path, output_dir: Path
) -> "OptimizeAnythingConfig":
    """Build the backend's configuration for one optimization.

    Args:
        settings: The optimization's own settings.
        run_dir: The backend's workspace. An existing one holding
            ``gepa_state.bin`` is resumed rather than replaced.
        output_dir: Where the eval server records each evaluation.

    Returns:
        The configuration, whose ``engine_config`` is validated by the backend
        when the engine is constructed.
    """
    from gepa.optimize_anything import OptimizeAnythingConfig

    return OptimizeAnythingConfig(
        engine="gepa",
        name=settings.name,
        max_evals=settings.max_evals,
        max_token_cost=settings.max_token_cost,
        # Every evaluation freezes a campaign, and every campaign of one
        # optimization shares a single authenticated profile directory and a
        # single run id, so two at once would overwrite each other's state.
        max_concurrency=1,
        stop_at_score=settings.stop_at_score,
        run_dir=str(run_dir),
        output_dir=str(output_dir),
        engine_config={
            "reflection": {
                "reflection_lm": settings.reflection_lm,
                "reflection_lm_kwargs": dict(settings.reflection_lm_kwargs),
                "reflection_minibatch_size": len(settings.examples),
            },
            "engine": {
                "max_workers": 1,
                "seed": settings.seed,
                "frontier_type": "hybrid",
                # gepa's own default. Kept explicit so the whole set stays
                # visible here. False would convert EvaluatorAbort (raised
                # when the harness has faulted repeatedly) into a plain 0.0
                # score, burning the rest of the budget on a run that should
                # have stopped instead.
                "raise_on_exception": True,
            },
        },
    )


def run(settings: RunSettings, evaluator: Evaluator) -> "GEPAResult":
    """Search for a better bundle than the one the evaluator was seeded with.

    Args:
        settings: The optimization's own settings.
        evaluator: Scores one candidate on one example; its seed bundle is
            what the search starts from.

    Returns:
        The backend's result, also written to ``result.json`` under
        :attr:`RunSettings.directory`.
    """
    from gepa.optimize_anything import optimize_anything

    directory = settings.directory
    directory.mkdir(parents=True, exist_ok=True)
    result = optimize_anything(
        seed_candidate=encode(evaluator.settings.seed),
        evaluator=evaluator,
        dataset=list(settings.examples),
        valset=None,
        test_set=list(settings.test_examples) or None,
        objective=OBJECTIVE,
        background=BACKGROUND,
        config=gepa_config(settings, directory / GEPA_DIR, directory / EVALS_DIR),
    )
    _write_result(settings, evaluator, result, directory / RESULT_FILE)
    return result


def load_examples(spec: Mapping[str, Any]) -> tuple[Example, ...]:
    """Read the examples an optimization measures on out of a spec.

    Args:
        spec: A mapping holding ``examples``, a list of entries. Every entry
            names a ``kind`` (``loop`` or ``interview``), an ``id``, a
            ``pristine_dir`` and optionally a ``budget_usd``; a loop entry
            also names the ``cluster_id`` it scores. An interview entry's
            baseline is read back through
            :func:`~.fixtures.load_interview_fixture`.

    Returns:
        The examples, in the order the spec lists them.

    Raises:
        ExperimentError: If the spec names no examples, or an entry is of an
            unknown kind, is missing a field its kind needs, or holds a value
            that field cannot take.
    """
    entries = spec.get(EXAMPLES_KEY)
    if not isinstance(entries, list) or not entries:
        raise ExperimentError(f"the spec's {EXAMPLES_KEY!r} names no examples")
    return tuple(_example(index, entry) for index, entry in enumerate(entries))


def _example(index: int, entry: Mapping[str, Any]) -> Example:
    """One spec entry as the example it describes."""
    kind = entry.get("kind")
    if kind not in (LoopExample.kind, InterviewExample.kind):
        raise ExperimentError(f"example {index}: unknown kind {kind!r}")
    try:
        budget = (
            {"budget_usd": float(entry["budget_usd"])} if "budget_usd" in entry else {}
        )
        pristine_dir = Path(entry["pristine_dir"])
        if kind == LoopExample.kind:
            return LoopExample(
                id=entry["id"],
                pristine_dir=pristine_dir,
                cluster_id=entry["cluster_id"],
                **budget,
            )
        return InterviewExample(
            id=entry["id"],
            fixture=load_interview_fixture(pristine_dir),
            **budget,
        )
    except KeyError as error:
        raise ExperimentError(f"example {index}: missing {error}") from error
    except (TypeError, ValueError) as error:
        raise ExperimentError(f"example {index}: {error}") from error


def _readable(value: Any) -> str:
    """A path as its text, anything else JSON cannot hold as its repr."""
    return str(value) if isinstance(value, Path) else repr(value)


def _example_scores(settings: RunSettings, result: "GEPAResult") -> dict[str, float]:
    """What the best candidate scored on each example, under its own id.

    The backend keys validation scores by an example's position in the
    dataset it was handed, which is nothing to go on when the file is read
    later. A key that is not one of those positions is kept as it came, so a
    change in that scheme shows up rather than dropping the score.
    """
    if not result.val_subscores:
        return {}
    names = {position: example.id for position, example in enumerate(settings.examples)}
    return {
        names.get(key, str(key)): value
        for key, value in result.val_subscores[result.best_idx].items()
    }


def _write_result(
    settings: RunSettings,
    evaluator: Evaluator,
    result: "GEPAResult",
    path: Path,
) -> Path:
    """Write what the search found, and what it was run with.

    The settings are recorded beside the outcome because a result read months
    later is only interpretable against the budget, the examples and the
    proposer that produced it.
    """
    record: dict[str, Any] = {
        "name": settings.name,
        "stage": settings.stage,
        "ai_rfc": git_describe(evaluator.settings.source_plugin_root),
        "settings": asdict(settings),
        "best_candidate": result.best_candidate,
        "best_score": result.best_score,
        "candidates": len(result.candidates),
        "best_example_scores": _example_scores(settings, result),
        "total_evals": result.total_evals,
        "evaluations": evaluator.evaluations,
    }
    metadata = result.metadata or {}
    for key in (
        "test_score",
        "test_scores",
        "baseline_test_score",
        "baseline_test_scores",
    ):
        if key in metadata:
            record[key] = metadata[key]
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True, default=_readable) + "\n"
    )
    return path
