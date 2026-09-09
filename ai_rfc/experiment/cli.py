"""The ``python -m ai_rfc.experiment`` command-line surface."""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..config import (
    ConfigError,
    experiments_root,
    field_default,
    load_config,
    profile_dir,
)
from ..lifecycle.profile import init_profile, login_command
from ..lifecycle.workspace import TEMPLATE_COMMIT, TEMPLATE_URL
from . import DEFAULT_MODEL, EFFORTS, ExperimentError
from .arms import ARMS
from .workspace import migrate_draft as migrate_draft_workspace
from .workspace import prepare as prepare_workspace
from .workspace import reseal as reseal_workspace

if TYPE_CHECKING:
    from .config import Campaign

#: Cluster rounds between consolidation rounds, read from the schema's declared
#: default for ``sessions.consolidate_every`` rather than restated here. The
#: same number configures a reconstruction and freezes into a campaign, and two
#: literals would let the operator-facing value and the campaign's drift apart
#: without anything saying so. What is shared is the *declared default*: this
#: verb reads no value an operator wrote into a ``recon.yaml``, so
#: ``--consolidate-every`` is the only way to change what a campaign freezes.
DEFAULT_CONSOLIDATE_EVERY: int = field_default("sessions.consolidate_every")

#: Where a run records the sessions appended to it after it finished. One JSON
#: object per line, in the run directory beside the transcript it explains.
APPENDED_FILE = "appended.jsonl"

#: Printed after every ``optimize apply``. The verb writes the working tree
#: and stops there, and a diff nobody was told to read is a diff that gets
#: committed unread.
NOT_COMMITTED = (
    "Nothing was committed; review the diff, then run "
    "tests/experiment/test_render.py."
)

#: The only model id a rehearsal records. The fake agent answers to anything,
#: so this is what keeps a real id — and the spend it implies — out of a stage
#: that asks for no consent because it cannot spend.
FAKE_MODEL = "fake-model"

#: What a ``claude-cli:`` judge runs at. Measured on 2026-09-04: a short
#: grading call takes 3 to 4 s at low effort and 112 s at the default, and
#: the judge makes one call per anchored claim per evaluation, fourteen on
#: the largest seed run, so the default would add half an hour to each.
JUDGE_EFFORT = "low"
JUDGE_TIMEOUT_S = 120


#: Evaluations one whole search round costs, as a multiple of the example
#: count: the seed over the selection set, the current candidate over a
#: minibatch, and the proposal over that same minibatch, with the minibatch
#: being the selection set. A rehearsal given less than this never scores a
#: proposal at all and reports a converged search rather than a starved one.
_REHEARSAL_ROUNDS = 3


def _report(message: str) -> None:
    print(message, file=sys.stderr)


def _window(value: str) -> tuple[int, int]:
    """Parse ``LOW-HIGH`` into an inclusive ordinal window.

    Args:
        value: The flag's raw text.

    Returns:
        The inclusive bounds.

    Raises:
        argparse.ArgumentTypeError: If the text is not two ordinals, or the
            bounds are reversed.
    """
    low, _, high = value.partition("-")
    if not high or not low.isdigit() or not high.isdigit():
        raise argparse.ArgumentTypeError(
            f"window must be LOW-HIGH, e.g. 49-51; got {value!r}"
        )
    bounds = (int(low), int(high))
    if bounds[0] > bounds[1] or bounds[0] < 1:
        raise argparse.ArgumentTypeError(f"window {value!r} is not an ordinal range")
    return bounds


def _arms(value: str) -> tuple[str, ...]:
    """Parse and validate a comma-separated arm list.

    Validating here rather than in ``init_campaign`` matters because campaign
    init runs the parity suite first: a typo caught at parse time costs an
    argparse error, and a typo caught later costs that whole suite's runtime.

    Args:
        value: The raw ``--arms`` string, e.g. ``"A,C"``.

    Returns:
        The parsed arms, in the order given.

    Raises:
        argparse.ArgumentTypeError: If the list is empty, repeats an arm, or
            names one the harness does not define.
    """
    arms = tuple(part.strip() for part in value.split(",") if part.strip())
    if not arms:
        raise argparse.ArgumentTypeError("no arms given")
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown arm(s) {', '.join(unknown)}; known arms are " f"{', '.join(ARMS)}"
        )
    if len(set(arms)) != len(arms):
        raise argparse.ArgumentTypeError(f"repeated arm in {value!r}")
    return arms


def _model(value: str) -> str:
    """Reject an empty model id without pinning the set of valid ones.

    A ``choices=`` list here would lock the harness out of every model released
    after this file was written, which is the opposite of what the flag is for.

    Args:
        value: The raw ``--model`` string.

    Returns:
        The stripped model id.

    Raises:
        argparse.ArgumentTypeError: If it is blank.
    """
    model = value.strip()
    if not model:
        raise argparse.ArgumentTypeError("model id cannot be empty")
    return model


def _interval(value: str) -> int:
    """Parse a consolidation interval, refusing one that inverts the flag.

    The schedule asks whether the cluster rounds since the last consolidation
    reach the interval, so a negative one is reached by the very first cluster:
    a mistyped minus sign turns "rarely" into "after every round" and buys a
    paid editorial pass per cluster. ``recon.yaml``'s loader already refuses a
    negative integer for the same field, and this keeps the flag saying no less.

    Args:
        value: The flag's raw text.

    Returns:
        The interval; 0 disables mid-sweep rounds.

    Raises:
        argparse.ArgumentTypeError: If it is not a non-negative integer.
    """
    try:
        interval = int(value)
    except ValueError:
        interval = -1
    if interval < 0:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a non-negative integer; 0 disables mid-sweep "
            f"rounds, and a negative interval would buy one after every cluster"
        )
    return interval


def _repo_root() -> Path:
    """The repository this package is installed from."""
    return Path(__file__).resolve().parents[2]


