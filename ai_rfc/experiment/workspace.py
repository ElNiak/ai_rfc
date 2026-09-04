"""Pristine reconstruction workspaces: prepared once, copied per run.

A pristine workspace is the deterministic output of the substrate stages
plus the window pre-seeding of D27, digest-manifested so every run starts
from bytes the campaign recorded. Nothing here mutates a substrate artifact:
out-of-window clusters are marked processed by checkpoints of the workspace
manifest, each carrying a harness sidecar the analysis excludes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

import yaml

from ai_rfc.draft.build import BuildError, Toolchain, load_toolchain
from ai_rfc.draft.checkpoint import write_checkpoint
from ai_rfc.timeline.store import read_clusters
from ai_rfc.views import cli as views_cli

from . import ExperimentError

PROMPTS = Path(__file__).parent / "prompts"
DRAFT_SKELETON = PROMPTS / "draft-skeleton.md"
TEMPLATE_URL = "https://github.com/ElNiak/auto-i-d-template"
TEMPLATE_COMMIT = "dcdd985a86afad97a50f7b5e1b613f57c194b774"
ADOPTER_FILES = ("Makefile", ".gitignore", ".editorconfig")
EXTRA_IGNORES = ("lib", ".venv", ".gems", "node_modules", "Gemfile.lock", ".refcache")
REFERENCES_FILE = "references.yaml"
REFCACHE_DIR = "refcache"
SUBSTRATE_PARTS = ("clone", "corpus", "timeline")
HARNESS_NAME = "ai-rfc-harness"
HARNESS_EMAIL = "ai-rfc-harness@localhost"
PINNED_DATE = "2026-08-26T00:00:00+00:00"
HARNESS_MARKER = "harness.json"
DIGEST_FILE = "pristine.sha256"
RECORD_FILE = "pristine.json"
_SKIP_FROM_DIGEST = frozenset({DIGEST_FILE, RECORD_FILE})


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
    references: tuple[str, ...] = ()

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
    references=(
        "RFC9000",
        "RFC9001",
        "RFC9002",
        "RFC9114",
        "RFC9204",
        "RFC8446",
        "RFC5280",
        "RFC6125",
    ),
)
#: MARK across its whole timeline. A production sweep is this: a target whose
#: window spans every cluster, run with ``session_mode="per-cluster"``. Nothing
#: in the harness distinguishes it from an experiment arm — the window is the
#: only thing that made the pilot a pilot.
MARK = Target(
    name="mark",
    source=Path("reconstructions/mark"),
    #: The snapshot is pinned rather than resolved to the newest, so a campaign
    #: freezes the evidence it ran against. This one is ``ai_rfc.forge/2``; the
    #: 2026-08-25 snapshot beside it predates the package rename and carries no
    #: ``fidelity_ceiling``, which is why it is not the pin.
    forge_snapshot=Path(
        "forge/gitlab.cylab.be__cylab__mark/snapshot-2026-09-01T09-35-27Z"
    ),
    window=(1, 69),
    draft_name="draft-elniak-mark-reconstructed",
    rfc_id="MARK-RECON-1",
    title="MARK: A Reconstructed Specification",
    abbrev="MARK Reconstructed",
    references=("RFC9110", "RFC8259"),
)
TARGETS: dict[str, Target] = {"aioquic": AIOQUIC, "mark": MARK}


def _run_git(*args: str, date: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", *args], capture_output=True, text=True, env=env)


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    result = _run_git("-C", str(repo), *args, date=date)
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


def _fetch_adopter_files(template: str, template_commit: str) -> dict[str, str]:
    """Clone ``template`` at its pin and read the adopter files' text.

    Args:
        template: Clone source (URL or local path).
        template_commit: The commit to read the adopter files from.

    Returns:
        Each of ``ADOPTER_FILES`` mapped to its file content.

    Raises:
        ExperimentError: If the clone fails or an adopter file is missing.
    """
    with tempfile.TemporaryDirectory(prefix="i-d-template-") as staging:
        library = Path(staging) / "template"
        cloned = _run_git("clone", "-q", template, str(library))
        if cloned.returncode != 0:
            raise ExperimentError(f"cloning {template} failed: {cloned.stderr.strip()}")
        _git(library, "checkout", "-q", template_commit)
        files: dict[str, str] = {}
        for name in ADOPTER_FILES:
            source = library / "template" / name
            if not source.exists():
                raise ExperimentError(
                    f"{template}@{template_commit[:12]} has no template/{name}"
                )
            files[name] = source.read_text()
        return files


def _write_adopter_files(dest: Path, files: dict[str, str]) -> None:
    """Write already-fetched adopter files into ``dest`` and extend its ignores."""
    for name, text in files.items():
        (dest / name).write_text(text)
    ignored = [
        line for line in (dest / ".gitignore").read_text().splitlines() if line.strip()
    ]
    (dest / ".gitignore").write_text("\n".join([*ignored, *EXTRA_IGNORES]) + "\n")


def scaffold_draft(
    dest: Path, target: Target, *, template: str, template_commit: str
) -> str:
    """Clone the template at its pin, copy its adopter files, seed the draft.

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
    files = _fetch_adopter_files(template, template_commit)
    if dest.exists():
        raise ExperimentError(f"{dest} exists; a draft is scaffolded once")
    dest.mkdir(parents=True)
    _write_adopter_files(dest, files)
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
        dest,
        "commit",
        "-q",
        "-m",
        "adopt the Internet-Draft template",
        date=PINNED_DATE,
    )
    return _git(dest, "rev-parse", "HEAD")


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
    if _git(draft, "status", "--porcelain"):
        raise ExperimentError(
            f"{draft} has uncommitted changes; commit or discard them first"
        )
    tracked = _git(draft, "ls-files").splitlines()
    drafts = [
        name for name in tracked if name.startswith("draft-") and name.endswith(".md")
    ]
    if len(drafts) != 1:
        raise ExperimentError(
            f"{draft} tracks {len(drafts)} draft-*.md files; expected one"
        )
    if "main.mk" not in tracked and "Makefile" in tracked:
        raise ExperimentError(f"{draft} is already an adopter; nothing to migrate")
    files = _fetch_adopter_files(template, template_commit)
    to_remove = [name for name in tracked if name != drafts[0]]
    if to_remove:
        _git(draft, "rm", "-q", "--", *to_remove)
    _write_adopter_files(draft, files)
    _git(draft, "add", "--", *ADOPTER_FILES)
    _git(draft, "config", "user.name", HARNESS_NAME)
    _git(draft, "config", "user.email", HARNESS_EMAIL)
    _git(
        draft,
        "commit",
        "-q",
        "-m",
        "adopt the Internet-Draft template layout",
        date=PINNED_DATE,
    )
    return _git(draft, "rev-parse", "HEAD")


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


