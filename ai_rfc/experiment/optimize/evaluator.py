"""One candidate and one example in, one number and its evidence out.

A search backend proposes text and needs a score back; everything between is
here. Each call materializes the proposal as a plugin root, freezes a campaign
on it, launches one run through the harness, audits and analyzes the
transcript, and scores the workspace the run left behind. A campaign is frozen
once, so every evaluation gets its own — nothing is ever re-audited or
re-analyzed in place.

Two failures are told apart deliberately. A proposal the codec rejects is the
backend's own doing and is reported as such, with the list of broken guards it
can propose against. A harness fault is not: a busy profile or a launch that
never mounted its tools says nothing about the text, so it is retried once and,
when it keeps happening, stops the optimization rather than teaching it that
the current proposal is worthless. A judge that answered none of its calls is
counted as the same kind of failure, not as a relevance term of zero.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

import yaml

from ai_rfc.draft.build import BuildError, BuildReport, build, load_toolchain
from ai_rfc.draft.gate import GateError, load_revisions

from .. import ExperimentError
from ..audit import audit_run
from ..config import Campaign, CampaignConfig, init_campaign
from ..driver import launch_pending
from ..metrics import analyze_run, window_clusters
from ..workspace import REFCACHE_DIR
from .codec import Bundle, CodecError, decode, frontmatters_from_plugin, materialize
from .fixtures import InterviewFixture
from .judge import JudgeError
from .scoring import (
    ZERO_CODEC,
    ZERO_HARNESS,
    Judge,
    Score,
    Weights,
    score_interview,
    score_loop,
)

#: The one run every campaign of an optimization holds: arm A, repeat 1.
RUN_ID = "A1"
PLUGINS_DIR = "plugins"
CAMPAIGNS_DIR = "campaigns"
#: Written inside a materialized plugin root so the next evaluation of the
#: same proposal can tell a complete tree from an interrupted one.
STAMP_FILE = "candidate.sha256"
#: Where the default build puts its scratch clone, beside the run's workspace
#: rather than inside it: the workspace is what the score is computed from.
BUILD_DIR = "draft-build"


@dataclass(frozen=True)
class LoopExample:
    """One reconstruction cluster a candidate is measured on.

    Attributes:
        id: Names this example in the info dict and in the backend's records.
        pristine_dir: The prepared workspace every run copies from.
        cluster_id: The single in-window cluster the run must process.
        budget_usd: The per-run cost cap, and the divisor of the reported
            cost efficiency.
    """

    id: str
    pristine_dir: Path
    cluster_id: str
    budget_usd: float = 4.0
    kind: ClassVar[str] = "loop"


@dataclass(frozen=True)
class InterviewExample:
    """One author interview a candidate is measured on.

    Attributes:
        id: Names this example in the info dict and in the backend's records.
        fixture: The planted workspace and the answers it holds.
        budget_usd: The per-run cost cap.
    """

    id: str
    fixture: InterviewFixture
    budget_usd: float = 2.0
    kind: ClassVar[str] = "interview"


Example = LoopExample | InterviewExample


@dataclass(frozen=True)
class EvaluatorSettings:
    """Everything an evaluation needs that does not change between them.

    Attributes:
        root: Every campaign, and every materialized plugin, lands under here.
        profile_dir: The authenticated ``CLAUDE_CONFIG_DIR``, shared by every
            run so one login serves the whole optimization.
        python: Interpreter the runs' substrate shim executes.
        claude_bin: The agent binary to launch.
        model: Model every run is launched against.
        effort: Reasoning effort every run is launched with.
        timeout_s: Wall-clock cap on one run.
        panther_repo: Recorded in each campaign, and the root relative paths
            resolve against.
        toolchain: The verified ``toolchain.json`` every campaign records.
        source_plugin_root: The real plugin. Frontmatters and every file the
            candidate does not carry are copied from it; it is never written.
        seed: The bundle the optimization started from, which bounds how far
            a proposal may drift.
        judge: Rates each anchored claim against the code it points at.
        weights: How the loop's graded terms trade off.
        build: Builds one run's draft; ``None`` uses
            :func:`draft_build_report`.
        pre_launch: Called with the frozen campaign before its run is
            launched, for whatever the caller must plant in the profile.
        log: Receives one line per progress event and per harness fault.
        max_consecutive_harness_failures: How many evaluations may fault twice
            in a row before the optimization is stopped.
    """

    root: Path
    profile_dir: Path
    python: str
    claude_bin: str
    model: str
    effort: str
    timeout_s: int
    panther_repo: Path
    toolchain: Path | None
    source_plugin_root: Path
    seed: Bundle
    judge: Judge
    weights: Weights = Weights()
    build: Callable[[Campaign, Path], BuildReport | None] | None = None
    pre_launch: Callable[[Campaign, Example], None] | None = None
    log: Callable[[str], None] = print
    max_consecutive_harness_failures: int = 2


class EvaluatorAbort(ExperimentError):
    """Raised when the harness keeps faulting and the run should stop.

    A zero the backend reads as a verdict on the proposal is worse than no
    result at all when the cause was infrastructure, so a run of consecutive
    double faults ends the optimization instead of feeding it noise.
    """


def campaign_id_for(counter: int, example: Example, candidate_sha: str) -> str:
    """The directory name one evaluation's campaign is frozen under.

    Args:
        counter: A number that never repeats within one root.
        example: The example being evaluated.
        candidate_sha: The candidate's full digest.

    Returns:
        The campaign id.
    """
    return f"e{counter:04d}-{example.kind}-{candidate_sha[:8]}"


def _latest_tag(workspace: Path) -> str:
    """The highest-numbered revision tag a run recorded, or ``HEAD``."""
    try:
        entries = load_revisions(workspace / "revisions.yaml")
    except (GateError, OSError, yaml.YAMLError):
        return "HEAD"
    return max(entries, key=lambda entry: entry.number).tag if entries else "HEAD"


def draft_build_report(
    campaign: Campaign, workspace: Path, *, log: Callable[[str], None] = print
) -> BuildReport | None:
    """Build one run's draft at the last revision it tagged.

    The working tree is never built: a run that tagged nothing is built at
    ``HEAD``, which is the last thing it committed.

    Every ``OSError`` is caught, not only a missing tool, so a pilot whose
    build fails on a full disk, an unwritable scratch directory or a copy it
    could not make scores this term zero and logs the reason rather than
    stopping the optimization. The reason is the only place that shows.

    Args:
        campaign: The campaign whose frozen toolchain record is used.
        workspace: The run's final workspace.
        log: Receives the reason when a build could not start.

    Returns:
        The report, or None when the campaign froze no toolchain or the build
        could not start.
    """
    if campaign.toolchain is None:
        return None
    sealed = workspace / REFCACHE_DIR
    try:
        return build(
            workspace / "draft",
            toolchain=load_toolchain(Path(campaign.toolchain)),
            out=workspace.parent / BUILD_DIR,
            ref=_latest_tag(workspace),
            refcache=sealed if sealed.is_dir() else None,
        )
    except (BuildError, OSError) as error:
        # OSError as well as BuildError: `build` invokes the record's
        # executables through a bare `subprocess.run`, so a record naming a
        # tool that is not installed raises `FileNotFoundError` from there.
        # The caller scores outside the harness-fault wrapper, so an exception
        # escaping here would end a whole optimization over a term worth a
        # fifth of one evaluation.
        log(f"{campaign.id}: the draft did not build: {error}")
        return None


def _campaigns_already_frozen(root: Path) -> int:
    """How many evaluation campaigns this root already holds."""
    campaigns = root / CAMPAIGNS_DIR
    if not campaigns.is_dir():
        return 0
    return sum(1 for path in campaigns.glob("e*") if path.is_dir())


def _pristine_dir(example: Example) -> Path:
    return (
        example.fixture.pristine_dir
        if isinstance(example, InterviewExample)
        else example.pristine_dir
    )


def _harness_fault(
    analysis: dict[str, Any], example: Example, workspace: Path
) -> str | None:
    """Why this run's numbers do not describe the candidate, or None.

    A run that never mounted its arm's tools is not a weaker candidate; it is
    an unmeasured one. Neither is a workspace whose window does not hold the
    cluster the example names — the score would be computed over a cluster
    nobody asked for.
    """
    if analysis.get("audit") is None:
        return "the run produced no audit record"
    surface = analysis.get("surface") or {}
    if not surface.get("intact"):
        return (
            f"the run never presented its arm's tool surface "
            f"(mcp_servers={surface.get('mcp_servers')})"
        )
    if isinstance(example, LoopExample):
        in_window = [row["id"] for row in window_clusters(workspace)]
        if in_window != [example.cluster_id]:
            return (
                f"the window holds {in_window}, not the single cluster "
                f"{example.cluster_id!r} this example scores"
            )
    return None


class Evaluator:
    """One fresh campaign, run, audit, analysis and score per call."""

    def __init__(self, settings: EvaluatorSettings) -> None:
        """Read the frontmatters and take up the campaign counter where it is.

        Args:
            settings: What every evaluation shares.
        """
        self._settings = settings
        self._frontmatters = frontmatters_from_plugin(settings.source_plugin_root)
        self._issued = _campaigns_already_frozen(settings.root)
        self._evaluations = 0
        self._consecutive_faults = 0

    @property
    def settings(self) -> EvaluatorSettings:
        """What every evaluation shares, as this evaluator was built with."""
        return self._settings

    @property
    def evaluations(self) -> int:
        """How many calls returned a score."""
        return self._evaluations

    def __call__(
        self, candidate: str, example: Example
    ) -> tuple[float, dict[str, Any]]:
        """Score one proposal on one example.

        Args:
            candidate: The proposed text, as :func:`~.codec.encode` writes it.
            example: What to measure it on.

        Returns:
            The value, and everything the backend may read: every graded term
            on a graded run, and a named reason on every zero.

        Raises:
            EvaluatorAbort: If the harness has now faulted twice in a row on
                ``max_consecutive_harness_failures`` evaluations running.
        """
        sha = hashlib.sha256(candidate.encode()).hexdigest()
        names = {
            "kind": example.kind,
            "example_id": example.id,
            "candidate_sha": sha,
        }
        try:
            bundle = decode(candidate, seed=self._settings.seed)
        except CodecError as error:
            self._evaluations += 1
            return 0.0, {
                "reason": ZERO_CODEC,
                "error_type": "codec",
                "failed_checks": list(error.reasons),
                **names,
            }

        plugin_root = self._materialized(bundle, sha)
        campaign, score, fault = self._run_once_more_if_it_faults(
            bundle, plugin_root, example, sha
        )
        if fault is not None or campaign is None or score is None:
            return self._harness_zero(fault or "the harness returned nothing", names)

        self._consecutive_faults = 0
        self._evaluations += 1
        return score.value, {
            **score.info,
            "candidate_sha": sha,
            "campaign_id": campaign.id,
            "campaign_dir": str(campaign.dir),
            "run_dir": str(campaign.runs_dir / RUN_ID),
            "example_id": example.id,
        }

    def _materialized(self, bundle: Bundle, sha: str) -> Path:
        """The plugin root for this proposal, written once per digest.

        A directory without a matching stamp is an interrupted write, not a
        cache hit, so it is removed rather than written over: ``materialize``
        copies files in and would otherwise leave a half-written tree's
        leftovers standing beside the new ones.
        """
        dest = self._settings.root / PLUGINS_DIR / sha[:8]
        stamp = dest / STAMP_FILE
        if stamp.is_file() and stamp.read_text().strip() == sha:
            return dest
        if dest.exists():
            shutil.rmtree(dest)
        materialize(
            bundle,
            self._frontmatters,
            dest,
            source_plugin_root=self._settings.source_plugin_root,
        )
        stamp.write_text(sha + "\n")
        return dest

    def _run_once_more_if_it_faults(
        self, bundle: Bundle, plugin_root: Path, example: Example, sha: str
    ) -> tuple[Campaign | None, Score | None, str | None]:
        """Freeze, launch, audit, analyze and score; on a fault, once more.

        The retry starts from a fresh campaign id: a campaign is frozen once,
        so the faulted one's directory stands as the evidence it is.

        Scoring is inside the retry for one reason: a judge that answered no
        call at all is infrastructure, the same as a launch that never
        mounted its tools, and the score it would otherwise produce is a
        relevance term of zero indistinguishable from a real verdict. Only
        that failure is caught here — any other exception out of scoring is a
        defect in the scorer and must stop the run.
        """
        campaign: Campaign | None = None
        score: Score | None = None
        fault: str | None = None
        for attempt in (1, 2):
            campaign = None
            score = None
            analysis: dict[str, Any] | None = None
            try:
                campaign = self._freeze(bundle, plugin_root, example, sha)
                analysis = self._launch(campaign, example)
                fault = _harness_fault(
                    analysis, example, campaign.runs_dir / RUN_ID / "workspace"
                )
            # Every step above reads the filesystem, spawns a process or calls
            # the caller's own pre_launch hook. Any of them failing is
            # infrastructure, which this class exists to tell apart from a bad
            # proposal, so the class of exception is not what decides that.
            except Exception as error:  # noqa: BLE001
                fault = f"{type(error).__name__}: {error}"
            if fault is None and campaign is not None and analysis is not None:
                try:
                    score = self._score(campaign, analysis, example)
                except JudgeError as error:
                    fault = f"{type(error).__name__}: {error}"
            if fault is None:
                return campaign, score, None
            self._settings.log(f"harness fault on attempt {attempt}: {fault}")
        return None, None, fault

    def _harness_zero(
        self, detail: str, names: dict[str, Any]
    ) -> tuple[float, dict[str, Any]]:
        """Report a twice-faulted evaluation, or stop the optimization."""
        self._consecutive_faults += 1
        if self._consecutive_faults >= self._settings.max_consecutive_harness_failures:
            raise EvaluatorAbort(
                f"the harness faulted twice on each of "
                f"{self._consecutive_faults} evaluations running; the last was: "
                f"{detail}"
            )
        self._evaluations += 1
        return 0.0, {
            "reason": ZERO_HARNESS,
            "error_type": "harness",
            "detail": detail,
            **names,
        }

    def _freeze(
        self, bundle: Bundle, plugin_root: Path, example: Example, sha: str
    ) -> Campaign:
        """Freeze this evaluation's own campaign on the proposed loop text."""
        settings = self._settings
        counter = self._issued
        self._issued += 1
        return init_campaign(
            CampaignConfig(
                root=settings.root,
                campaign_id=campaign_id_for(counter, example, sha),
                pristine_dir=_pristine_dir(example),
                arms=("A",),
                repeats=1,
                seed=0,
                model=settings.model,
                effort=settings.effort,
                budget_usd=example.budget_usd,
                timeout_s=settings.timeout_s,
                panther_repo=settings.panther_repo,
                plugin_root=plugin_root,
                python=settings.python,
                claude_bin=settings.claude_bin,
                # The parity suite is the protocol's stop-ship check on the
                # substrate, which no proposal touches; running it per
                # proposal would re-prove one fact hundreds of times.
                parity=None,
                session_mode="single",
                toolchain=settings.toolchain,
                loop_template=bundle.loop,
                task_profile=example.kind,
                profile_dir=settings.profile_dir,
                verify_toolchain=False,
            )
        )

    def _launch(self, campaign: Campaign, example: Example) -> dict[str, Any]:
        """Run the campaign's single run and recompute everything it did."""
        settings = self._settings
        if settings.pre_launch is not None:
            settings.pre_launch(campaign, example)
        launch_pending(campaign, only=[RUN_ID], report=settings.log)
        audit_run(campaign, RUN_ID)
        analysis = analyze_run(campaign, RUN_ID)
        # analyze_run records the run's outcomes, not where it wrote them, so
        # the scorer would drop the session's closing text without this.
        analysis["run_dir"] = str(campaign.runs_dir / RUN_ID)
        return analysis

    def _score(
        self, campaign: Campaign, analysis: dict[str, Any], example: Example
    ) -> Score:
        """Grade the run the way its task profile is graded."""
        settings = self._settings
        workspace = campaign.runs_dir / RUN_ID / "workspace"
        if isinstance(example, InterviewExample):
            return score_interview(
                analysis, workspace=workspace, fixture=example.fixture
            )
        if settings.build is None:
            report = draft_build_report(campaign, workspace, log=settings.log)
        else:
            report = settings.build(campaign, workspace)
        return score_loop(
            analysis,
            workspace=workspace,
            clone=workspace / "clone",
            cluster_id=example.cluster_id,
            judge=settings.judge,
            build_report=report,
            weights=settings.weights,
            budget_usd=example.budget_usd,
        )