def _default_plugin_dir() -> Path:
    """The ai-rfc plugin beside this package."""
    return _repo_root() / "plugins" / "ai-rfc"


def _fake_claude() -> Path:
    """The stand-in agent a rehearsal drives; it ships with the tests."""
    return _repo_root() / "tests" / "experiment" / "fake_claude" / "claude"


def _refuse_an_unpriced_reflection_model(model: str) -> None:
    """Stop a pilot whose proposer spend nothing can measure.

    ``--max-token-cost`` becomes gepa's ``max_reflection_cost``, and the
    stopper reading it totals what ``litellm.completion_cost`` reports. gepa
    swallows a pricing failure as a cost of 0.00, so against a model litellm
    does not price the total never rises, the stopper never fires, and the
    only thing bounding the proposer is ``--max-evals`` — while the flag was
    required precisely so something else would.

    Args:
        model: The ``--reflection-lm`` id, as litellm will see it.

    Raises:
        ExperimentError: If litellm prices neither the id nor the id with its
            provider prefix stripped.
    """
    import litellm

    bare = model.split("/", 1)[1] if "/" in model else model
    if model in litellm.model_cost or bare in litellm.model_cost:
        return
    try:
        litellm.get_model_info(model)
        return
    except Exception:  # noqa: BLE001 - any lookup failure reads as unpriced
        pass
    raise ExperimentError(
        f"litellm does not price {model}, so --max-token-cost cannot bind: "
        "gepa reads the proposer's spend from litellm.completion_cost and "
        "counts an unpriced call as 0.00, which leaves --max-evals the only "
        "bound on it. Name a model litellm prices, or check the one you want "
        "first with the gepa skill's preflight script (~/.claude/plugins/"
        "cache/gepa/gepa-optimize-anything/0.1.0/scripts/preflight.py)"
    )


