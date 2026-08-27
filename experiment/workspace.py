"""Pristine reconstruction workspaces: prepared once, copied per run.

A pristine workspace is the deterministic output of the substrate stages
plus the window pre-seeding of D27, digest-manifested so every run starts
from bytes the campaign recorded. Nothing here mutates a substrate artifact:
out-of-window clusters are marked processed by checkpoints of the workspace
manifest, each carrying a harness sidecar the analysis excludes.
"""

from __future__ import annotations

import os
import shutil
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import ExperimentError

PROMPTS = Path(__file__).parent / "prompts"
DRAFT_SKELETON = PROMPTS / "draft-skeleton.md"
TEMPLATE_URL = "https://github.com/ElNiak/auto-i-d-template"
TEMPLATE_COMMIT = "dcdd985a86afad97a50f7b5e1b613f57c194b774"
TEMPLATE_STRIP = (
    ".claude",
    ".claude-plugin",
    ".codacy",
    ".serena",
    ".specify",
    "CLAUDE.md",
    ".mcp.example.json",
    ".mcp.json",
)
HARNESS_NAME = "arfc-harness"
HARNESS_EMAIL = "arfc-harness@localhost"
PINNED_DATE = "2026-08-26T00:00:00+00:00"


@dataclass(frozen=True)
class Target:
    """One reconstruction target and the window the experiment processes."""

    name: str
    source: Path
    forge_snapshot: Path | None
    window: tuple[int, int]
    draft_name: str
    rfc_id: str
    title: str
    abbrev: str

    @property
    def pristine_name(self) -> str:
        """Directory name encoding target and window, e.g. ``aioquic-w02-11``."""
        low, high = self.window
        return f"{self.name}-w{low:02d}-{high:02d}"


AIOQUIC = Target(
    name="aioquic",
    source=Path("reconstructions/aioquic"),
    forge_snapshot=Path(
        "forge/github.com__aiortc__aioquic/snapshot-2026-08-25T15-16-59Z"
    ),
    window=(2, 11),
    draft_name="draft-elniak-aioquic-reconstructed",
    rfc_id="AIOQUIC-RECON",
    title="aioquic: A Reconstructed Specification",
    abbrev="aioquic Reconstructed",
)
TARGETS: dict[str, Target] = {"aioquic": AIOQUIC}


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        raise ExperimentError(
            f"git {' '.join(args)} in {repo} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def out_of_window(ordinals: Iterable[int], window: tuple[int, int]) -> list[int]:
    """The ordinals outside ``window`` (inclusive bounds), in input order.

    Args:
        ordinals: Cluster ordinals, typically every ordinal in a timeline.
        window: Inclusive ``(low, high)`` range the experiment processes.

    Returns:
        The ordinals to pre-seed, in the order they were given.
    """
    low, high = window
    return [ordinal for ordinal in ordinals if ordinal < low or ordinal > high]


def scaffold_draft(
    dest: Path, target: Target, *, template: str, template_commit: str
) -> str:
    """Clone the template at its pin, strip its agent files, seed the draft.

    Args:
        dest: Where the draft repository is created (must not exist).
        target: Names the draft file and fills its front matter.
        template: Clone source (URL or local path).
        template_commit: The commit the scaffold is pinned to.

    Returns:
        The draft repository's HEAD after the scaffold commit.

    Raises:
        ExperimentError: If ``dest`` exists or any git step fails.
    """
    if dest.exists():
        raise ExperimentError(f"{dest} exists; a draft is scaffolded once")
    cloned = subprocess.run(
        ["git", "clone", "-q", template, str(dest)], capture_output=True, text=True
    )
    if cloned.returncode != 0:
        raise ExperimentError(f"cloning {template} failed: {cloned.stderr.strip()}")
    _git(dest, "checkout", "-q", template_commit)
    shutil.rmtree(dest / ".git")
    for name in TEMPLATE_STRIP:
        path = dest / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    ignore = dest / ".gitignore"
    if ignore.exists():
        kept = [
            line
            for line in ignore.read_text().splitlines()
            if line.strip() != "draft-*"
        ]
        ignore.write_text("\n".join(kept) + "\n")
    skeleton = string.Template(DRAFT_SKELETON.read_text()).substitute(
        title=target.title,
        abbrev=target.abbrev,
        draft_name=target.draft_name,
        target=target.name,
    )
    (dest / f"{target.draft_name}.md").write_text(skeleton)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "user.name", HARNESS_NAME)
    _git(dest, "config", "user.email", HARNESS_EMAIL)
    _git(dest, "add", "-A")
    _git(
        dest, "commit", "-q", "-m", "scaffold from auto-i-d-template", date=PINNED_DATE
    )
    return _git(dest, "rev-parse", "HEAD")