def _digests(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if ".git" in relative.parts or relative.name in _SKIP_FROM_DIGEST:
            continue
        found[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _read_digest_manifest(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            digest, _, relative = line.partition("  ")
            expected[relative] = digest
    return expected


def write_digest(root: Path) -> Path:
    """Write ``pristine.sha256`` over every regular file outside ``.git``.

    Args:
        root: The workspace to seal.

    Returns:
        The digest manifest's path.
    """
    lines = [f"{digest}  {relative}" for relative, digest in _digests(root).items()]
    digest_path = root / DIGEST_FILE
    digest_path.write_text("\n".join(lines) + "\n")
    return digest_path


def verify_digest(root: Path) -> list[str]:
    """Differences between a tree and its digest manifest; empty means verified.

    Args:
        root: A workspace previously sealed by :func:`write_digest`.

    Returns:
        One ``missing:``/``unexpected:``/``modified:`` line per differing
        path, in path-sorted order.
    """
    manifest_path = root / DIGEST_FILE
    if not manifest_path.exists():
        return [f"{DIGEST_FILE} is missing"]
    expected = _read_digest_manifest(manifest_path)
    actual = _digests(root)
    problems = []
    for relative in sorted(set(expected) | set(actual)):
        if relative not in actual:
            problems.append(f"missing: {relative}")
        elif relative not in expected:
            problems.append(f"unexpected: {relative}")
        elif actual[relative] != expected[relative]:
            problems.append(f"modified: {relative}")
    return problems


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
        head = _git(dest / name, "rev-parse", "HEAD")
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
    clone_head = _git(dest / "clone", "rev-parse", "HEAD")
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
    dirty = _git(dest / "draft", "status", "--porcelain")
    if dirty:
        raise ExperimentError(
            f"{workspace}/draft has uncommitted changes; a baseline seals its "
            f"whole tree, so these would enter every run copied from it. "
            f"Commit or discard them first:\n{dirty}"
        )
    record["draft_head"] = _git(dest / "draft", "rev-parse", "HEAD")
    record["resealed_from"] = str(workspace)
    (dest / RECORD_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    write_digest(dest)
    return dest


def _git_version() -> str:
    return _run_git("--version").stdout.strip()


def _copy_substrate(
    source: Path, pristine: Path, forge_snapshot: Path | None
) -> Path | None:
    for part in SUBSTRATE_PARTS:
        if not (source / part).is_dir():
            raise ExperimentError(f"{source / part} is missing")
    pristine.mkdir(parents=True)
    for part in SUBSTRATE_PARTS:
        shutil.copytree(source / part, pristine / part, symlinks=False)
    if forge_snapshot is None:
        return None
    snapshot = pristine / forge_snapshot
    shutil.copytree(source / forge_snapshot, snapshot, symlinks=False)
    return snapshot


def _emit_and_verify_views(
    pristine: Path, snapshot: Path | None, views_cli: ModuleType
) -> None:
    args = [
        str(pristine / "timeline"),
        "--corpus",
        str(pristine / "corpus"),
        "--repo",
        str(pristine / "clone"),
        "--out",
        str(pristine / "clusters"),
    ]
    if snapshot is not None:
        args += ["--forge", str(snapshot)]
    if views_cli.main(args) != 0:
        raise ExperimentError("view emission failed; see stderr")
    if views_cli.main(args + ["--verify"]) != 0:
        raise ExperimentError("views do not reproduce byte-for-byte; see stderr")


def _write_empty_state(pristine: Path, target: Target) -> None:
    (pristine / "manifest.yaml").write_text(
        yaml.safe_dump(
            {"rfc": target.rfc_id, "title": target.title, "requirements": {}},
            sort_keys=False,
        )
    )
    (pristine / "questions.yaml").write_text("questions: {}\n")
    (pristine / "revisions.yaml").write_text("revisions: {}\n")
    (pristine / "interviews").mkdir()


def _reference_filename(reference: str) -> str:
    if reference.startswith("RFC"):
        return f"reference.RFC.{reference[3:]}.xml"
    return f"reference.{reference}.xml"


def prepare(
    target: Target,
    *,
    root: Path,
    panther_repo: Path,
    toolchain: Path | None = None,
    template: str = TEMPLATE_URL,
    template_commit: str = TEMPLATE_COMMIT,
) -> Path:
    """Build the pristine workspace of ``target`` under ``root/pristine/``.

    Copies the substrate outputs, emits and re-verifies every view, writes the
    empty manifest and registers, scaffolds the draft, seals any references
    the target declares, pre-seeds every out-of-window ordinal, records
    provenance and writes the digest manifest.

    Args:
        target: What to prepare, and the window to leave unprocessed.
        root: The runs root; the workspace lands in ``root/pristine/``.
        panther_repo: PANTHER repository root; ``target.source`` resolves
            relative to it.
        toolchain: Toolchain record used to seal ``target.references`` into
            the workspace's ``refcache/``; required when the target declares
            any references.
        template: Draft template clone source.
        template_commit: The commit the draft scaffold is pinned to.

    Returns:
        The pristine directory.

    Raises:
        ExperimentError: If the pristine directory exists, a source part is
            missing, a substrate stage fails, references are declared with no
            toolchain, the toolchain record is unusable, or the toolchain
            never cached a declared reference.
    """
    pristine = root / "pristine" / target.pristine_name
    if pristine.exists():
        raise ExperimentError(
            f"{pristine} exists; a pristine workspace is prepared once"
        )

    record_toolchain: Toolchain | None = None
    if target.references:
        if toolchain is None:
            raise ExperimentError(
                f"{target.name} declares references; pass --toolchain so they "
                "can be sealed into the workspace"
            )
        try:
            record_toolchain = load_toolchain(toolchain)
        except BuildError as error:
            raise ExperimentError(
                f"toolchain record {toolchain} is unusable: {error}"
            ) from None
        missing = [
            reference
            for reference in target.references
            if not (record_toolchain.refcache / _reference_filename(reference)).exists()
        ]
        if missing:
            raise ExperimentError(
                f"the toolchain never cached {', '.join(missing)}; add them to "
                "the seed list and re-run `experiment toolchain provision`"
            )

    source = (
        target.source if target.source.is_absolute() else panther_repo / target.source
    )
    snapshot = _copy_substrate(source, pristine, target.forge_snapshot)
    clone_head = _git(pristine / "clone", "rev-parse", "HEAD")

    _emit_and_verify_views(pristine, snapshot, views_cli)
    _write_empty_state(pristine, target)
    draft_head = scaffold_draft(
        pristine / "draft", target, template=template, template_commit=template_commit
    )

    refcache_sha256: str | None = None
    toolchain_sha256: str | None = None
    template_home: str | None = None
    if record_toolchain is not None:
        cache = pristine / REFCACHE_DIR
        cache.mkdir()
        for reference in target.references:
            name = _reference_filename(reference)
            shutil.copyfile(record_toolchain.refcache / name, cache / name)
        refcache_sha256 = hashlib.sha256(
            b"".join((cache / p.name).read_bytes() for p in sorted(cache.iterdir()))
        ).hexdigest()
        toolchain_sha256 = hashlib.sha256(
            record_toolchain.path.read_bytes()
        ).hexdigest()
        template_home = str(record_toolchain.template_home)
    if target.references:
        (pristine / REFERENCES_FILE).write_text(
            "references:\n"
            + "".join(f"- {reference}\n" for reference in target.references)
        )
    else:
        (pristine / REFERENCES_FILE).write_text("references: []\n")

    ordinals = [row["ordinal"] for row in read_clusters(pristine / "timeline")]
    seeded = preseed(pristine, out_of_window(ordinals, target.window))

    record: dict[str, Any] = {
        "target": target.name,
        "window": list(target.window),
        "source": str(source),
        "clone_head": clone_head,
        "draft_head": draft_head,
        "template": template,
        "template_commit": template_commit,
        "scaffold_layout": "adopter",
        "references": list(target.references),
        "refcache_sha256": refcache_sha256,
        "toolchain_sha256": toolchain_sha256,
        "template_home": template_home,
        "forge_snapshot": str(target.forge_snapshot) if target.forge_snapshot else None,
        "cluster_count": len(ordinals),
        "pre_seeded": seeded,
        "git_version": _git_version(),
        "python": sys.version.split()[0],
    }
    (pristine / RECORD_FILE).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    write_digest(pristine)
    return pristine