def _run_parity(python: str) -> dict:
    """Run the server parity suite; the protocol's stop-ship construct check.

    Args:
        python: Interpreter to run pytest with.

    Returns:
        Whether it passed and pytest's last line.
    """
    env = {**os.environ, "SSLKEYLOGFILE": ""}
    completed = subprocess.run(
        [python, "-m", "pytest", "-q", "tests/server/test_parity.py"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return {
        "passed": completed.returncode == 0,
        "summary": lines[-1] if lines else completed.stderr[-200:],
    }


def _optimize_run(args: argparse.Namespace, root: Path) -> int:
    """Search for a better bundle, or refuse to start.

    A pilot pays for every evaluation and for every proposal, so it names each
    model and, for a LiteLLM proposer, both ceilings itself, prints what the
    worst case costs and waits to be told to go ahead. A rehearsal names
    nothing: it drives the fake agent the tests ship, rates every anchored
    claim a perfect fit and proposes the seed straight back, so it exercises
    the whole loop without a paid call.

    Args:
        args: The parsed ``optimize run`` arguments.
        root: The experiments root; this optimization owns
            ``<root>/optimize/<name>``.

    Returns:
        0 once the search has finished and written its result.

    Raises:
        ExperimentError: If the stage's preconditions are not met, in which
            case nothing has been created.
    """
    from ai_rfc.draft.build import BuildReport

    from .config import Campaign
    from .optimize.claude_cli import ClaudeCliCall, cli_model
    from .optimize.codec import encode, seed_from_plugin
    from .optimize.evaluator import Evaluator, EvaluatorSettings
    from .optimize.judge import anthropic_transport, build_judge
    from .optimize.run import (
        OPTIMIZE_DIR,
        RESULT_FILE,
        RunSettings,
        SeedEchoLM,
        load_examples,
        log,
    )
    from .optimize.run import run as optimize
    from .optimize.scoring import ClaimHunk, Judge, Judgement

    def rehearsal_judge(hunks: list[ClaimHunk]) -> list[Judgement]:
        """Rate every anchored claim a perfect fit, calling nothing."""
        return [Judgement(hunk.claim_id, 1.0, "rehearsal stub") for hunk in hunks]

    def rehearsal_build(campaign: Campaign, workspace: Path) -> BuildReport:
        """Report a clean compile without a toolchain to run one.

        A rehearsal has no real toolchain — the point is to cost nothing —
        so building for real would score every candidate's prose term zero
        and leave a fifth of the value untried. This is a constant rather
        than a measurement, which is the reason a rehearsal's scores mean
        nothing next to a pilot's; what it buys is that the term, and the
        scoring around it, is exercised at all.
        """
        return BuildReport(
            ref="HEAD",
            commit="0" * 40,
            draft="rehearsal",
            source_sha256="0" * 64,
            date="1970-01-01",
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

    plugin_root = (args.plugin_root or _default_plugin_dir()).resolve()
    seed = seed_from_plugin(plugin_root)
    max_evals = args.max_evals
    max_token_cost = args.max_token_cost
    profile = args.profile_dir or profile_dir(root)
    judge: Judge
    reflection_lm: str | SeedEchoLM | ClaudeCliCall
    build: Callable[[Campaign, Path], BuildReport | None] | None

    if args.stage == "pilot":
        proposer = None if args.reflection_lm is None else cli_model(args.reflection_lm)
        cli_judge = None if args.judge_model is None else cli_model(args.judge_model)
        wanted = [
            ("--max-evals", args.max_evals),
            ("--model", args.model),
            ("--reflection-lm", args.reflection_lm),
            ("--judge-model", args.judge_model),
        ]
        if proposer is None:
            wanted.insert(1, ("--max-token-cost", args.max_token_cost))
        missing = [flag for flag, value in wanted if value is None]
        if missing:
            raise ExperimentError(
                "a pilot pays for every evaluation and every proposal, so it "
                f"names each cost itself; missing {', '.join(missing)}"
            )
        if proposer is not None and max_token_cost is not None:
            raise ExperimentError(
                "--max-token-cost cannot bind with a claude-cli: proposer: gepa "
                "meters a callable at 0.00, so the cap would be a promise nothing "
                "enforces. Drop it; --max-evals and --timeout-s are the caps"
            )
        if (
            proposer is None
            and cli_judge is not None
            and not os.environ.get("ANTHROPIC_API_KEY")
        ):
            # A claude-cli: judge does not build anthropic_transport, the
            # package's only credential check, so the proposer's own key is
            # checked here; its first call comes after the seed evaluation has
            # already spent sessions.
            raise ExperimentError(
                f"--reflection-lm {args.reflection_lm} bills ANTHROPIC_API_KEY, "
                "which is not set; the claude-cli: judge draws on the profile "
                "instead, so nothing else checks it. Set the key, or name a "
                "claude-cli:<model> proposer to run on the profile too"
            )
        claude_bin = args.claude_bin or "claude"
        # Where the one-shot calls run; created on their first call, so a
        # refusal below still leaves nothing behind.
        calls_dir = root / OPTIMIZE_DIR / args.name
        if cli_judge is None:
            judge = build_judge(anthropic_transport(args.judge_model))
        else:
            judge = build_judge(
                ClaudeCliCall(
                    claude_bin,
                    profile,
                    cli_judge,
                    cwd=calls_dir,
                    effort=JUDGE_EFFORT,
                    timeout_s=JUDGE_TIMEOUT_S,
                )
            )
        if proposer is None:
            reflection_lm = args.reflection_lm
        else:
            reflection_lm = ClaudeCliCall(
                claude_bin,
                profile,
                proposer,
                cwd=calls_dir,
                effort=args.effort,
                timeout_s=args.timeout_s,
            )
        model = args.model
        build = None
    else:
        fake = _fake_claude()
        claude_bin = args.claude_bin or str(fake)
        if not Path(claude_bin).exists():
            raise ExperimentError(
                f"{claude_bin} is not there; a rehearsal drives the fake agent "
                "that ships with the tests, so point --claude-bin at one"
            )
        # A rehearsal has no --yes and no printed ceiling, because by
        # construction it cannot spend: the seed is echoed back, the judge and
        # the build are constants, and the agent is a script. Point it at the
        # real CLI and every one of those bounds is gone while the flags that
        # exist to ask about money are still not being asked for.
        if Path(claude_bin).resolve() != fake.resolve():
            raise ExperimentError(
                f"--stage fake drives the agent that ships with the tests, and "
                f"{claude_bin} is not it ({fake}). A rehearsal never asks for "
                "--yes because it cannot spend; a real agent here would launch "
                "paid sessions through the shared profile unasked. Use --stage "
                "pilot to spend"
            )
        named = [
            flag
            for flag, value in (
                ("--reflection-lm", args.reflection_lm),
                ("--judge-model", args.judge_model),
            )
            if value is not None
        ]
        if named:
            raise ExperimentError(
                "--stage fake proposes the seed back and rates every claim "
                f"itself, so {', '.join(named)} names a model this stage never "
                "calls and a pilot would pay for. Use --stage pilot to name one"
            )
        # Set before anything imports gepa, which pulls litellm, which
        # fetches its cost map from GitHub at import time unless told not to.
        # A rehearsal is defined by reaching nothing; the local map is also
        # the only one it could get behind a command sandbox.
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        judge = rehearsal_judge
        build = rehearsal_build
        reflection_lm = SeedEchoLM(encode(seed))
        model = args.model or FAKE_MODEL
        if model != FAKE_MODEL:
            raise ExperimentError(
                f"--stage fake records {FAKE_MODEL} on every campaign it "
                f"freezes; {model} names a model that would be paid for if the "
                "agent were ever pointed at a real CLI. Use --stage pilot to "
                "name a real one"
            )
        # max_token_cost is left unset rather than zeroed. It becomes gepa's
        # max_reflection_cost, and MaxReflectionCostStopper stops as soon as
        # cost >= the cap; a callable reflection LM is wrapped in TrackingLM,
        # which always reports 0.0, so a zero cap would end the rehearsal
        # before it scored anything. SeedEchoLM cannot spend either way, and
        # --max-evals still bounds the run.

    examples = load_examples(json.loads(args.examples.read_text()))
    if max_evals is None:
        max_evals = _REHEARSAL_ROUNDS * len(examples)

    if args.stage == "pilot":
        per_example = max(example.budget_usd for example in examples)
        if max_token_cost is None:
            print(
                f"worst case: 2 x {max_evals} harness sessions on {model} of up to "
                f"{args.timeout_s} s each (${per_example:.2f} is one session's own "
                f"cap, not a bill), plus up to {max_evals} proposer calls on "
                f"{reflection_lm!r} of up to {args.timeout_s} s each, plus one "
                f"judge call per anchored claim per evaluation on {args.judge_model}"
            )
            meter = (
                "nothing here bills a key: every call draws on the subscription "
                "behind the profile, whose usage limit is the only meter"
                if cli_judge is not None
                else f"the judge on {args.judge_model} bills ANTHROPIC_API_KEY; "
                "the sessions and the proposer draw on the subscription behind "
                "the profile, whose usage limit is their only meter"
            )
            print(
                "the factor of two is the evaluator's one retry per faulted run; "
                f"{meter}, and --max-evals and --timeout-s are the only caps"
            )
        else:
            worst_case = 2 * max_evals * per_example + max_token_cost
            print(
                f"worst case: 2 x {max_evals} evaluations x ${per_example:.2f} + "
                f"${max_token_cost:.2f} proposer = ${worst_case:.2f}"
            )
            print(
                "the factor of two is the evaluator's one retry per faulted run; "
                "plus judge calls (one short request per anchored claim per "
                "evaluation), which are not in the figure above"
            )
        if not args.yes:
            raise ExperimentError("pass --yes to spend it")

    # Last, so that a pilot's cost refusals are reported on any interpreter:
    # they are what stops money being spent, and this only stops a traceback.
    if importlib.util.find_spec("gepa") is None:
        raise ExperimentError(
            "no search backend on this interpreter; gepa installs under the "
            "optimize extra, which needs Python 3.11 - run this verb with that "
            "environment's python"
        )

    if args.stage == "pilot":
        if cli_model(args.reflection_lm) is None:
            _refuse_an_unpriced_reflection_model(args.reflection_lm)
        # Last of the refusals because it is the slow one: verify clones and
        # builds the template twice. Every evaluation's campaign is frozen
        # with verify_toolchain off, so this is the one place the record is
        # checked — and a bad record would otherwise zero the prose term for
        # every candidate, visible only in the log.
        from ..toolchain import verify as verify_toolchain

        ok, reasons = verify_toolchain(args.toolchain)
        if not ok:
            raise ExperimentError(
                f"{args.toolchain} does not verify, and a pilot builds every "
                "draft with it: unchecked, each candidate would score its "
                f"prose term zero for a reason nothing reports. {'; '.join(reasons)}"
            )

    settings = RunSettings(
        name=args.name,
        stage=args.stage,
        root=root,
        examples=examples,
        max_evals=max_evals,
        max_token_cost=max_token_cost,
        reflection_lm=reflection_lm,
        seed=args.seed,
    )
    evaluator = Evaluator(
        EvaluatorSettings(
            root=settings.directory,
            profile_dir=profile,
            python=args.python,
            claude_bin=claude_bin,
            model=model,
            effort=args.effort,
            timeout_s=args.timeout_s,
            panther_repo=(args.panther_repo or _repo_root()).resolve(),
            toolchain=args.toolchain.resolve(),
            source_plugin_root=plugin_root,
            seed=seed,
            judge=judge,
            build=build,
            log=log,
        )
    )
    result = optimize(settings, evaluator)
    print(f"best score: {result.best_score}")
    print(f"candidates: {len(result.candidates)}  evaluations: {result.total_evals}")
    print(f"result: {settings.directory / RESULT_FILE}")
    return 0


def _record_append(
    run_dir: Path, due: Any, lines_before: int, recorded: bool, timed_out: bool
) -> None:
    """Note, in the run directory, a session appended after the run finished.

    ``status.json`` is written once and never revised, so once this verb has
    run it describes a prefix of ``events.jsonl`` — and ``audit_run`` reads the
    two together. This says where that prefix ends and what lies past it, so
    the discrepancy reads as a decision rather than as corruption.

    Args:
        run_dir: The run's directory.
        due: The round that was run.
        lines_before: Transcript lines present before it was launched.
        recorded: Whether the round recorded its revision.
        timed_out: Whether the session was killed on its cap.
    """
    record = {
        "kind": "consolidation",
        "ordinal": due.ordinal,
        "base_cluster": due.base_cluster,
        "appended_at": datetime.now(timezone.utc).isoformat(),
        "events_lines_before": lines_before,
        "recorded": recorded,
        "timed_out": timed_out,
    }
    with (run_dir / APPENDED_FILE).open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _run_one_consolidation(
    campaign: Campaign, only: list[str] | None, acknowledged: bool
) -> int:
    """Run one consolidation round against a run's workspace as it stands.

    The round is asked with ``at_end`` although no sweep surrounds it: a
    consolidation run by hand consolidates whatever the workspace still has
    outstanding, which is not a question the interval answers.

    Args:
        campaign: The frozen campaign the run belongs to.
        only: Run ids from ``--only``; exactly one is required.
        acknowledged: Whether the caller accepted appending to a finished run.

    Returns:
        0 when the round recorded its revision, 1 when it did not.

    Raises:
        ExperimentError: If ``--only`` does not name exactly one run of this
            campaign, the append went unacknowledged, or that run has no
            workspace.
    """
    from . import per_cluster
    from .consolidation import consolidation_due
    from .runner import EVENTS_FILE, run_ref

    if only is None or len(only) != 1:
        raise ExperimentError(
            "--task consolidation runs one round against one run; name that "
            "run with --only <run id>"
        )
    ref = run_ref(campaign, only[0])
    if ref.arm == "C":
        # The sweep declines C's rounds because D42 freezes its tool surface,
        # and nothing further down would: the arm's consolidation prompt is
        # rendered like every other one, so the round would launch and spend.
        # Ahead of the acknowledgment, because this refusal is unconditional:
        # asking for the flag first walks an operator through accepting a risk
        # that was never on the table, and then refuses them anyway.
        _report(
            f"{ref.run_id}: arm C does not consolidate (its tool surface is frozen)"
        )
        return 1
    if not acknowledged:
        # The launcher refuses to relaunch a run in place, and a run directory
        # exists only because it launched once. Appending a session to it is
        # defensible — the sweep's own final round is one — but it leaves
        # status.json describing a prefix of a transcript the audit reads
        # whole, so it is accepted explicitly rather than crossed in silence.
        raise ExperimentError(
            "a consolidation round appends a session to a run that already "
            "finished, leaving status.json describing only a prefix of "
            "events.jsonl; pass --append-to-finished-run to accept that, and "
            "point it at a copy — never at a sealed baseline"
        )
    if not ref.workspace.is_dir():
        raise ExperimentError(
            f"{ref.workspace} does not exist; a consolidation round edits a "
            f"workspace some sweep already left behind"
        )
    due = consolidation_due(ref.workspace, campaign.consolidate_every, at_end=True)
    if due is None:
        # consolidation_due answers None for a revisions.yaml it cannot read as
        # well as for one with nothing outstanding, and deliberately: a
        # malformed map is the gate's finding, not a thing to edit. Inside a
        # sweep that costs a round nobody needed; here it would spend on a
        # session with nothing to tell it what to consolidate.
        _report(
            f"{ref.run_id}: nothing to consolidate — "
            f"{ref.workspace / 'revisions.yaml'} records no unconsolidated "
            f"cluster round the gate's own loader accepts"
        )
        return 1
    events_path = ref.run_dir / EVENTS_FILE
    # Counted before the round, because the round appends to this same file.
    lines_before = (
        len(events_path.read_text(errors="replace").splitlines())
        if events_path.exists()
        else 0
    )
    recorded, timed_out = per_cluster._run_consolidation(
        campaign,
        ref,
        due,
        budget_usd=campaign.budget_usd,
        timeout_s=campaign.timeout_s,
        at_end=True,
        report=_report,
    )
    _record_append(ref.run_dir, due, lines_before, recorded, timed_out)
    print(
        f"{ref.run_id}: consolidation {due.ordinal:02d} "
        f"recorded={recorded} timed_out={timed_out}"
    )
    print(f"appended past line {lines_before} of {events_path}; see {APPENDED_FILE}")
    return 0 if recorded else 1


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Runs root (default: AI_RFC_EXPERIMENTS_ROOT or ~/ai-rfc-experiments).",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiment",
        description="AI+MCP vs AI+CLI experiment harness over the ai_rfc plugin.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profile_cmd = commands.add_parser("profile", help="Isolated Claude Code profile.")
    profile_verbs = profile_cmd.add_subparsers(dest="verb", required=True)
    profile_init = profile_verbs.add_parser("init", help="Create the profile dir.")
    _add_root(profile_init)

    preflight = commands.add_parser(
        "preflight", help="S0: prove the profile is hermetic."
    )
    _add_root(preflight)
    preflight.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )
    preflight.add_argument(
        "--claude", default="claude", help="Claude Code binary to launch."
    )
    preflight.add_argument(
        "--model",
        type=_model,
        default=DEFAULT_MODEL,
        help="Model id to launch against (default: %(default)s).",
    )
    preflight.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds before the session's process group is killed.",
    )

    render = commands.add_parser("render", help="Regenerate the plugin SKILL.md.")
    render.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )

    workspace = commands.add_parser("workspace", help="Pristine workspaces.")
    workspace_verbs = workspace.add_subparsers(dest="verb", required=True)
    prepare = workspace_verbs.add_parser("prepare", help="Build a pristine workspace.")
    _add_root(prepare)
    prepare.add_argument(
        "--config",
        type=Path,
        required=True,
        help="recon.yaml naming the source, the window and the draft.",
    )
    prepare.add_argument(
        "--window",
        type=_window,
        default=None,
        help="Inclusive ordinal range LOW-HIGH to leave unprocessed, "
        "overriding the config's own; e.g. 49-51 for a three-cluster slice.",
    )
    prepare.add_argument(
        "--template",
        default=TEMPLATE_URL,
        help="Internet-Draft template repository (default: %(default)s).",
    )
    prepare.add_argument(
        "--template-commit",
        default=TEMPLATE_COMMIT,
        help="Template commit to pin (default: %(default)s).",
    )
    prepare.add_argument(
        "--toolchain",
        type=Path,
        default=None,
        help="Toolchain record for sealing declared references, overriding "
        "the config's own.",
    )

    reseal = workspace_verbs.add_parser(
        "reseal",
        help="Seal a used workspace as the baseline a continuing campaign copies.",
    )
    _add_root(reseal)
    reseal.add_argument(
        "workspace",
        type=Path,
        help=(
            "A stopped run's workspace to continue from. It is copied, not "
            "modified: the run directory stays the evidence its audit reads."
        ),
    )
    reseal.add_argument(
        "--as",
        dest="name",
        required=True,
        help="Name for the resealed baseline under <root>/pristine.",
    )

    migrate_draft = workspace_verbs.add_parser(
        "migrate-draft",
        help="Move a library-root draft to the adopter layout in one commit.",
    )
    migrate_draft.add_argument(
        "workspace",
        type=Path,
        help="A workspace whose draft/ to migrate.",
    )
    migrate_draft.add_argument(
        "--template",
        default=TEMPLATE_URL,
        help="Internet-Draft template repository (default: %(default)s).",
    )
    migrate_draft.add_argument(
        "--template-commit",
        default=TEMPLATE_COMMIT,
        help="Template commit to pin (default: %(default)s).",
    )

    campaign = commands.add_parser("campaign", help="Frozen run matrices.")
    campaign_verbs = campaign.add_subparsers(dest="verb", required=True)
    init = campaign_verbs.add_parser("init", help="Freeze a campaign.")
    _add_root(init)
    init.add_argument(
        "--id", required=True, help="Campaign identifier; names its directory."
    )
    init.add_argument(
        "--baseline",
        required=True,
        help=(
            "Name under <root>/pristine, or a path. That directory keeps its "
            "recorded name; only the flag changed."
        ),
    )
    init.add_argument(
        "--arms",
        type=_arms,
        default="A,B,C",
        help=(
            "Comma-separated arms to run; one arm is a production run rather "
            f"than a comparison. Known arms: {', '.join(ARMS)}."
        ),
    )
    init.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="Runs per arm (default: %(default)s).",
    )
    init.add_argument(
        "--seed",
        type=int,
        default=20260826,
        help="Seed fixing the frozen run order (default: %(default)s).",
    )
    init.add_argument(
        "--model",
        type=_model,
        default=DEFAULT_MODEL,
        help="Model id every run launches against (default: %(default)s).",
    )
    init.add_argument(
        "--effort",
        choices=EFFORTS,
        default="high",
        help="Reasoning effort per launch (default: %(default)s).",
    )
    init.add_argument(
        "--budget",
        type=float,
        default=25.0,
        help=(
            "USD ceiling per run (default: %(default)s). Size it against the "
            "cluster count: a run killed on budget cannot be resumed in place."
        ),
    )
    init.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Seconds before a run's process group is killed (default: %(default)s).",
    )
    init.add_argument(
        "--panther-repo",
        type=Path,
        required=True,
        help="Checkout supplying the ai_rfc substrate every run may reach.",
    )
    init.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help="Default: the ai-rfc plugin beside this package.",
    )
    init.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter used for the parity suite (default: this one).",
    )
    init.add_argument(
        "--claude",
        default="claude",
        help="Claude Code binary; frozen as a resolved path, not a name.",
    )
    init.add_argument(
        "--session-mode",
        choices=("single", "per-cluster"),
        default="single",
        help=(
            "How a run is executed (default: %(default)s). single gives the "
            "whole window to one agent session; per-cluster spawns one per "
            "cluster, which over a long window avoids reasoning about late "
            "clusters from a compacted summary and makes a killed run resumable."
        ),
    )
    init.add_argument(
        "--toolchain",
        type=Path,
        default=None,
        help=(
            "toolchain.json from `ai-rfc toolchain provision` (default: "
            "<root>/tools/toolchain.json)."
        ),
    )
    init.add_argument(
        "--skip-parity",
        action="store_true",
        help="Skip the parity suite. It is the protocol's stop-ship check.",
    )
    init.add_argument(
        "--consolidate-every",
        type=_interval,
        default=DEFAULT_CONSOLIDATE_EVERY,
        help=(
            "Cluster rounds between consolidation rounds; 0 disables mid-sweep "
            "ones (default: %(default)s, the schema default this shares with "
            "recon.yaml's sessions.consolidate_every; a value configured there "
            "is not read)."
        ),
    )

    run = commands.add_parser("run", help="Launch pending runs in the frozen order.")
    run.add_argument("campaign", type=Path, help="Campaign directory.")
    run.add_argument("--only", default=None, help="Comma-separated run ids.")
    run.add_argument(
        "--task",
        choices=("sweep", "consolidation"),
        default="sweep",
        help=(
            "Run the window, or one consolidation round against the workspace "
            "of the single run named by --only, as it stands."
        ),
    )
    run.add_argument(
        "--append-to-finished-run",
        action="store_true",
        help=(
            "Required by --task consolidation: accept that the round appends a "
            "session to a run that already finished, so status.json will "
            "describe only a prefix of events.jsonl. Point it at a copy."
        ),
    )

    audit = commands.add_parser("audit", help="Audit every run's transcript.")
    audit.add_argument("campaign", type=Path, help="Campaign directory.")

    questions = commands.add_parser(
        "questions", help="List the developer questions a run has open."
    )
    questions.add_argument("run_dir", type=Path, help="A run directory.")
    questions.add_argument(
        "--all",
        action="store_true",
        help="Include questions already answered (default: open only).",
    )

    analyze = commands.add_parser(
        "analyze", help="Recompute outcomes; write aggregate.json and report.md."
    )
    analyze.add_argument("campaign", type=Path, help="Campaign directory.")

    optimize = commands.add_parser(
        "optimize", help="Search for better skill texts, and apply what it finds."
    )
    optimize_verbs = optimize.add_subparsers(dest="verb", required=True)

    seed_cmd = optimize_verbs.add_parser(
        "seed", help="Print the bundle an optimization starts from."
    )
    seed_cmd.add_argument(
        "--plugin-root",
        type=Path,
        required=True,
        help="The plugin whose loop template and three prose skills are encoded.",
    )
    seed_cmd.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the encoding here instead of to standard output.",
    )

    prepare_interview = optimize_verbs.add_parser(
        "prepare-interview", help="Build the interview task's pristine workspace."
    )
    _add_root(prepare_interview)
    prepare_interview.add_argument(
        "--panther-repo",
        type=Path,
        required=True,
        help="Checkout the prepared workspace records and resolves against.",
    )
    prepare_interview.add_argument(
        "--template",
        default=TEMPLATE_URL,
        help="Internet-Draft template repository (default: %(default)s).",
    )
    prepare_interview.add_argument(
        "--template-commit",
        default=TEMPLATE_COMMIT,
        help="Template commit to pin (default: %(default)s).",
    )
    prepare_interview.add_argument(
        "--toolchain",
        type=Path,
        default=None,
        help="Toolchain record; the interview target declares no references, "
        "so it goes unused.",
    )
    prepare_interview.add_argument(
        "--name",
        default="interview-fixture",
        help="Directory name of the sealed baseline (default: %(default)s).",
    )

    optimize_run = optimize_verbs.add_parser(
        "run", help="Search for a bundle that scores better than the plugin's."
    )
    _add_root(optimize_run)
    optimize_run.add_argument(
        "--name",
        required=True,
        help="Names the optimization, and its directory under <root>/optimize.",
    )
    optimize_run.add_argument(
        "--stage",
        choices=("fake", "pilot"),
        required=True,
        help=(
            "fake rehearses the whole loop against the agent the tests ship, "
            "paying for nothing; pilot runs it for real."
        ),
    )
    optimize_run.add_argument(
        "--examples",
        type=Path,
        required=True,
        help="JSON spec naming what every candidate is measured on.",
    )
    optimize_run.add_argument(
        "--max-evals",
        type=int,
        default=None,
        help=(
            "Cap on evaluator calls; required by a pilot. A rehearsal defaults "
            f"to {_REHEARSAL_ROUNDS} per example, which is one whole round."
        ),
    )
    optimize_run.add_argument(
        "--max-token-cost",
        type=float,
        default=None,
        help="USD ceiling on the proposer's own spend; required by a pilot whose "
        "--reflection-lm is a LiteLLM id, refused beside a claude-cli: one, which "
        "gepa meters at zero.",
    )
    optimize_run.add_argument(
        "--reflection-lm",
        type=_model,
        default=None,
        help="Pilot only: the proposer. A LiteLLM model id, or claude-cli:<model> "
        "to run it through `claude -p` on --profile-dir with no API key.",
    )
    optimize_run.add_argument(
        "--judge-model",
        type=_model,
        default=None,
        help="Pilot only: the model rating each anchored claim. An Anthropic API "
        "id (needs ANTHROPIC_API_KEY), or claude-cli:<model> to run it through "
        "`claude -p` on --profile-dir at low effort.",
    )
    optimize_run.add_argument(
        "--model",
        type=_model,
        default=None,
        help="Model every evaluation's run is launched against.",
    )
    optimize_run.add_argument(
        "--effort",
        choices=EFFORTS,
        default="high",
        help="Reasoning effort per launch (default: %(default)s).",
    )
    optimize_run.add_argument(
        "--timeout-s",
        type=int,
        default=7200,
        help="Seconds before one evaluation's run is killed (default: %(default)s).",
    )
    optimize_run.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help="The authenticated profile every run shares (default: <root>/profile).",
    )
    optimize_run.add_argument(
        "--toolchain",
        type=Path,
        required=True,
        help="toolchain.json every evaluation's campaign records; a campaign "
        "cannot be frozen without one. A pilot builds each draft with it, so "
        "the executables it names must exist (see `ai-rfc toolchain provision`); a "
        "rehearsal stubs the build, so any well-formed record will load.",
    )
    optimize_run.add_argument(
        "--claude-bin",
        default=None,
        help="Agent binary to launch (default: claude, or the fake one for a "
        "rehearsal).",
    )
    optimize_run.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter the runs' substrate shim executes (default: this one).",
    )
    optimize_run.add_argument(
        "--plugin-root",
        type=Path,
        default=None,
        help="The plugin the search starts from. Default: the ai-rfc plugin "
        "beside this package.",
    )
    optimize_run.add_argument(
        "--panther-repo",
        type=Path,
        default=None,
        help="Checkout each campaign records its revision of. Default: the "
        "repository this package is installed from.",
    )
    optimize_run.add_argument(
        "--seed",
        type=int,
        default=0,
        help="The backend's own RNG seed (default: %(default)s).",
    )
    optimize_run.add_argument(
        "--yes",
        action="store_true",
        help="Required by a pilot: proceed with the spend it prints.",
    )

    optimize_apply = optimize_verbs.add_parser(
        "apply", help="Write a candidate into the plugin, committing nothing."
    )
    optimize_apply.add_argument(
        "candidate", type=Path, help="File holding the candidate to write."
    )
    optimize_apply.add_argument(
        "--plugin-root",
        type=Path,
        required=True,
        help="The plugin to write into. Named rather than defaulted: this "
        "verb changes a working tree.",
    )
    optimize_apply.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Where the loop template is written, and what the loop skill is "
        "then rendered from. Default: the packaged template.",
    )
    optimize_apply.add_argument(
        "--force",
        action="store_true",
        help="Write even over uncommitted changes to the files it replaces.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one harness command.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        0 on success, 1 when the harness refused or an input was unusable, and
        3 when a gate said no — ``preflight`` not reaching "go", or the parity
        suite failing. 2 is left to ``argparse``, as everywhere else in this
        package: a caller must be able to tell a mistyped flag from a gate that
        must stop a campaign, and the two call for opposite responses.
    """
    args = _parser().parse_args(argv)
    root = args.root if getattr(args, "root", None) else experiments_root()
    try:
        if args.command == "profile" and args.verb == "init":
            profile_path = init_profile(root)
            print(f"profile: {profile_path}")
            print(f"log in once with:\n  {login_command(root)}")
        elif args.command == "preflight":
            from .preflight import run_preflight

            plugin_dir = args.plugin_dir or _default_plugin_dir()
            report = run_preflight(
                root=root,
                plugin_dir=plugin_dir.resolve(),
                claude_bin=args.claude,
                model=args.model,
                timeout_s=args.timeout,
            )
            for check in report["checks"]:
                flag = "PASS" if check["passed"] else "FAIL"
                need = "required" if check["required"] else "product"
                print(f"{flag}  {check['check']:<14} ({need})")
            print(f"report: {root / 'spike-report.json'}")
            return 0 if report["go"] else 3
        elif args.command == "render":
            from .render import write_plugin_skill

            plugin_dir = args.plugin_dir or _default_plugin_dir()
            print(f"wrote {write_plugin_skill(plugin_dir.resolve())}")
        elif args.command == "workspace" and args.verb == "prepare":
            config_path = args.config.resolve()
            try:
                config = load_config(config_path)
            except ConfigError as error:
                raise ExperimentError(str(error)) from None
            if args.window is not None:
                config = dataclasses.replace(config, window=args.window)
            if args.toolchain is not None:
                config = dataclasses.replace(config, toolchain=args.toolchain)
            pristine = prepare_workspace(
                config,
                root=root,
                config_path=config_path,
                template=args.template,
                template_commit=args.template_commit,
            )
            record = json.loads((pristine / "pristine.json").read_text())
            print(f"pristine: {pristine}")
            print(
                f"clusters: {record['cluster_count']}  "
                f"pre-seeded: {len(record['pre_seeded'])}  "
                f"window: {record['window']}"
            )
        elif args.command == "workspace" and args.verb == "reseal":
            baseline = reseal_workspace(
                args.workspace.resolve(), root / "pristine" / args.name
            )
            record = json.loads((baseline / "pristine.json").read_text())
            print(f"pristine: {baseline}")
            print(f"draft HEAD: {record['draft_head']}  window: {record['window']}")
            print(f"resealed from: {record['resealed_from']} (left unmodified)")
        elif args.command == "workspace" and args.verb == "migrate-draft":
            head = migrate_draft_workspace(
                args.workspace.resolve(),
                template=args.template,
                template_commit=args.template_commit,
            )
            print(f"draft HEAD: {head}")
        elif args.command == "campaign" and args.verb == "init":
            from .config import CampaignConfig, init_campaign

            plugin_dir = (args.plugin_dir or _default_plugin_dir()).resolve()
            pristine = Path(args.baseline)
            if not pristine.is_absolute():
                pristine = root / "pristine" / args.baseline
            toolchain = args.toolchain
            if toolchain is None:
                default_toolchain = root / "tools" / "toolchain.json"
                if default_toolchain.exists():
                    toolchain = default_toolchain
            if toolchain is not None:
                toolchain = toolchain.resolve()
            parity = None if args.skip_parity else _run_parity(args.python)
            campaign = init_campaign(
                CampaignConfig(
                    root=root,
                    campaign_id=args.id,
                    pristine_dir=pristine,
                    arms=args.arms,
                    repeats=args.repeats,
                    seed=args.seed,
                    model=args.model,
                    effort=args.effort,
                    budget_usd=args.budget,
                    timeout_s=args.timeout,
                    panther_repo=args.panther_repo.resolve(),
                    plugin_root=plugin_dir,
                    python=args.python,
                    claude_bin=args.claude,
                    parity=parity,
                    session_mode=args.session_mode,
                    toolchain=toolchain,
                    consolidate_every=args.consolidate_every,
                )
            )
            print(f"campaign: {campaign.dir}")
            print(f"run order: {' '.join(campaign.run_order)}")
            print(f"parity: {parity}")
            if parity is not None and not parity["passed"]:
                _report("finding: parity suite FAILED - stop-ship per protocol")
                return 3
        elif args.command == "run":
            from .config import load_campaign
            from .driver import launch_pending

            campaign = load_campaign(args.campaign.resolve())
            only = args.only.split(",") if args.only else None
            if args.task == "consolidation":
                return _run_one_consolidation(
                    campaign, only, args.append_to_finished_run
                )
            statuses = launch_pending(
                campaign,
                only=only,
                report=_report,
            )
            for status in statuses:
                print(
                    f"{status.run_id}: exit={status.exit_code} "
                    f"timed_out={status.timed_out}"
                )
            failed = [
                status.run_id
                for status in statuses
                if status.timed_out or status.exit_code != 0
            ]
            if failed:
                # Every run's outcome was printed and then discarded, so a
                # driver could not distinguish a campaign where nothing worked
                # from one where everything did.
                _report(f"error: {len(failed)} run(s) failed: {', '.join(failed)}")
                return 1
        elif args.command == "audit":
            from .audit import audit_campaign
            from .config import load_campaign

            audits = audit_campaign(load_campaign(args.campaign.resolve()))
            for run_id, audit in audits.items():
                print(
                    f"{run_id}: integrity={audit['integrity']} "
                    f"bypass={audit['bypass_attempts']['count']} "
                    f"errors={audit['errors']['class1']}/{audit['errors']['class2']}"
                )
        elif args.command == "questions":
            import yaml

            from .summary import QUESTIONS_FILE

            path = args.run_dir.resolve() / "workspace" / QUESTIONS_FILE
            try:
                document = yaml.safe_load(path.read_text()) or {}
            except (OSError, yaml.YAMLError) as error:
                raise ExperimentError(f"could not read {path}: {error}") from error
            entries = document.get("questions") or {}
            shown = [
                (key, entry)
                for key, entry in sorted(entries.items())
                if isinstance(entry, dict)
                and (args.all or entry.get("status") == "open")
            ]
            openq = sum(
                1
                for entry in entries.values()
                if isinstance(entry, dict) and entry.get("status") == "open"
            )
            print(f"{openq} open of {len(entries)}")
            for key, entry in shown:
                claims = ", ".join(entry.get("claim_ids") or []) or "no claim"
                print(f"\n  {key}  [{claims}]  asked {entry.get('asked_at')}")
                print(f"    {str(entry.get('question') or '').strip()}")
        elif args.command == "analyze":
            from .audit import audit_campaign
            from .config import load_campaign
            from .metrics import analyze_campaign
            from .report import render_report

            campaign = load_campaign(args.campaign.resolve())
            audit_campaign(campaign)
            aggregate = analyze_campaign(campaign)
            report_path = campaign.analysis_dir / "report.md"
            report_path.write_text(render_report(aggregate))
            print(f"aggregate: {campaign.analysis_dir / 'aggregate.json'}")
            print(f"report: {report_path}")
        elif args.command == "optimize" and args.verb == "seed":
            from .optimize.codec import encode, seed_from_plugin

            text = encode(seed_from_plugin(args.plugin_root.resolve()))
            if args.out is None:
                sys.stdout.write(text)
            else:
                args.out.write_text(text)
                print(f"seed: {args.out}")
        elif args.command == "optimize" and args.verb == "prepare-interview":
            from .optimize.fixtures import build_interview_pristine

            fixture = build_interview_pristine(
                root,
                panther_repo=args.panther_repo.resolve(),
                template=args.template,
                template_commit=args.template_commit,
                toolchain=args.toolchain,
                name=args.name,
            )
            print(f"pristine: {fixture.pristine_dir}")
        elif args.command == "optimize" and args.verb == "run":
            return _optimize_run(args, root)
        elif args.command == "optimize" and args.verb == "apply":
            from .optimize.apply import apply as apply_candidate
            from .optimize.apply import (
                by_repository,
                diff_stat,
                targets,
                uncommitted_work,
            )
            from .render import TEMPLATE

            plugin_root = args.plugin_root.resolve()
            template_path = args.template.resolve() if args.template else TEMPLATE
            work = uncommitted_work(targets(plugin_root, template_path=template_path))
            if work.unchecked:
                print(
                    "not checked, in no git repository: "
                    + ", ".join(str(path) for path in work.unchecked)
                )
            if work.dirty and not args.force:
                raise ExperimentError(
                    "these files hold work nobody committed and would be "
                    f"overwritten: {', '.join(work.dirty)}; commit them, or "
                    "pass --force"
                )
            applied = apply_candidate(
                args.candidate.read_text(), plugin_root, template_path=template_path
            )
            # One diff per repository, for the same reason the guard asks each
            # one separately: a pathspec that leaves its repository is refused.
            written, _ = by_repository(applied.written)
            for repo, owned in written.items():
                print(diff_stat(repo, owned), end="")
            print(f"rendered: {applied.rendered_skill}")
            print(NOT_COMMITTED)
    except (ExperimentError, OSError) as error:
        _report(f"error: {error}")
        return 1
    return 0
