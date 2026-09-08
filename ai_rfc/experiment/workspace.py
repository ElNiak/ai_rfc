"""Pristine reconstruction workspaces: prepared once, copied per run.

A pristine workspace is a workspace ``ai-rfc init`` built from a ``recon.yaml``,
carried through the deterministic stages and pre-seeded over the window of D27,
digest-manifested so every run starts from bytes the campaign recorded. The
initialising half lives in :mod:`ai_rfc.lifecycle.workspace`, because a campaign
pristine and a production workspace are the same thing up to what a campaign
adds. Nothing here mutates a substrate artifact: out-of-window clusters are
marked processed by checkpoints of the workspace manifest, each carrying a
harness sidecar the analysis excludes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterable

from ai_rfc.draft.checkpoint import write_checkpoint
from ai_rfc.timeline.store import read_clusters
from ai_rfc.views import cli as views_cli

from ..config import ReconConfig
from ..lifecycle import LifecycleError
from ..lifecycle.init.cli import initialise
from ..lifecycle.workspace import (
    ADOPTER_FILES,
    HARNESS_EMAIL,
    HARNESS_NAME,
    PINNED_DATE,
    RECORD_FILE,
    TEMPLATE_COMMIT,
    TEMPLATE_URL,
    Layout,
    _fetch_adopter_files,
    _git,
    _write_adopter_files,
    verify_digest,
    write_digest,
)
from ..pipeline.run import perform
from ..pipeline.stages import BY_NAME
from . import ExperimentError

HARNESS_MARKER = "harness.json"

#: The stages a pristine workspace is carried through before it is sealed. The
#: agent stages are what a campaign exists to observe, so preparation stops at
#: the last deterministic one before them.
PREPARED_STAGES = ("history", "timeline", "views")


def _git_checked(repo: Path, *args: str) -> str:
    """:func:`ai_rfc.lifecycle.workspace._git`, refusing as the harness does.

    Args:
        repo: Working tree to run the command in.
        *args: Git subcommand and its arguments.

    Returns:
        The command's stdout, stripped.

    Raises:
        ExperimentError: If git exits non-zero.
    """
    try:
        return _git(repo, *args)
    except LifecycleError as error:
        raise ExperimentError(str(error)) from None


def pristine_name(config: ReconConfig) -> str:
    """Directory name encoding the reconstruction and its window.

    Two differently windowed slices of one source would otherwise collide in
    ``pristine/``. A config that declares no window reconstructs every cluster,
    and the range it covers is not known until the timeline exists, so it is
    named for that rather than for a range invented before the stages ran.

    Args:
        config: The configuration the workspace is prepared from.

    Returns:
        ``<name>-wLL-HH``, or ``<name>-all`` when no window is declared.
    """
    if config.window is None:
        return f"{config.name}-all"
    low, high = config.window
    return f"{config.name}-w{low:02d}-{high:02d}"


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


def migrate_draft(
    workspace: Path,
    *,
    template: str = TEMPLATE_URL,
    template_commit: str = TEMPLATE_COMMIT,
) -> str:
    """Move a library-root draft repository to the adopter layout in one commit.

    Every tracked file except the draft file is removed and the three adopter
    files are added; tags are untouched, so every earlier revision still lists
    the tree it was gated against.

    Args:
        workspace: The workspace whose ``draft/`` to migrate.
        template: Template clone source.
        template_commit: The commit the adopter files are taken from.

    Returns:
        The draft repository's new HEAD.

    Raises:
        ExperimentError: If the draft is dirty, already an adopter, or has no
            single draft file.
    """
    draft = workspace / "draft"
    if _git_checked(draft, "status", "--porcelain"):
        raise ExperimentError(
            f"{draft} has uncommitted changes; commit or discard them first"
        )
    tracked = _git_checked(draft, "ls-files").splitlines()
    drafts = [
        name for name in tracked if name.startswith("draft-") and name.endswith(".md")
    ]
    if len(drafts) != 1:
        raise ExperimentError(
            f"{draft} tracks {len(drafts)} draft-*.md files; expected one"
        )
    if "main.mk" not in tracked and "Makefile" in tracked:
        raise ExperimentError(f"{draft} is already an adopter; nothing to migrate")
    try:
        files = _fetch_adopter_files(template, template_commit)
    except LifecycleError as error:
        raise ExperimentError(str(error)) from None
    to_remove = [name for name in tracked if name != drafts[0]]
    if to_remove:
        _git_checked(draft, "rm", "-q", "--", *to_remove)
    _write_adopter_files(draft, files)
    _git_checked(draft, "add", "--", *ADOPTER_FILES)
    _git_checked(draft, "config", "user.name", HARNESS_NAME)
    _git_checked(draft, "config", "user.email", HARNESS_EMAIL)
    try:
        _git(
            draft,
            "commit",
            "-q",
            "-m",
            "adopt the Internet-Draft template layout",
            date=PINNED_DATE,
        )
    except LifecycleError as error:
        raise ExperimentError(str(error)) from None
    return _git_checked(draft, "rev-parse", "HEAD")


def preseed(workspace: Path, ordinals: Iterable[int]) -> list[str]:
    """Checkpoint the workspace manifest against each ordinal and mark it pre-seeded.

    Args:
        workspace: The workspace whose ``checkpoints/`` directory is written.
        ordinals: Cluster ordinals to pre-seed, in order.

    Returns:
        The cluster ids checkpointed, in the order given.

    Raises:
        ExperimentError: If an ordinal has no cluster.
    """
    by_ordinal = {
        row["ordinal"]: row["id"] for row in read_clusters(workspace / "timeline")
    }
    seeded = []
    for ordinal in ordinals:
        cluster_id = by_ordinal.get(ordinal)
        if cluster_id is None:
            raise ExperimentError(f"no cluster with ordinal {ordinal}")
        checkpoint_dir = write_checkpoint(
            workspace / "manifest.yaml",
            workspace / "timeline",
            cluster_id,
            workspace / "checkpoints",
        )
        (checkpoint_dir / HARNESS_MARKER).write_text(
            json.dumps(
                {"pre_seeded": True, "reason": "outside window", "ordinal": ordinal},
                sort_keys=True,
            )
            + "\n"
        )
        seeded.append(cluster_id)
    return seeded


def copy_workspace(pristine: Path, dest: Path) -> Path:
    """Copy a pristine workspace for one run and verify the copy.

    The copy is verified against the digest manifest copied along with it.

    Args:
        pristine: The sealed workspace to copy from.
        dest: Where the run's private workspace is created (must not exist).

    Returns:
        The destination path.

    Raises:
        ExperimentError: If ``dest`` exists, the copy does not reproduce the
            digest manifest, or a nested repository HEAD moved.
    """
    if dest.exists():
        raise ExperimentError(f"{dest} exists; a run never reuses a workspace")
    shutil.copytree(pristine, dest, symlinks=False)
    problems = verify_digest(dest)
    if problems:
        raise ExperimentError(f"copied workspace does not verify: {problems[:5]}")
    record = json.loads((dest / RECORD_FILE).read_text())
    for name, key in (("clone", "clone_head"), ("draft", "draft_head")):
        head = _git_checked(dest / name, "rev-parse", "HEAD")
        if head != record[key]:
            raise ExperimentError(
                f"{name} HEAD {head} differs from recorded {record[key]}"
            )
    return dest


def reseal(workspace: Path, dest: Path) -> Path:
    """Seal a used workspace as a new baseline, leaving the source untouched.

    Every run copies from a sealed baseline, and both :func:`driver.execute` and
    :func:`copy_workspace` refuse a tree that no longer matches its digest. A
    run's own workspace moves past its seal the moment a session commits prose,
    so continuing a stopped sweep in a fresh campaign means re-sealing that
    workspace rather than relaxing the guard that caught it.

    The seal is taken on a copy. A finished run's directory is what its audit
    reads, and rewriting the record and digest in place would edit that evidence
    in order to launch the next campaign.

    ``draft_head`` is re-read because prose commits and revision tags advance it
    by design. ``clone_head`` is checked and never updated: the clone is
    read-only for the whole reconstruction, so a moved one is a defect rather
    than progress, and it must fail here exactly as it would in a run.

    Args:
        workspace: A run's workspace to continue from.
        dest: Where the resealed baseline is written; must not exist.

    Returns:
        The resealed baseline's path.

    Raises:
        ExperimentError: If ``workspace`` holds no pristine record, ``dest``
            exists, or the clone's HEAD moved.
    """
    if not (workspace / RECORD_FILE).exists():
        raise ExperimentError(f"{workspace} is not a prepared pristine workspace")
    if dest.exists():
        raise ExperimentError(f"{dest} exists; a pristine workspace is prepared once")

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(workspace, dest, symlinks=False)

    record = json.loads((dest / RECORD_FILE).read_text())
    clone_head = _git_checked(dest / "clone", "rev-parse", "HEAD")
    if clone_head != record["clone_head"]:
        raise ExperimentError(
            f"clone HEAD {clone_head} differs from recorded {record['clone_head']}"
        )
    # The mirror of the clone check above: the clone must not have moved, and
    # the draft must not be mid-write. A kill landing between a prose write and
    # its commit leaves files that `write_digest` would seal into the baseline,
    # so `verify_digest` passes on every copy and the half-written prose
    # propagates silently into every run made from it. Nothing downstream would
    # name it — `partial_reason` reads checkpoints and tags, not the worktree.
    dirty = _git_checked(dest / "draft", "status", "--porcelain")
    if dirty:
        raise ExperimentError(
            f"{workspace}/draft has uncommitted changes; a baseline seals its "
            f"whole tree, so these would enter every run copied from it. "
            f"Commit or discard them first:\n{dirty}"
        )
    record["draft_head"] = _git_checked(dest / "draft", "rev-parse", "HEAD")
    record["resealed_from"] = str(workspace)
    (dest / RECORD_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    write_digest(dest)
    return dest


def prepare(
    config: ReconConfig,
    *,
    root: Path,
    config_path: Path,
    template: str = TEMPLATE_URL,
    template_commit: str = TEMPLATE_COMMIT,
) -> Path:
    """Build a campaign's pristine workspace from a config, under ``root/pristine/``.

    Initialises the workspace exactly as ``ai-rfc init`` does, performs the
    deterministic stages up to views, re-verifies that the views reproduce
    byte-for-byte, pre-seeds every out-of-window cluster and seals the digest a
    campaign copies from.

    Args:
        config: What to reconstruct, and the window to leave unprocessed.
        root: The runs root; the workspace lands in ``root/pristine/``.
        config_path: Where the config was read from; its digest is recorded.
        template: Draft template clone source.
        template_commit: The commit the draft scaffold is pinned to.

    Returns:
        The pristine directory.

    Raises:
        ExperimentError: If the pristine directory exists, initialisation
            refuses, a substrate stage fails or the views do not reproduce.
    """
    pristine = root / "pristine" / pristine_name(config)
    if pristine.exists():
        raise ExperimentError(
            f"{pristine} exists; a pristine workspace is prepared once"
        )
    try:
        initialise(
            config,
            config_path=config_path,
            dest=pristine,
            template=template,
            template_commit=template_commit,
        )
    except LifecycleError as error:
        raise ExperimentError(str(error)) from None

    layout = Layout(pristine)
    for name in PREPARED_STAGES:
        result = perform(BY_NAME[name], layout)
        if not result.ok:
            raise ExperimentError(f"{name} exited {result.exit_code}; see stderr")
        # A campaign compares runs against these bytes, so the views are asked
        # to reproduce themselves before anything is sealed.
        if name == "views" and views_cli.main([*result.argv, "--verify"]) != 0:
            raise ExperimentError("views do not reproduce byte-for-byte; see stderr")

    ordinals = [row["ordinal"] for row in read_clusters(layout.timeline)]
    window = config.window or (min(ordinals), max(ordinals))
    seeded = preseed(pristine, out_of_window(ordinals, window))

    record = json.loads(layout.init_record.read_text())
    record.update(
        {
            "target": config.name,
            "window": list(window),
            "cluster_count": len(ordinals),
            "pre_seeded": seeded,
            "clone_head": record["resolved_pin"],
            "scaffold_layout": "adopter",
        }
    )
    (pristine / RECORD_FILE).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    write_digest(pristine)
    return pristine
