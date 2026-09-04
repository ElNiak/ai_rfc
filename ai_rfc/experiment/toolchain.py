"""Provision and verify the shared Internet-Draft toolchain, once per machine.

One pinned template checkout under ``<root>/tools/`` carries the venv
(xml2rfc), the gems (kramdown-rfc), the node tools (idnits, aasvg) and a
reference cache seeded by one online build. ``toolchain.json`` records every
path and version; ``ai_rfc.draft.build`` reads it and nothing else. The
2026-09-03 run-and-see is the specification: this module reproduces it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_rfc.draft.build import build, load_toolchain, probe_toolchain

from . import ExperimentError
from .workspace import TEMPLATE_COMMIT, TEMPLATE_URL, _git, _run_git

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

TOOLS_DIR = "tools"
TEMPLATE_DIR = "i-d-template"
RECORD_FILE = "toolchain.json"
REFCACHE_DIGEST = "refcache.sha256"
REFCACHE_DIR = ".refcache"
PROBE_DIR = "probe"
SEED_DRAFT = "draft-seed-refs.md"
EXAMPLE_DRAFT = "draft-todo-yourname-protocol.md"
NODE_PACKAGES = ("aasvg", "@ietf-tools/idnits")
#: Everything the two known targets cite; the seed build caches each once.
DEFAULT_REFERENCES: tuple[str, ...] = (
    "RFC2119",
    "RFC8174",
    "RFC9000",
    "RFC9001",
    "RFC9002",
    "RFC9114",
    "RFC9204",
    "RFC8446",
    "RFC5280",
    "RFC6125",
    "RFC7322",
    "RFC2360",
    "RFC3552",
    "RFC9110",
    "RFC8259",
)
VERIFY_DATE = "2026-08-26"
_SEED_FRONT = """---
title: "Reference Cache Seed"
abbrev: "Ref Seed"
docname: draft-seed-refs-latest
category: info
ipr: trust200902
area: General
workgroup: Individual Submission
keyword: Internet-Draft
stand_alone: yes
smart_quotes: no
pi: [toc, sortrefs, symrefs]
author:
 -
    ins: A. Harness
    name: ai-rfc harness
    organization: none
    email: ai-rfc-harness@localhost
