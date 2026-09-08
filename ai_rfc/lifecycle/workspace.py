"""The workspace a reconstruction runs in: acquired, scaffolded, sealed.

``ai-rfc init`` builds one from a ``recon.yaml``, and the experiment harness
prepares its campaign pristines by calling the same code, so a production
workspace and a campaign's differ only in what the campaign adds on top of
this. Acquisition is the one networked phase (D34) and it lives here rather
than in a stage, because a stage the driver may re-run must not reach the
network.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import string
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import ReconConfig
from ..draft.build import BuildError, Toolchain, load_toolchain
from ..pipeline.run import perform
from ..pipeline.stages import BY_NAME
from ..pipeline.workspace import Workspace
from . import LifecycleError

#: The draft skeleton still lives beside the harness's other prompts. Reading
#: it from here is a layering inversion the substrate cannot fix on its own:
#: the file is prose the harness owns, and moving it would move a prompt out of
#: the directory every other prompt is rendered from.
PROMPTS = Path(__file__).resolve().parents[1] / "experiment" / "prompts"
DRAFT_SKELETON = PROMPTS / "draft-skeleton.md"
#: What the skeleton cites without declaring: its ``{::boilerplate
#: bcp14-tagged}`` expands to the BCP 14 paragraph, which references both of
#: these. They belong beside :data:`DRAFT_SKELETON` because the skeleton is
#: what makes them mandatory — a workspace's sealed refcache overrides the
#: toolchain's shared one at build time, so a cache holding only the config's
#: declared references cannot build the draft ``scaffold`` just wrote.
SKELETON_REFERENCES: tuple[str, ...] = ("RFC2119", "RFC8174")
TEMPLATE_URL = "https://github.com/ElNiak/auto-i-d-template"
TEMPLATE_COMMIT = "dcdd985a86afad97a50f7b5e1b613f57c194b774"
ADOPTER_FILES = ("Makefile", ".gitignore", ".editorconfig")
EXTRA_IGNORES = ("lib", ".venv", ".gems", "node_modules", "Gemfile.lock", ".refcache")
REFERENCES_FILE = "references.yaml"
REFCACHE_DIR = "refcache"
HARNESS_NAME = "ai-rfc-harness"
HARNESS_EMAIL = "ai-rfc-harness@localhost"
PINNED_DATE = "2026-08-26T00:00:00+00:00"
DIGEST_FILE = "pristine.sha256"
RECORD_FILE = "pristine.json"
CONFIG_FILE = "recon.yaml"
INIT_RECORD = "init.json"
#: The digest manifest cannot cover itself, and the campaign record is written
#: after the seal. The sealed config and ``init.json`` are both digested: they
#: are what a workspace is verified to have been initialised from.
_SKIP_FROM_DIGEST = frozenset({DIGEST_FILE, RECORD_FILE})


class Layout(Workspace):
    """A production workspace: the pipeline's layout plus the operator's records."""

    @property
    def config(self) -> Path:
        """The sealed copy of ``recon.yaml``."""
        return self.root / CONFIG_FILE

    @property
    def init_record(self) -> Path:
        """What ``init`` resolved and froze."""
        return self.root / INIT_RECORD

    @property
    def refcache(self) -> Path:
        """Sealed reference cache for offline builds."""
        return self.root / REFCACHE_DIR

    @property
    def runs(self) -> Path:
        """One directory per ``run`` invocation (CLI-2)."""
        return self.root / "runs"

    @property
    def interviews(self) -> Path:
        """Interview transcripts the question register points at."""
        return self.root / "interviews"


def _run_git(*args: str, date: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", *args], capture_output=True, text=True, env=env)


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    result = _run_git("-C", str(repo), *args, date=date)
    if result.returncode != 0:
        raise LifecycleError(
            f"git {' '.join(args)} in {repo} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


@dataclass(frozen=True)
class Acquired:
    """What acquisition pinned."""

    resolved_sha: str
    forge_snapshot: str | None


def acquire(config: ReconConfig, layout: Layout) -> Acquired:
    """Clone the source at its pin and fetch the forge snapshot — the networked phase.

    Args:
        config: The validated configuration.
        layout: The workspace to fill.

    Returns:
        The resolved pin and the snapshot directory name, if a forge was fetched.

    Raises:
        LifecycleError: If the clone or the checkout fails, or the forge stage
            refuses.
    """
    cloned = _run_git("clone", "-q", config.source.repo, str(layout.clone))
    if cloned.returncode != 0:
        raise LifecycleError(
            f"cloning {config.source.repo} failed: {cloned.stderr.strip()}"
        )
    checked = _run_git(
        "-C", str(layout.clone), "checkout", "-q", "--detach", config.source.pin
    )
    if checked.returncode != 0:
        raise LifecycleError(
            f"{config.source.pin}: not a commit or ref in {config.source.repo}: "
            f"{checked.stderr.strip()}"
        )
    resolved = _git(layout.clone, "rev-parse", "HEAD")
    snapshot: str | None = None
    if config.source.host != "none":
        result = perform(
            BY_NAME["forge"],
            layout,
            forge_url=config.source.repo,
            host=config.source.host,
        )
        if not result.ok:
            raise LifecycleError(f"forge fetch exited {result.exit_code}; see stderr")
        latest = layout.latest_forge_snapshot()
        snapshot = latest.name if latest else None
    return Acquired(resolved_sha=resolved, forge_snapshot=snapshot)


def _fetch_adopter_files(template: str, template_commit: str) -> dict[str, str]:
    """Clone ``template`` at its pin and read the adopter files' text.

    Args:
        template: Clone source (URL or local path).
        template_commit: The commit to read the adopter files from.

    Returns:
        Each of ``ADOPTER_FILES`` mapped to its file content.

    Raises:
        LifecycleError: If the clone fails or an adopter file is missing.
    """
    with tempfile.TemporaryDirectory(prefix="i-d-template-") as staging:
        library = Path(staging) / "template"
        cloned = _run_git("clone", "-q", template, str(library))
        if cloned.returncode != 0:
            raise LifecycleError(f"cloning {template} failed: {cloned.stderr.strip()}")
        _git(library, "checkout", "-q", template_commit)
        files: dict[str, str] = {}
        for name in ADOPTER_FILES:
            source = library / "template" / name
            if not source.exists():
                raise LifecycleError(
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


def scaffold(
    config: ReconConfig, layout: Layout, *, template: str, template_commit: str
) -> str:
    """Clone the template at its pin, copy its adopter files, seed the draft.

    Args:
        config: Names the draft file and fills its front matter.
        layout: The workspace whose ``draft/`` is created; it must not exist.
        template: Clone source (URL or local path).
        template_commit: The commit the scaffold is pinned to.

    Returns:
        The draft repository's HEAD after the scaffold commit.

    Raises:
        LifecycleError: If the draft directory exists or any git step fails.
    """
    files = _fetch_adopter_files(template, template_commit)
    dest = layout.draft
    if dest.exists():
        raise LifecycleError(f"{dest} exists; a draft is scaffolded once")
    dest.mkdir(parents=True)
    _write_adopter_files(dest, files)
    skeleton = string.Template(DRAFT_SKELETON.read_text()).substitute(
        title=config.draft.title,
        abbrev=config.draft.abbrev,
        draft_name=config.draft.name,
        target=config.name,
    )
    (dest / f"{config.draft.name}.md").write_text(skeleton)
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


def write_registers(config: ReconConfig, layout: Layout) -> None:
    """Write the empty manifest, question and revision registers.

    Args:
        config: Supplies the manifest's ``rfc`` identifier and title.
        layout: The workspace to write into.
    """
    layout.manifest.write_text(
        yaml.safe_dump(
            {
                "rfc": config.draft.rfc_id,
                "title": config.draft.title,
                "requirements": {},
            },
            sort_keys=False,
        )
    )
    layout.questions.write_text("questions: {}\n")
    layout.revisions.write_text("revisions: {}\n")
    layout.interviews.mkdir()


def _reference_filename(reference: str) -> str:
    if reference.startswith("RFC"):
        return f"reference.RFC.{reference[3:]}.xml"
    return f"reference.{reference}.xml"


def sealed_references(config: ReconConfig) -> tuple[str, ...]:
    """Everything a workspace's refcache must hold, in a stable order.

    The declared references first, in the order the config gave them, then any
    of :data:`SKELETON_REFERENCES` the config did not already name. Additive
    and duplicate-free, so a config that declares RFC2119 itself is unaffected.

    Args:
        config: The validated configuration.

    Returns:
        The references to seal. Distinct from ``config.references``, which
        stays the record of what the operator declared.
    """
    sealed = list(config.references)
    sealed.extend(
        reference for reference in SKELETON_REFERENCES if reference not in sealed
    )
    return tuple(sealed)


def require_toolchain(config: ReconConfig) -> Toolchain | None:
    """Load the toolchain a config's references need, before anything is written.

    Every refusal a declared reference can earn is raised here, so a workspace
    is never half-built by a run that was always going to be refused.

    Args:
        config: The validated configuration.

    Returns:
        The loaded toolchain, or ``None`` when the config declares no
        references and none is needed.

    Raises:
        LifecycleError: If references are declared without a usable toolchain
            record, or the toolchain never cached one of them.
    """
    if not config.references:
        return None
    if config.toolchain is None or not config.toolchain.exists():
        raise LifecycleError(
            f"{config.name} declares references but no toolchain record exists "
            f"at {config.toolchain}; run `ai-rfc toolchain provision` first"
        )
    try:
        toolchain = load_toolchain(config.toolchain)
    except BuildError as error:
        raise LifecycleError(
            f"toolchain record {config.toolchain} is unusable: {error}"
        ) from None
    missing = [
        reference
        for reference in sealed_references(config)
        if not (toolchain.refcache / _reference_filename(reference)).exists()
    ]
    if missing:
        raise LifecycleError(
            f"the toolchain never cached {', '.join(missing)}; add them to "
            "the seed list and re-run `ai-rfc toolchain provision`"
        )
    return toolchain


def seal_references(
    config: ReconConfig, layout: Layout, toolchain: Toolchain | None
) -> tuple[str | None, str | None]:
    """Copy the references the workspace needs into it and record what it holds.

    Seals :func:`sealed_references`, not ``config.references``: the sealed cache
    overrides the toolchain's at build time, so it must also carry what the
    scaffolded draft cites on its own. ``references.yaml`` keeps naming the
    declared set, which is a different fact.

    Args:
        config: Names the declared references.
        layout: The workspace to seal them into.
        toolchain: The record they are copied from, from
            :func:`require_toolchain`.

    Returns:
        The digest over the sealed cache and the toolchain's template home,
        both ``None`` when nothing was sealed.
    """
    refcache_sha256: str | None = None
    template_home: str | None = None
    if toolchain is not None:
        cache = layout.refcache
        cache.mkdir()
        for reference in sealed_references(config):
            name = _reference_filename(reference)
            shutil.copyfile(toolchain.refcache / name, cache / name)
        refcache_sha256 = hashlib.sha256(
            b"".join((cache / p.name).read_bytes() for p in sorted(cache.iterdir()))
        ).hexdigest()
        template_home = str(toolchain.template_home)
    if config.references:
        (layout.root / REFERENCES_FILE).write_text(
            "references:\n"
            + "".join(f"- {reference}\n" for reference in config.references)
        )
    else:
        (layout.root / REFERENCES_FILE).write_text("references: []\n")
    return refcache_sha256, template_home


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
