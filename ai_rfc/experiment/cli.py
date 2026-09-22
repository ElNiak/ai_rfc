"""The experiment instrument's command-line surface, behind both its doors.

``ai-rfc experiment`` mounts :func:`configure` and calls :func:`run`;
``python -m ai_rfc.experiment`` reaches the same two through :func:`main` over
:func:`build_standalone_parser`. The module door is not a legacy alias — the
server core's stage runs and the raw arm both invoke it, and they keep doing so
until CLI-3 moves them across.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ai_rfc import __version__
from ai_rfc.driver import DriverError, printable
from ai_rfc.driver.arms import ARMS
from ai_rfc.parser import Parser

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
from .workspace import migrate_draft as migrate_draft_workspace
from .workspace import prepare as prepare_workspace
from .workspace import reseal as reseal_workspace

if TYPE_CHECKING:
    from .config import Campaign
    from .ground_truth import GroundTruthReport
    from .judge import JudgeReport

#: Cluster rounds between consolidation rounds, read from the schema's declared
#: default for ``sessions.consolidate_every`` rather than restated here. The
#: same number configures a reconstruction and freezes into a campaign, and two
#: literals would let the operator-facing value and the campaign's drift apart
#: without anything saying so. It is the last of the three tiers
#: :func:`_consolidation_interval` resolves: ``--consolidate-every`` outranks
#: the ``sessions.consolidate_every`` of the ``recon.yaml`` given to
#: ``--config``, which outranks this declared default.
DEFAULT_CONSOLIDATE_EVERY: int = field_default("sessions.consolidate_every")

#: Where a run records the sessions appended to it after it finished. One JSON
#: object per line, in the run directory beside the transcript it explains.
APPENDED_FILE = "appended.jsonl"

#: Printed after every ``optimize apply``. The verb writes the working tree
#: and stops there, and a diff nobody was told to read is a diff that gets
#: committed unread.
NOT_COMMITTED = (
    "Nothing was committed; review the diff, then run tests/driver/test_render.py."
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

#: Seconds the whole-draft ``judge`` verb waits, and **not a measurement**.
#: The only timing this package has is the one above, and it is of a
#: different call in both of the ways that matter: that one grades a single
#: claim, this one reads a whole draft -- 166,554 bytes of it on the MARK
#: baseline, measured with ``wc -c`` -- and that one runs at ``low`` effort,
#: which is what makes 120 s fit it, while this one runs at ``high``. Either
#: difference alone would invalidate the budget: at the wrapper's own default
#: -- ``high``, per ``ClaudeCliCall.__init__`` -- the *short* call already
#: took 112 s.
#: So this is headroom, not a figure: roughly five times the one datum there
#: is, chosen on the asymmetry rather than on a model of the call. A budget
#: that is too short does not save the money -- the child has already spent
#: whatever it spent before the kill -- it loses the reply as well, while one
#: that is too long only delays a wedged call. Replace it with a measurement:
#: one real judge call against a real draft, before the pilot.
JUDGE_DRAFT_TIMEOUT_S = 600

#: Where ``judge`` writes one draft's grades and the conditions they were
#: given under, under the directory ``--out`` names.
JUDGE_REPORT_FILE = "judge.json"

#: Where ``ground-truth`` writes one draft's standing against the pinned
#: dataset, under the directory ``--out`` names. Named apart from the
#: judgement beside it because the two are different kinds of evidence: this
#: one consulted nothing.
GROUND_TRUTH_REPORT_FILE = "ground-truth.json"

#: What ``judge`` grades when ``--dimension`` names nothing. Four axes rather
#: than one overall mark: a single number cannot say whether a draft reads
#: badly or specifies too little, and those call for opposite revisions. They
#: are the prose properties the deterministic lint cannot reach — it counts
#: sections, citations and BCP 14 terms, none of which say whether a normative
#: sentence can be read two ways.
JUDGE_DIMENSIONS: tuple[str, ...] = (
    "structure",
    "precision",
    "completeness",
    "readability",
)

#: Seconds the version probe waits. A binary that reads its stdin rather than
#: answering ``--version`` would otherwise hold the verb open forever.
_VERSION_TIMEOUT_S = 30

#: What the judge's manifest records where a session reported nothing. Said in
#: words rather than left as an empty value, because an empty model id or an
#: empty skill list reads as a measurement that came back empty, and that is
#: the opposite claim: an absent ``slash_commands`` says the leak was not
#: measured, not that it was closed.
_NOT_REPORTED = "not reported"

#: The blinding regime the judge runs under, and it is not provisional. The
#: design spec framed the two residual leaks as lasting "until an API key is
#: provisioned"; this project never uses one, and ``--bare`` — the only flag
#: that stops ``CLAUDE.md`` being auto-discovered — refuses OAuth outright. So
#: there is no end date to wait for and the manifest must not imply one.
_JUDGE_BLINDING_REGIME = "oauth-permanent"

#: How the accepted leak was measured, carried beside the claim itself. A bare
#: boolean would record the claim without its provenance, turning an inherited
#: measurement into an asserted one.
#:
#: The difference in argv is named as a difference rather than as a list of
#: flags. The spec's probe ran ``--tools "" --strict-mcp-config --system-prompt
#: --exclude-dynamic-system-prompt-sections --model --output-format --verbose``;
#: :meth:`.optimize.claude_cli.ClaudeCliCall.argv` adds five to that and drops
#: one, and any enumeration here would be a second copy of a vector that is
#: free to move — with the reader unable to tell a flag nobody listed from a
#: flag that is not there.
_JUDGE_BLINDING_EVIDENCE = (
    "measured 2026-09-03 on claude 2.1.259 under the design spec's probe "
    "argv; this judge runs the different argv ClaudeCliCall.argv() documents, "
    "and no probe has re-measured the leak under it or under the "
    "claude_version recorded beside this field"
)


#: Evaluations one whole search round costs, as a multiple of the example
#: count: the seed over the selection set, the current candidate over a
#: minibatch, and the proposal over that same minibatch, with the minibatch
#: being the selection set. A rehearsal given less than this never scores a
#: proposal at all and reports a converged search rather than a starved one.
_REHEARSAL_ROUNDS = 3


def _report(message: str) -> None:
    """Write a diagnostic to stderr, as one printable line.

    The harness's twin of :func:`ai_rfc.lifecycle.common.report`, which it may
    not call: ``experiment`` sits above ``driver`` and beside ``lifecycle``,
    and reaching across for one line would invert that. Escaped here rather
    than at each call site, because the values these lines carry — a campaign
    id, a model's own text, a caught error's message — are the operator's and
    the agent's, and one carrying a line break forges a second diagnostic
    beneath the first.

    Args:
        message: The line to print.
    """
    print(printable(message), file=sys.stderr)


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
        # Escaped, not merely echoed: argparse prints an ArgumentTypeError to
        # stderr through its own formatting, which routes through neither
        # ``lifecycle.common.report`` nor anything else that escapes, and a
        # newline inside an arm name forged a second line under the usage line
        # argparse prints above it. The value is already refused here, so the
        # message is the only thing left to make safe — which is why this
        # escapes where ``lifecycle/run/cli.py``'s ``_bound``, whose value
        # cannot be checked for membership at all, refuses outright.
        raise argparse.ArgumentTypeError(
            f"unknown arm(s) {', '.join(printable(arm) for arm in unknown)}; "
            f"known arms are {', '.join(ARMS)}"
        )
    if len(set(arms)) != len(arms):
        raise argparse.ArgumentTypeError(f"repeated arm in {value!r}")
    return arms


def _campaign_id(value: str) -> str:
    """Validate a campaign id where it is still one token: at the parser.

    The id names a directory — ``config.py:356`` joins it as ``root /
    "campaigns" / campaign_id`` — and an **absolute** value replaces the root
    of that join outright: ``Path('/r/campaigns') / '/tmp/evil'`` is
    ``/tmp/evil``. The frozen-once guard on the next line then tests a
    directory under somebody else's root and finds it absent, so it permits
    the freeze; a ``..`` walks out of the campaigns root the operator named
    just as quietly. Neither is a thing the sink can discover after the join,
    which is why the check is here.

    Shape, not membership, and deliberately: unlike a cluster id, a campaign
    id names a directory that does not exist yet, so there is no set to be a
    member of.

    Args:
        value: The raw ``--id`` string.

    Returns:
        ``value`` unchanged.

    Raises:
        argparse.ArgumentTypeError: If it is not one segment of that alphabet.
    """
    # Function-local, like every other reach into `config` from this module:
    # the parser is built for `--help` and for `--version`, and `config`
    # pulls the render, toolchain and driver-session machinery behind it.
    from .config import CAMPAIGN_ID, CAMPAIGN_ID_REASON

    if not CAMPAIGN_ID.fullmatch(value):
        # Escaped, not merely echoed, for the reason ``_arms`` records:
        # argparse prints this through its own formatting, which routes
        # through nothing that escapes, so a newline in the value forges a
        # second stderr line in the shape of the usage line printed above it.
        raise argparse.ArgumentTypeError(
            f"campaign id '{printable(value)}' {CAMPAIGN_ID_REASON}"
        )
    return value


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


def _consolidation_interval(flag: int | None, config_path: Path | None) -> int:
    """Resolve the consolidation cadence a campaign freezes.

    Three tiers, most specific first: the flag, then the
    ``sessions.consolidate_every`` of the ``recon.yaml`` the campaign
    initialises from, then the schema's declared default. A config that
    declares no ``sessions`` block configures no cadence, so it falls through
    to the default rather than to a zero that would consolidate after every
    cluster.

    Args:
        flag: ``--consolidate-every``, or None when it was not given. 0 is a
            value rather than an absence — it disables mid-sweep rounds — so
            the tiers are separated by ``is None``, not by truthiness.
        config_path: ``--config``, or None when it was not given.

    Returns:
        The interval to freeze into the campaign.

    Raises:
        ExperimentError: If the config cannot be read or does not validate.
    """
    if flag is not None:
        return flag
    if config_path is None:
        return DEFAULT_CONSOLIDATE_EVERY
    try:
        config = load_config(config_path.resolve())
    except ConfigError as error:
        raise ExperimentError(str(error)) from None
    if config.sessions is None:
        return DEFAULT_CONSOLIDATE_EVERY
    return config.sessions.consolidate_every


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
            # The union argv was ruled for the judge alone, and this loop's
            # own vector is the one measured on 2.1.260, so it keeps it. The
            # init assertion is not part of that gate and holds here too --
            # which leaves the proposer carrying the guard without the two
            # flags meant to prevent what the guard refuses, so the arm
            # likeliest to mount a server is the one lacking the flag that
            # would stop it. Kept deliberately: a proposer session that
            # mounted one is not a proposer this experiment is studying, and
            # changing this vector was outside U1's ruling. The cost is a
            # failure mode nothing else here has -- per ClaudeCliError, a
            # proposer that raises stops the optimization, so a mid-loop
            # refusal ends the run rather than scoring anything.
            reflection_lm = ClaudeCliCall(
                claude_bin,
                profile,
                proposer,
                cwd=calls_dir,
                effort=args.effort,
                timeout_s=args.timeout_s,
                union_argv=False,
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
            campaign, names one that is not shaped like a run id at all, the
            append went unacknowledged, or that run has no workspace.
    """
    from ai_rfc.driver.consolidation import consolidation_due
    from ai_rfc.driver.session import EVENTS_FILE
    from ai_rfc.driver.stream import result_events, salvage_stream

    from . import per_cluster
    from .campaign_runs import checked_run_id

    # `RESULT_FILE` named from the runner, not from `.optimize.run`, which
    # defines a constant of the same name for a different file.
    from .runner import RESULT_FILE, run_ref

    if only is None or len(only) != 1:
        raise ExperimentError(
            "--task consolidation runs one round against one run; name that "
            "run with --only <run id>"
        )
    # The same validator the sweep applies, and for the same two sinks: below,
    # the id is joined into a run directory by `run_ref` and interpolated into
    # every line this function reports. `split_run_id`'s membership check is not
    # it — `run_order` is itself read out of campaign.json, so a tampered order
    # satisfies it. One path sanitising while its sibling does not is worse than
    # neither doing it, because a reader assumes the pair agree.
    ref = run_ref(campaign, checked_run_id(only[0]))
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
    # Both counted before the round, because the round appends to this same
    # file. The result-event count is what the session is told to skip: charged
    # from zero it would report the whole finished run's cost as its own.
    before = events_path.read_text(errors="replace") if events_path.exists() else ""
    lines_before = len(before.splitlines())
    seen_before = len(result_events(salvage_stream(before)[0]))
    recorded, result = per_cluster._run_consolidation(
        campaign,
        ref,
        due,
        # The whole cap, not a remainder: no sweep surrounds this round, so
        # there is no earlier session of this invocation to have spent any of
        # it. A run made of several sessions is the case that needs a
        # remainder, and it has one.
        budget_usd=campaign.budget_usd,
        timeout_s=campaign.timeout_s,
        seen=seen_before,
        at_end=True,
        report=_report,
    )
    timed_out = result.timed_out
    _record_append(ref.run_dir, due, lines_before, recorded, timed_out)
    print(
        f"{ref.run_id}: consolidation {due.ordinal:02d} "
        f"recorded={recorded} timed_out={timed_out}"
    )
    print(f"appended past line {lines_before} of {events_path}; see {APPENDED_FILE}")
    # Said out loud because the opposite was true until #61 was fixed, and an
    # operator who ran a round by hand had no way to tell from here whether it
    # would be paid for in the figures. `analyze` merges the transcript's
    # result events; `result.json`, written when the run first finished, is no
    # longer what the cost is read from.
    print(
        f"`experiment analyze --only {ref.run_id}` counts this round's cost: "
        f"it merges the result events in {events_path.name}, not the "
        f"{RESULT_FILE} written when the run first finished"
    )
    return 0 if recorded else 1


