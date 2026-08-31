"""A campaign: every constant of a run matrix, frozen before the first launch.

``campaign.json`` records what the protocol says must be pinned — model,
effort, harness version, prompts and their diffs, the pristine digest, the
run order — so a reviewer can tell exactly what ran. Prompts are rendered
here once; the runner only reads them back.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
import shutil
import string
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import ExperimentError
from .arms import ARMS
from .render import arm_prompt, unified_diff
from .workspace import DIGEST_FILE, RECORD_FILE

PROMPTS = Path(__file__).parent / "prompts"
TASK_TEMPLATE = PROMPTS / "task.md"
CAMPAIGN_FILE = "campaign.json"
_SHIM = """#!/bin/sh
exec "{python}" -c "import sys; sys.path.insert(0, '{server_src}'); from ai_rfc_server.cli import main; sys.exit(main())" "$@"
"""


@dataclass(frozen=True)
class CampaignRequest:
    """What a caller asks for, before anything is resolved or frozen.

    Distinct from :class:`Campaign`, which is the *record*: it also holds the
    resolved binary, the claude version, the frozen run order and the digests,
    none of which a caller supplies. Keeping the ask and the record apart is
    what lets :func:`init_campaign` take one argument instead of sixteen
    positional-order-sensitive ones.
    """

    root: Path
    campaign_id: str
    pristine_dir: Path
    arms: tuple[str, ...]
    repeats: int
    seed: int
    model: str
    effort: str
    budget_usd: float
    timeout_s: int
    panther_repo: Path
    plugin_root: Path
    python: str
    claude_bin: str
    parity: dict[str, Any] | None


@dataclass(frozen=True)
class Campaign:
    """One frozen run matrix and everything needed to launch and analyze it."""

    id: str
    root: Path
    target: str
    window: tuple[int, int]
    arms: tuple[str, ...]
    repeats: int
    seed: int
    model: str
    effort: str
    budget_usd: float
    timeout_s: int
    profile_dir: Path
    pristine_dir: Path
    panther_repo: Path
    plugin_root: Path
    python: str
    claude_bin: str
    claude_version: str
    run_order: tuple[str, ...]
    prompt_sha256: dict[str, str]
    pristine_sha256: str
    git: dict[str, str]
    parity: dict[str, Any] | None
    created_at: str

    @property
    def dir(self) -> Path:
        """The campaign directory."""
        return self.root / "campaigns" / self.id

    @property
    def runs_dir(self) -> Path:
        """Where each run's artifacts live."""
        return self.dir / "runs"

    @property
    def prompts_dir(self) -> Path:
        """Rendered prompts and their diffs."""
        return self.dir / "prompts"

    @property
    def bin_dir(self) -> Path:
        """The shim directory placed first on every run's PATH."""
        return self.dir / "bin"

    @property
    def audit_dir(self) -> Path:
        """Per-run audit records."""
        return self.dir / "audit"

    @property
    def analysis_dir(self) -> Path:
        """Aggregate results."""
        return self.dir / "analysis"

    @property
    def server_src(self) -> Path:
        """The ``ai_rfc_server`` source root under the plugin."""
        return self.plugin_root / "server" / "src"

    def run_spec(self, run_id: str) -> tuple[str, int]:
        """Split a run id like ``B2`` into its arm and repeat.

        Args:
            run_id: An id from this campaign's run order.

        Returns:
            The arm letter and the repeat number.

        Raises:
            ExperimentError: If the id is not in this campaign's run order.
        """
        if run_id not in self.run_order:
            raise ExperimentError(f"{run_id} is not in this campaign's run order")
        return run_id[0], int(run_id[1:])


def run_order(arms: tuple[str, ...], repeats: int, seed: int) -> tuple[str, ...]:
    """Seeded interleaving: every repeat block holds every arm, shuffled.

    Args:
        arms: The arm letters taking part.
        repeats: How many blocks to emit.
        seed: Fixes the shuffle so a campaign replays in the same order.

    Returns:
        Run ids in launch order.
    """
    order: list[str] = []
    for block in range(1, repeats + 1):
        shuffled = list(arms)
        random.Random(seed + block).shuffle(shuffled)
        order.extend(f"{arm}{block}" for arm in shuffled)
    return tuple(order)


def render_task(window: tuple[int, int]) -> str:
    """The task prompt, identical across arms, with the window spelled out.

    Args:
        window: Inclusive first and last cluster ordinals.

    Returns:
        The rendered prompt.
    """
    low, high = window
    return string.Template(TASK_TEMPLATE.read_text()).substitute(low=low, high=high)