"""


def _make_variables(home: Path, record: dict[str, Any] | None = None) -> list[str]:
    variables = [
        f"LIBDIR={home}",
        "DEFAULT_BRANCH=main",
        "BRANCH_FETCH=false",
        "NO_NODEJS=true",
    ]
    if record:
        variables += [
            f"GEM_PATH={record['ruby']['gem_path']}",
            f"GEM_HOME={record['ruby']['gem_path']}",
            f"kramdown-rfc={record['ruby']['kramdown_rfc']}",
        ]
    return variables


def _version(run: Runner, *argv: str) -> str:
    try:
        result = run(list(argv), capture_output=True, text=True)
    except OSError as error:
        raise ExperimentError(f"cannot run {argv[0]}: {error}") from None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[-1] if text else ""


def _seed_draft(references: tuple[str, ...]) -> str:
    listed = "\n".join(
        f"  {ref}:" for ref in references if ref not in ("RFC2119", "RFC8174")
    )
    cited = ", ".join(
        f"{{{{{ref}}}}}" for ref in references if ref not in ("RFC2119", "RFC8174")
    )
    return (
        _SEED_FRONT
        + f"\nnormative:\n{listed}\n\n--- abstract\n\nSeeds a reference cache.\n\n"
        "--- middle\n\n# Introduction\n\n{::boilerplate bcp14-tagged}\n\n"
        f"This draft cites {cited} so that each is fetched once.\n\n"
        "# Security Considerations\n\nNone.\n\n"
        "# IANA Considerations\n\nNone.\n\n--- back\n"
    )


def _digest_refcache(refcache: Path) -> str:
    lines = []
    for entry in sorted(refcache.glob("*.xml")):
        lines.append(f"{hashlib.sha256(entry.read_bytes()).hexdigest()}  {entry.name}")
    return "\n".join(lines) + ("\n" if lines else "")


def provision(
    root: Path,
    *,
    template: str = TEMPLATE_URL,
    template_commit: str = TEMPLATE_COMMIT,
    references: tuple[str, ...] = DEFAULT_REFERENCES,
    runner: Runner | None = None,
) -> Path:
    """Install the toolchain under ``root/tools`` and write its record.

    The only networked command in the harness: it clones the template, lets
    the template's ``make deps`` install the venv and gems, installs the node
    tools, and builds a seed draft once online to fill the reference cache.

    Args:
        root: The experiments root (``AI_RFC_EXPERIMENTS_ROOT``).
        template: Template clone source (URL or local path).
        template_commit: The commit to pin.
        references: The reference ids to cache.
        runner: ``subprocess.run`` stand-in for make/npm/version probes.

    Returns:
        The record path.

    Raises:
        ExperimentError: If a record exists or any step fails.
    """
    run = runner or subprocess.run
    tools = root / TOOLS_DIR
    record_path = tools / RECORD_FILE
    if record_path.exists():
        raise ExperimentError(f"{record_path} exists; a toolchain is provisioned once")
    home = tools / TEMPLATE_DIR
    if home.exists():
        raise ExperimentError(f"{home} exists; remove it to re-provision")
    tools.mkdir(parents=True, exist_ok=True)
    cloned = _run_git("clone", "-q", template, str(home))
    if cloned.returncode != 0:
        raise ExperimentError(f"cloning {template} failed: {cloned.stderr.strip()}")
    _git(home, "checkout", "-q", template_commit)
    shutil.rmtree(home / ".git")

    probe = tools / PROBE_DIR
    probe.mkdir(exist_ok=True)
    shutil.copyfile(home / "template" / "Makefile", probe / "Makefile")
    shutil.copyfile(home / "example" / EXAMPLE_DRAFT, probe / EXAMPLE_DRAFT)
    (probe / SEED_DRAFT).write_text(_seed_draft(references))

    make = shutil.which("gmake") or shutil.which("make") or "make"
    deps = run(
        [
            make,
            "-C",
            str(probe),
            "-f",
            str(home / "main.mk"),
            *_make_variables(home),
            "deps",
        ],
        capture_output=True,
        text=True,
    )
    if deps.returncode != 0:
        raise ExperimentError(f"make deps failed:\n{deps.stderr[-2000:]}")
    npm = run(
        ["npm", "install", "--prefix", str(tools), "--no-save", *NODE_PACKAGES],
        capture_output=True,
        text=True,
    )
    if npm.returncode != 0:
        raise ExperimentError(f"npm install failed:\n{npm.stderr[-2000:]}")

    binstubs = sorted(home.glob(".gems/ruby/*/bin/kramdown-rfc"))
    if not binstubs:
        raise ExperimentError(
            f"no kramdown-rfc binstub under {home / '.gems'}; did bundle install run?"
        )
    kramdown = binstubs[-1]
    gem_path = kramdown.parent.parent
    ruby = shutil.which("ruby")
    node = shutil.which("node")
    if ruby is None or node is None:
        raise ExperimentError("ruby and node must be on PATH to provision")
    record: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "native",
        "template_home": str(home),
        "template_commit": template_commit,
        "template_source": template,
        "make": {"path": make, "version": _version(run, make, "--version")},
        "python": {
            "venv": str(home / ".venv"),
            "xml2rfc": str(home / ".venv" / "bin" / "xml2rfc"),
            "xml2rfc_version": _version(
                run, str(home / ".venv" / "bin" / "xml2rfc"), "--version"
            ),
        },
        "ruby": {
            "bin_dir": str(Path(ruby).parent),
            "version": _version(run, ruby, "--version"),
            "gem_path": str(gem_path),
            "kramdown_rfc": str(kramdown),
            "gemfile_lock": (
                (home / "Gemfile.lock").read_text()
                if (home / "Gemfile.lock").exists()
                else ""
            ),
        },
        "node": {
            "bin_dir": str(Path(node).parent),
            "version": _version(run, node, "--version"),
            "idnits": str(tools / "node_modules" / ".bin" / "idnits"),
            "aasvg": str(tools / "node_modules" / ".bin" / "aasvg"),
        },
        "refcache": {
            "dir": str(tools / REFCACHE_DIR),
            "entries": list(references),
            "seed_draft": str(probe / SEED_DRAFT),
            "digest_file": str(tools / REFCACHE_DIGEST),
        },
    }
    seed = run(
        [
            make,
            "-C",
            str(probe),
            "-f",
            str(home / "main.mk"),
            *_make_variables(home, record),
            f"KRAMDOWN_REFCACHEDIR={tools / REFCACHE_DIR}",
            "txt",
        ],
        capture_output=True,
        text=True,
    )
    if seed.returncode != 0:
        raise ExperimentError(f"the online seed build failed:\n{seed.stderr[-2000:]}")
    missing = [
        ref
        for ref in references
        if not (tools / REFCACHE_DIR / f"reference.RFC.{ref[3:]}.xml").exists()
    ]
    if missing:
        raise ExperimentError(f"the seed build cached nothing for {', '.join(missing)}")
    (tools / REFCACHE_DIGEST).write_text(_digest_refcache(tools / REFCACHE_DIR))
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    ok, reasons = verify(record_path, runner=run)
    if not ok:
        raise ExperimentError("provisioned, but verify failed: " + "; ".join(reasons))
    return record_path


def verify(
    record: Path, *, runner: Runner | None = None
) -> tuple[bool, tuple[str, ...]]:
    """Re-check a toolchain record offline: executables, refcache, reproducibility.

    Args:
        record: The ``toolchain.json`` to verify.
        runner: ``subprocess.run`` stand-in for the make invocations.

    Returns:
        ``(ok, reasons)``; ``reasons`` is empty when ok.
    """
    reasons: list[str] = []
    try:
        toolchain = load_toolchain(record)
    except Exception as error:  # noqa: BLE001 - every failure is a reason, not a crash
        return False, (str(error),)
    reasons.extend(probe_toolchain(toolchain))
    tools = record.parent
    digest_file = tools / REFCACHE_DIGEST
    if not digest_file.exists():
        reasons.append(f"refcache digest {digest_file} is missing")
    elif digest_file.read_text() != _digest_refcache(toolchain.refcache):
        reasons.append("refcache contents differ from the recorded digest")
    if reasons:
        return False, tuple(reasons)
    example = toolchain.template_home / "example" / EXAMPLE_DRAFT
    # The scratch lives outside the toolchain root: tools/ is evidence, and
    # campaign init calls verify on every init.
    with tempfile.TemporaryDirectory(prefix="ai-rfc-toolchain-verify-") as staging:
        scratch = Path(staging)
        repo = scratch / "example"
        repo.mkdir(parents=True)
        shutil.copyfile(example, repo / EXAMPLE_DRAFT)
        shutil.copyfile(
            toolchain.template_home / "template" / "Makefile", repo / "Makefile"
        )
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.name", "ai-rfc-harness")
        _git(repo, "config", "user.email", "ai-rfc-harness@localhost")
        _git(repo, "add", EXAMPLE_DRAFT, "Makefile")
        _git(repo, "commit", "-q", "-m", "example", date="2026-08-26T00:00:00+00:00")
        digests = []
        for attempt in ("first", "second"):
            report = build(
                repo,
                toolchain=toolchain,
                out=scratch / attempt,
                targets=("txt",),
                date=VERIFY_DATE,
                runner=runner,
            )
            if report.exit_code != 0 or report.findings:
                reasons.append(
                    f"the example did not build offline ({attempt}): "
                    f"{'; '.join(report.findings) or report.exit_code}"
                )
                return False, tuple(reasons)
            digests.append(
                {name: entry["sha256"] for name, entry in report.outputs.items()}
            )
    if digests[0] != digests[1]:
        reasons.append("two offline builds of the example differ")
    return not reasons, tuple(reasons)