def _claude_version(claude_bin: str) -> str:
    """What the binary says its version is, or that it said nothing.

    A second copy of :func:`.preflight.run_preflight`'s probe rather than a
    call into it, because the two want different things from a silent binary:
    that one records the empty string beside a spike report, and an empty
    version in a manifest reads as measured-and-blank. This one also closes
    stdin and takes a timeout, so a binary that reads its input rather than
    answering the flag cannot hold the verb open.

    Args:
        claude_bin: The binary to ask.

    Returns:
        The first line it printed, or :data:`_NOT_REPORTED` when it could not
        be run, timed out, exited non-zero, or printed nothing.
    """
    try:
        probe = subprocess.run(
            [claude_bin, "--version"],
            input="",
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _NOT_REPORTED
    lines = probe.stdout.strip().splitlines()
    if probe.returncode != 0 or not lines:
        return _NOT_REPORTED
    return lines[0].strip()


def _judge_manifest(
    transport: Any, *, dimensions: tuple[str, ...], claude_bin: str
) -> dict[str, Any]:
    """The conditions one judging call was made under, so its scores can be read.

    What the session reported comes off its init event and never off the flags
    that asked for it: a probe for this design had a model claim tools its
    argv had not given it, so what was requested is not evidence of what ran.
    A manifest taking the model from ``--model`` would name the model
    requested rather than the one that answered.

    Nothing absent is defaulted, for the reason the transport does not default
    its own init: an absent ``slash_commands`` says the leak was not measured,
    while a ``[]`` in its place says it was measured and found closed, and one
    value for the two leaves a reader misinformed rather than uninformed.

    Args:
        transport: The call the grades were asked for through, after it was
            made. Its ``last_init``, ``spend_usd`` and ``unpriced_calls`` are
            what this reads; a call that raised has cleared the first and
            kept the other two, which is why a refused call still prices.
        dimensions: The dimensions the call asked for.
        claude_bin: The binary that was launched, asked for its own version.

    Returns:
        The manifest.
    """
    from .judge import RUBRIC

    init = transport.last_init if isinstance(transport.last_init, dict) else {}
    return {
        "argv": transport.argv(),
        "model": init["model"] if "model" in init else _NOT_REPORTED,
        "claude_version": _claude_version(claude_bin),
        "rubric_sha256": hashlib.sha256(RUBRIC.encode()).hexdigest(),
        "dimensions": list(dimensions),
        "blinding": {
            "regime": _JUDGE_BLINDING_REGIME,
            "claude_md_loaded": True,
            "slash_commands": (
                init["slash_commands"] if "slash_commands" in init else _NOT_REPORTED
            ),
            "evidence": _JUDGE_BLINDING_EVIDENCE,
        },
        # Together, never one alone: a spend of 0.0 says either "nothing was
        # billed" or "nothing was measured", and the count is what tells them
        # apart. The running total rather than the last call's figure, because
        # it is the one that survives a call that was billed and then refused.
        "spend_usd": transport.spend_usd,
        "unpriced_calls": transport.unpriced_calls,
    }


def _write_judgement(
    out: Path,
    *,
    draft: Path,
    manifest: dict[str, Any],
    report: JudgeReport | None,
    error: str | None,
) -> Path:
    """Write one judgement and the conditions it was produced under.

    Args:
        out: The directory to write into; created if it does not exist.
        draft: The draft that was graded.
        manifest: What :func:`_judge_manifest` produced.
        report: The judge's report, or ``None`` when the call or the reply was
            refused. Every field it would have filled is written as null
            rather than as an empty one, so a reader never reads "graded and
            scored nothing" where nothing was graded.
        error: Why there is no report, or ``None`` when there is one. Present
            on both paths so a reader has one key to look at either way.

    Returns:
        The file written.
    """
    payload = {
        "draft": str(draft),
        "judged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scores": None if report is None else dict(report.scores),
        "quotes": None if report is None else list(report.quotes),
        # ``None`` survives as null: it means the quotes were never checked,
        # which is a different claim from an empty list's "checked, all found".
        "unverified": (
            None
            if report is None or report.unverified is None
            else list(report.unverified)
        ),
        "error": error,
        "manifest": manifest,
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / JUDGE_REPORT_FILE
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _judge_run(args: argparse.Namespace, root: Path) -> int:
    """Grade one draft, and record what its grades were produced under.

    A non-empty ``unverified`` ships the scores rather than refusing them,
    because :data:`~.judge.RUBRIC` tells the model that a quotation it cannot
    find is *returned alongside the grades*, not that the reply is invalid;
    refusing it here would punish a model for answering the contract it was
    given. But a judge that cites text the draft does not contain has not read
    the draft it graded, so the finding must be impossible to miss: it decides
    the exit code, and the quote count is printed on every path, including the
    clean one, so an absent finding is never what a reader has to rely on.

    Args:
        args: The parsed ``judge`` arguments.
        root: The runs root, which the profile defaults under.

    Returns:
        0 when every quote the judge gave was checked and found in the body it
        was shown, and 3 otherwise — a gate said no, and as with ``preflight``
        the evidence is written before the code is returned.

    Raises:
        ExperimentError: Whatever the call or the reply raises, after the
            manifest has been written: a refused call is a billed one, and its
            cost is the figure that would otherwise be lost with it.
    """
    from .judge import judge_draft, judge_transport

    draft = args.draft.resolve()
    out = args.out.resolve()
    dimensions = tuple(args.dimensions or JUDGE_DIMENSIONS)
    profile = (args.profile_dir or profile_dir(root)).resolve()
    # Read before the transport is built, so an unreadable draft costs no
    # temporary directory and no manifest for a call nobody made.
    text = draft.read_text()
    transport = judge_transport(
        args.claude_bin,
        profile,
        args.model,
        effort=args.effort,
        timeout_s=args.timeout_s,
    )

    def record(
        report: JudgeReport | None, error: str | None
    ) -> tuple[Path, dict[str, Any]]:
        manifest = _judge_manifest(
            transport, dimensions=dimensions, claude_bin=args.claude_bin
        )
        return (
            _write_judgement(
                out, draft=draft, manifest=manifest, report=report, error=error
            ),
            manifest,
        )

    try:
        report = judge_draft(text, transport, dimensions=dimensions)
    except ExperimentError as error:
        path, _ = record(None, str(error))
        _report(f"conditions recorded: {path}")
        raise
    path, manifest = record(report, None)
    print(f"judge: {path}")
    print(
        "scores: " + "  ".join(f"{name}={report.scores[name]}" for name in dimensions)
    )
    # The model the session reported, not the one asked for, and said here as
    # well as written: what a reader takes the scores to be worth turns on it.
    # Through ``printable`` for the same reason the quotes below are: this is
    # text the binary reported, not text this verb chose.
    print(f"model: {printable(str(manifest['model']))}")
    if report.unverified is None:
        print("quotes: not checked")
        _report("finding: the judge's quotes were never checked against the draft")
        return 3
    found = len(report.quotes) - len(report.unverified)
    print(f"quotes: {found} of {len(report.quotes)} verified")
    if report.unverified:
        _report(
            f"finding: {len(report.unverified)} of {len(report.quotes)} quotes "
            "are not in the draft the judge was shown"
        )
        for quote in report.unverified:
            # Through ``printable``: the quote is the model's own text, and an
            # unescaped line ending in it would reach stderr as several lines,
            # any of which can be spelled to read like this verb's own.
            _report(f"  {printable(quote)}")
        return 3
    return 0


def _write_ground_truth(
    out: Path,
    *,
    draft: Path,
    dataset: dict[str, Any],
    report: GroundTruthReport,
) -> Path:
    """Write one draft's score and everything needed to read the numbers.

    The two ratios are meaningless alone. ``recall`` needs the count it was
    taken over, ``claim_accuracy`` needs the count of claims the draft made,
    and both need the pin the draft was scored against and the window the
    matcher read proximity in -- two scores taken at different widths are not
    comparable, and nothing else in the file would say so. The entries left
    out of the denominator travel with the reason each one was left out, for
    the same reason: a count alone cannot say whether an entry was unscorable
    by any matcher or only by this one.

    Args:
        out: The directory to write into; created if it does not exist.
        draft: The draft that was scored.
        dataset: The dataset document the entries came from.
        report: What :func:`~.ground_truth.score_draft` produced.

    Returns:
        The file written.
    """
    from .ground_truth import DATASET, NEARBY_CHARS

    payload = {
        "draft": str(draft),
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "name": DATASET,
            "repository": dataset["repository"],
            "commit": dataset["commit"],
        },
        "nearby_chars": NEARBY_CHARS,
        "scored": report.scored,
        "excluded": list(report.excluded),
        # Per entry, because the two reasons are different facts about the
        # dataset and a reader who sees only a count cannot tell them apart:
        # one entry no matcher over prose could ever score, the other one a
        # matcher that read syntax rather than a character window could.
        "excluded_because": dict(report.excluded_because),
        "matched": list(report.matched),
        "attempted": list(report.attempted),
        "mismatched": list(report.mismatched),
        "missed": list(report.missed),
        "recall": report.recall,
        # ``None`` survives as null, and it is the point of the axis rather
        # than an omission: a draft that attempted nothing has an unmeasured
        # accuracy, which 0.0 and 1.0 both misreport.
        "claim_accuracy": report.claim_accuracy,
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / GROUND_TRUTH_REPORT_FILE
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _ground_truth_run(args: argparse.Namespace) -> int:
    """Score one draft against the pinned dataset, asking nothing.

    No ``root``, unlike every other verb that writes evidence: this one reads
    a draft, reads a dataset out of its own package and writes where ``--out``
    says. It reaches no runs root, no profile and no model — that independence
    is the axis, not an economy.

    Args:
        args: The parsed ``ground-truth`` arguments.

    Returns:
        0. A low recall is a measurement about a draft, not a gate that failed,
        and returning 3 for one would put a finding where ``preflight`` and
        ``judge`` put a refusal.

    Raises:
        ExperimentError: The draft is not UTF-8 text, so there is no prose to
            score. Raised rather than left as the ``UnicodeDecodeError`` it
            came from, which is a ``ValueError`` and would escape ``run``'s
            handler as a traceback.
    """
    from .ground_truth import load_dataset, score_draft

    draft = args.draft.resolve()
    try:
        text = draft.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        # A ValueError, so ``run``'s handler does not catch it and the
        # operator would meet a traceback where every other bad input to this
        # package produces one line and exit 1.
        raise ExperimentError(
            f"{draft} is not UTF-8 text ({error}); a draft is prose to search, "
            "and bytes that do not decode state nothing that could be scored"
        ) from error
    dataset = load_dataset()
    report = score_draft(text, dataset["entries"])
    path = _write_ground_truth(
        args.out.resolve(), draft=draft, dataset=dataset, report=report
    )
    print(f"ground-truth: {path}")
    print(f"recall: {len(report.matched)} of {report.scored} scored")
    if report.claim_accuracy is None:
        # Said in words on the terminal too: a reader who sees only this line
        # must not come away with a number the draft never earned.
        print("claim accuracy: not measured; the draft attempted no entry")
    else:
        print(f"claim accuracy: {len(report.matched)} of {len(report.attempted)}")
    # Grouped by reason rather than counted in one lump: "whose value is not a
    # number" was true of every excluded entry when there was one reason, and
    # false of four of six the moment a second was added.
    counts: dict[str, int] = {}
    for _, reason in report.excluded_because:
        counts[reason] = counts.get(reason, 0) + 1
    print(f"excluded: {len(report.excluded)} entries nothing could score")
    for reason, count in counts.items():
        print(f"  {count} because {reason}")
    return 0


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Runs root (default: AI_RFC_EXPERIMENTS_ROOT or ~/ai-rfc-experiments).",
    )


def configure(parser: argparse.ArgumentParser) -> None:
    """Add the instrument's arguments to ``parser``.

    Args:
        parser: Either the root door's subparser for ``experiment`` or the
            standalone parser :func:`build_standalone_parser` builds; both must
            carry the same arguments, so both are configured here. The
            instrument's own second level of subparsers derives its ``prog``
            from this one, so a leaf's usage line names the whole path the
            operator typed through whichever door they used.
    """
    parser.description = "AI+MCP vs AI+CLI experiment harness over the ai_rfc plugin."
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
    prepare.add_argument(
        "--forge-snapshot",
        type=Path,
        default=None,
        help="A snapshot directory to adopt instead of fetching one. Cluster "
        "ids follow the snapshot's pull rows, so rebuilding a window that must "
        "match an earlier reconstruction means carrying that capture across "
        "rather than refetching a forge that has moved on.",
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
        "--id",
        required=True,
        type=_campaign_id,
        help="Campaign identifier; names its directory.",
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
        "--config",
        type=Path,
        default=None,
        help=(
            "recon.yaml to take the consolidation cadence from. Only its "
            "sessions.consolidate_every is read, and only when "
            "--consolidate-every is not given. The campaign itself is built "
            "from --baseline; nothing checks that this config is the one that "
            "baseline was prepared from, and the record keeps the interval "
            "without naming where it came from."
        ),
    )
    init.add_argument(
        "--consolidate-every",
        type=_interval,
        default=None,
        help=(
            "Cluster rounds between consolidation rounds; 0 disables mid-sweep "
            "ones. This outranks --config's sessions.consolidate_every, which "
            "outranks the schema default of "
            f"{DEFAULT_CONSOLIDATE_EVERY} used when neither is given."
        ),
    )

    # ``run_cmd``, not ``run``: this module now has a module-level ``run``,
    # which the door calls, and a local of that name inside the function that
    # builds the parser would shadow it for anything added here later.
    run_cmd = commands.add_parser(
        "run", help="Launch pending runs in the frozen order."
    )
    run_cmd.add_argument("campaign", type=Path, help="Campaign directory.")
    run_cmd.add_argument("--only", default=None, help="Comma-separated run ids.")
    run_cmd.add_argument(
        "--task",
        choices=("sweep", "consolidation"),
        default="sweep",
        help=(
            "Run the window, or one consolidation round against the workspace "
            "of the single run named by --only, as it stands."
        ),
    )
    run_cmd.add_argument(
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
    analyze.add_argument(
        "--build",
        action="store_true",
        help=(
            "Also build the draft each run last tagged, and report the "
            "result. Off by default because it clones the draft repository "
            "and runs make once per run; output lands in "
            "analysis/<run>/draft-build, never inside the run directory."
        ),
    )

    judge = commands.add_parser(
        "judge",
        help="Grade one draft's prose with a blinded model, recording what "
        "the grades were produced under.",
    )
    _add_root(judge)
    judge.add_argument(
        "draft",
        type=Path,
        help="The kramdown-rfc draft to grade. Its front matter and its "
        "harness citations come off before any judge sees it.",
    )
    judge.add_argument(
        "--out",
        type=Path,
        required=True,
        help=f"Directory to write {JUDGE_REPORT_FILE} into; created if absent. "
        "Named rather than defaulted: a judgement is evidence about one draft, "
        "and a shared default would let the next run overwrite it.",
    )
    judge.add_argument(
        "--dimension",
        dest="dimensions",
        action="append",
        default=None,
        help="A dimension to grade, repeatable. Default: "
        + ", ".join(JUDGE_DIMENSIONS)
        + ".",
    )
    judge.add_argument(
        "--model",
        type=_model,
        default=DEFAULT_MODEL,
        help="Model to ask for (default: %(default)s). Which model answered is "
        "recorded separately, off the session's own init event.",
    )
    judge.add_argument(
        "--claude-bin",
        default="claude",
        help="Claude Code binary to launch (default: %(default)s).",
    )
    judge.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help="The authenticated profile the call runs under (default: "
        "<root>/profile).",
    )
    judge.add_argument(
        "--effort",
        choices=EFFORTS,
        default="high",
        help="Reasoning effort for the grading call (default: %(default)s). "
        f"Higher than the per-claim judge's {JUDGE_EFFORT}: this one reads a "
        "whole draft before it grades one.",
    )
    judge.add_argument(
        "--timeout-s",
        type=int,
        default=JUDGE_DRAFT_TIMEOUT_S,
        help="Seconds before the grading call is killed (default: %(default)s). "
        f"Higher than the per-claim judge's {JUDGE_TIMEOUT_S} and not measured: "
        "no whole-draft call has been timed, so the default is headroom.",
    )

    # ``ground_truth_cmd``, not ``ground_truth``: the latter would shadow the
    # module this verb imports, the way ``run_cmd`` above avoids shadowing
    # ``run``.
    ground_truth_cmd = commands.add_parser(
        "ground-truth",
        help="Score one draft against the pinned dataset, consulting no model.",
    )
    ground_truth_cmd.add_argument(
        "draft",
        type=Path,
        help="The draft to score. It is read as prose and searched as written; "
        "nothing is stripped from it and nothing is sent anywhere.",
    )
    ground_truth_cmd.add_argument(
        "--out",
        type=Path,
        required=True,
        help=f"Directory to write {GROUND_TRUTH_REPORT_FILE} into; created if "
        "absent. Named rather than defaulted, as for `judge`: a score is "
        "evidence about one draft, and a shared default would let the next "
        "draft overwrite it.",
    )

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


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.experiment`` uses.

    Returns:
        A parser carrying the instrument's own ``prog`` and ``--version``, over
        the arguments the root door mounts through :func:`configure`.
    """
    parser = Parser(prog="ai-rfc experiment")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc experiment {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Run one harness command.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 when the harness refused or an input was unusable, and
        3 when a gate said no — ``preflight`` not reaching "go", the parity
        suite failing, or ``judge`` finding a quote its judge gave that the
        draft it was shown does not contain *or* finding that the quotes were
        never checked at all. Those last two are different claims and both
        return 3, because a report nobody checked must not inherit the code
        that means checked-and-all-found. All three write their evidence
        before they return it: the code reports the finding, it does not
        replace the record. 2 is left to ``argparse``, as everywhere else in this
        package: a caller must be able to tell a mistyped flag from a gate that
        must stop a campaign, and the two call for opposite responses.
    """
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
            from ai_rfc.driver.render import write_plugin_skill

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
                forge_snapshot=(
                    None
                    if args.forge_snapshot is None
                    else args.forge_snapshot.resolve()
                ),
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
            # Before the parity suite: an unreadable config is a refusal the
            # operator should get in a second, not after a minutes-long run.
            consolidate_every = _consolidation_interval(
                args.consolidate_every, args.config
            )
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
                    plugin_root=plugin_dir,
                    python=args.python,
                    claude_bin=args.claude,
                    parity=parity,
                    session_mode=args.session_mode,
                    toolchain=toolchain,
                    consolidate_every=consolidate_every,
                )
            )
            print(f"campaign: {campaign.dir}")
            print(f"run order: {' '.join(campaign.run_order)}")
            print(f"parity: {parity}")
            if parity is not None and not parity["passed"]:
                _report("finding: parity suite FAILED - stop-ship per protocol")
                return 3
        elif args.command == "run":
            from .campaign_runs import launch_pending
            from .config import load_campaign

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
            from .quality import build_tally
            from .report import render_report

            campaign = load_campaign(args.campaign.resolve())
            audit_campaign(campaign)
            aggregate = analyze_campaign(campaign, build=args.build)
            report_path = campaign.analysis_dir / "report.md"
            report_path.write_text(render_report(aggregate))
            print(f"aggregate: {campaign.analysis_dir / 'aggregate.json'}")
            print(f"report: {report_path}")
            # Only when a build was asked for: without the flag every run is
            # `not requested`, and a line saying so is noise. The exit code is
            # deliberately not touched. R31 made a refused build a status and
            # not an abort, and failing the verb on it would put that same
            # poisoning back at the exit-code level, where one damaged run
            # would again degrade the signal for the whole campaign.
            if args.build:
                print(f"builds: {build_tally(aggregate['runs'])}")
        elif args.command == "judge":
            return _judge_run(args, root)
        elif args.command == "ground-truth":
            return _ground_truth_run(args)
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
                template=args.template,
                template_commit=args.template_commit,
                toolchain=args.toolchain,
                name=args.name,
            )
            print(f"pristine: {fixture.pristine_dir}")
        elif args.command == "optimize" and args.verb == "run":
            return _optimize_run(args, root)
        elif args.command == "optimize" and args.verb == "apply":
            from ai_rfc.driver.render import TEMPLATE

            from .optimize.apply import apply as apply_candidate
            from .optimize.apply import (
                by_repository,
                diff_stat,
                targets,
                uncommitted_work,
            )

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
    except (ExperimentError, DriverError, OSError) as error:
        _report(f"error: {error}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.experiment``).

    Kept alongside the mount rather than replaced by it: the server core's
    stage runs and the raw arm both invoke ``python -m ai_rfc.experiment``, and
    they keep doing so until CLI-3 moves them onto the door.

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code, per :func:`run`.
    """
    return run(build_standalone_parser().parse_args(argv))