def git_describe(path: Path) -> str:
    """``git describe --always --dirty`` of a repository, or ``unknown``.

    Args:
        path: Any path inside the repository.

    Returns:
        The description, or ``unknown`` when the command fails.
    """
    result = subprocess.run(
        ["git", "-C", str(path), "describe", "--always", "--dirty"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _claude_version(claude_bin: str) -> str:
    try:
        result = subprocess.run(
            [claude_bin, "--version"], capture_output=True, text=True
        )
    except OSError as error:
        raise ExperimentError(f"cannot run {claude_bin}: {error}") from None
    return result.stdout.strip() or result.stderr.strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def init_campaign(request: CampaignRequest) -> Campaign:
    """Freeze a campaign on disk.

    Args:
        request: What to freeze. See :class:`CampaignRequest`.

    Returns:
        The frozen campaign.

    Raises:
        ExperimentError: If the campaign exists, an arm is unknown, the
            pristine workspace lacks its digest or record, or the claude
            binary cannot be found.
    """
    root = request.root
    campaign_id = request.campaign_id
    pristine_dir = request.pristine_dir
    arms = request.arms
    repeats = request.repeats
    seed = request.seed
    python = request.python
    plugin_root = request.plugin_root
    panther_repo = request.panther_repo
    claude_bin = request.claude_bin

    for arm in arms:
        if arm not in ARMS:
            raise ExperimentError(f"unknown arm {arm!r}")
    # Freeze the binary, not a name. A run's PATH is minimal and deliberately
    # excludes the user's own bin directories, so a bare name that resolves
    # here would not resolve at launch — and a campaign that records a name
    # does not record which binary it actually ran.
    resolved_claude = shutil.which(claude_bin)
    if resolved_claude is None:
        raise ExperimentError(
            f"cannot find the claude binary {claude_bin!r} on PATH; "
            f"pass an absolute path with --claude"
        )
    campaign_dir = root / "campaigns" / campaign_id
    if campaign_dir.exists():
        raise ExperimentError(f"{campaign_dir} exists; a campaign is frozen once")
    digest_path = pristine_dir / DIGEST_FILE
    record_path = pristine_dir / RECORD_FILE
    if not digest_path.exists() or not record_path.exists():
        raise ExperimentError(f"{pristine_dir} is not a prepared pristine workspace")
    record = json.loads(record_path.read_text())

    prompts_dir = campaign_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    rendered = {arm: arm_prompt(arm, plugin_root) for arm in arms}
    prompt_sha256: dict[str, str] = {}
    for arm, text in rendered.items():
        (prompts_dir / f"arm-{arm}.md").write_text(text)
        prompt_sha256[f"arm-{arm}.md"] = _sha256(text)
    task = render_task(tuple(record["window"]))
    (prompts_dir / "task.md").write_text(task)
    prompt_sha256["task.md"] = _sha256(task)
    for index, first in enumerate(arms):
        for second in arms[index + 1 :]:
            (prompts_dir / f"diff-{first}-{second}.patch").write_text(
                unified_diff(
                    rendered[first], rendered[second], f"arm-{first}", f"arm-{second}"
                )
            )

    bin_dir = campaign_dir / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "arfc"
    shim.write_text(
        _SHIM.format(python=python, server_src=plugin_root / "server" / "src")
    )
    shim.chmod(0o755)
    for name in ("runs", "audit", "analysis"):
        (campaign_dir / name).mkdir()

    campaign = Campaign(
        id=campaign_id,
        root=root,
        target=record["target"],
        window=tuple(record["window"]),
        arms=tuple(arms),
        repeats=repeats,
        seed=seed,
        model=request.model,
        effort=request.effort,
        budget_usd=request.budget_usd,
        timeout_s=request.timeout_s,
        profile_dir=root / "profile",
        pristine_dir=pristine_dir,
        panther_repo=panther_repo,
        plugin_root=plugin_root,
        python=python,
        claude_bin=resolved_claude,
        claude_version=_claude_version(resolved_claude),
        run_order=run_order(tuple(arms), repeats, seed),
        prompt_sha256=prompt_sha256,
        pristine_sha256=digest_path.read_text(),
        git={
            "panther": git_describe(panther_repo),
            "ai_rfc": git_describe(plugin_root),
        },
        parity=request.parity,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    (campaign_dir / CAMPAIGN_FILE).write_text(_dump(campaign))
    return campaign


def _dump(campaign: Campaign) -> str:
    payload = dataclasses.asdict(campaign)
    for key, value in payload.items():
        if isinstance(value, Path):
            payload[key] = str(value)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def load_campaign(campaign_dir: Path) -> Campaign:
    """Read a frozen campaign back.

    Args:
        campaign_dir: The directory holding ``campaign.json``.

    Returns:
        The campaign as it was frozen.

    Raises:
        ExperimentError: If ``campaign.json`` is missing.
    """
    path = campaign_dir / CAMPAIGN_FILE
    if not path.exists():
        raise ExperimentError(f"{path} is missing; not a campaign directory")
    payload = json.loads(path.read_text())
    for key in ("root", "profile_dir", "pristine_dir", "panther_repo", "plugin_root"):
        payload[key] = Path(payload[key])
    payload["window"] = tuple(payload["window"])
    payload["arms"] = tuple(payload["arms"])
    payload["run_order"] = tuple(payload["run_order"])
    return Campaign(**payload)
