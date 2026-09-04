"""Build one draft revision with the Internet-Draft template's own toolchain.

The template (auto-i-d-template, a fork of martinthomson/i-d-template) is the
build: ``make`` renders kramdown-rfc markdown to XML, then xml2rfc to text and
HTML, lints whitespace and the docname, and runs idnits. Nothing here
reimplements any of that; this module clones the draft at a ref into a scratch
directory, runs ``make`` there with the network denied, and turns the
template's trace file into a report.

Offline is a property of the invocation, not of the tools: ``KRAMDOWN_OFFLINE``
makes kramdown-rfc read only its reference cache (and insert a *stub* for a
missing reference, which this module promotes to an error), ``xml2rfc -N``
refuses network fetches outright, and a black-hole proxy catches anything
else. A build therefore reproduces byte-for-byte from the same cache and the
same ``-D`` date.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .gate import GateError, draft_text

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

TOOLCHAIN_ENV = "AI_RFC_TOOLCHAIN"
BUILD_DIR = "build"
SCRATCH_DIR = "scratch"
REPORT_FILE = "build-report.json"
TRACE_FILE = "trace.txt"
DEFAULT_TARGETS: tuple[str, ...] = ("txt", "html", "lint", "idnits")
BLACKHOLE_PROXY = "http://127.0.0.1:9"
XML2RFC_BASE_OPTS = (
    "-q --rfc-base-url https://www.rfc-editor.org/rfc/ "
    "--id-base-url https://datatracker.ietf.org/doc/html/ "
    "--allow-local-file-access -N"
)
OUTPUT_SUFFIXES = (".txt", ".html")

_OFFLINE_STUB = re.compile(r"KRAMDOWN_OFFLINE: Inserting broken reference for (\S+)")
_XML2RFC_UNRESOLVED = re.compile(r"Unable to resolve external request: (\S+)")
_KRAMDOWN_WARNING = re.compile(r"^\*\* \((.*)\)\s*$")
_IDNITS_SUMMARY = re.compile(r"^\s*(ERROR|WARNING|COMMENT)\s+(\d+) nit")
#: The template's ``trace.sh`` writes ``<draft> <stage> <status>`` per stage and,
#: on failure, ``<draft> <stage> <stderr line>`` for the last stderr lines.
_TRACE_STATUS = re.compile(r"^(\S+) (\S+) (\d+)$")
#: A numbered series's refcache filename, once ``reference.``/``.xml`` are gone.
_REFERENCE_SERIES = re.compile(r"^(RFC|BCP|STD|FYI)\.(\d+)$")

DEFAULT_RUNNER: Runner = subprocess.run


class BuildError(RuntimeError):
    """Raised when the build cannot even start: no toolchain, no such ref."""


@dataclass(frozen=True)
class Toolchain:
    """The executables and directories a build needs, read from ``toolchain.json``."""

    path: Path
    template_home: Path
    template_commit: str
    refcache: Path
    make: str
    kramdown_rfc: str
    gem_path: str
    idnits: str
    #: Prepended to PATH, in order: the Ruby, Node and template-venv bin dirs.
    bin_dirs: tuple[str, ...]


def load_toolchain(path: Path) -> Toolchain:
    """Read a toolchain record.

    Args:
        path: The ``toolchain.json`` written by ``experiment toolchain provision``.

    Returns:
        The toolchain.

    Raises:
        BuildError: If the file is unreadable or lacks a required key.
    """
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BuildError(f"{path}: unreadable toolchain record: {error}") from None
    try:
        return Toolchain(
            path=path,
            template_home=Path(record["template_home"]),
            template_commit=str(record.get("template_commit", "")),
            refcache=Path(record["refcache"]["dir"]),
            make=record["make"]["path"],
            kramdown_rfc=record["ruby"]["kramdown_rfc"],
            gem_path=record["ruby"]["gem_path"],
            idnits=record["node"]["idnits"],
            bin_dirs=(
                record["ruby"]["bin_dir"],
                record["node"]["bin_dir"],
                str(Path(record["python"]["venv"]) / "bin"),
            ),
        )
    except (KeyError, TypeError) as error:
        raise BuildError(f"{path}: toolchain record lacks {error}") from None


def probe_toolchain(toolchain: Toolchain) -> tuple[str, ...]:
    """Name every executable or directory the record points at that is missing.

    Args:
        toolchain: The record to probe.

    Returns:
        ``"<label>: <path>"`` per missing item; empty when everything exists.
    """
    missing: list[str] = []
    for label, candidate in (
        ("make", toolchain.make),
        ("kramdown-rfc", toolchain.kramdown_rfc),
        ("idnits", toolchain.idnits),
    ):
        if not (Path(candidate).is_file() and os.access(candidate, os.X_OK)):
            missing.append(f"{label}: {candidate}")
    for label, path in (
        ("template_home", toolchain.template_home / "main.mk"),
        ("refcache", toolchain.refcache),
    ):
        if not path.exists():
            missing.append(f"{label}: {path}")
    return tuple(missing)


@dataclass(frozen=True)
class BuildReport:
    """What one build did and what it found."""

    ref: str
    commit: str
    draft: str
    source_sha256: str
    date: str
    targets: tuple[str, ...]
    exit_code: int
    argv: tuple[str, ...]
    template: dict[str, str]
    refcache: str
    stages: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, str], ...]
    broken_references: tuple[str, ...]
    idnits: dict[str, int]
    outputs: dict[str, dict[str, str]]
    offline: bool = True

    @property
    def findings(self) -> tuple[str, ...]:
        """Every reason this revision does not compile cleanly."""
        found: list[str] = []
        for stage in self.stages:
            if stage["status"] != 0:
                detail = "; ".join(stage["stderr"][-3:]) or f"exit {stage['status']}"
                found.append(
                    f"{stage['draft']}: stage {stage['stage']} failed ({detail})"
                )
        if self.exit_code != 0 and not found:
            found.append(
                f"make exited {self.exit_code} without a failed stage in the trace"
            )
        for reference in self.broken_references:
            found.append(f"broken reference {reference} (not in the refcache)")
        if self.idnits.get("ERROR", 0):
            found.append(f"idnits reported {self.idnits['ERROR']} error(s)")
        return tuple(found)

    def to_json(self) -> str:
        """Serialise deterministically, derived ``findings`` included."""
        payload = asdict(self)
        payload["findings"] = list(self.findings)
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git inside ``repo`` without raising; callers read the exit code."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def _parse_trace(path: Path) -> tuple[dict[str, Any], ...]:
    stages: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return ()
    for line in path.read_text().splitlines():
        match = _TRACE_STATUS.match(line)
        if match:
            draft, stage, status = match.groups()
            stages[(draft, stage)] = {
                "draft": draft,
                "stage": stage,
                "status": int(status),
                "stderr": [],
            }
            continue
        draft, _, rest = line.partition(" ")
        stage, _, text = rest.partition(" ")
        if (draft, stage) in stages:
            stages[(draft, stage)]["stderr"].append(text)
    return tuple(stages.values())


def _reference_key(token: str) -> str:
    """Recover the citation key an author wrote from a refcache filename.

    kramdown-rfc's offline-stub message and xml2rfc's unresolved-request
    message both name the cache file they tried to fetch
    (``reference.RFC.9999.xml``), not the key the draft's front matter cited
    (``RFC9999``); a finding must name the key so an author knows which
    entry to fix. Any other shape (already a bare key, or an I-D name that
    is itself the citation key) passes through unchanged.

    Args:
        token: The whitespace-delimited token a regex captured.

    Returns:
        The citation key.
    """
    key = token.removeprefix("reference.").removesuffix(".xml")
    series = _REFERENCE_SERIES.match(key)
    if series:
        key = series.group(1) + series.group(2)
    return key


def _parse_output(
    text: str,
) -> tuple[tuple[dict[str, str], ...], tuple[str, ...], dict[str, int]]:
    diagnostics: list[dict[str, str]] = []
    broken: list[str] = []
    idnits: dict[str, int] = {}
    for line in text.splitlines():
        stub = _OFFLINE_STUB.search(line)
        if stub:
            broken.append(_reference_key(stub.group(1)))
            diagnostics.append(
                {"tool": "kramdown-rfc", "severity": "error", "message": line.strip()}
            )
            continue
        unresolved = _XML2RFC_UNRESOLVED.search(line)
        if unresolved:
            broken.append(_reference_key(unresolved.group(1)))
            diagnostics.append(
                {"tool": "xml2rfc", "severity": "error", "message": line.strip()}
            )
            continue
        warning = _KRAMDOWN_WARNING.match(line)
        if warning:
            diagnostics.append(
                {
                    "tool": "kramdown-rfc",
                    "severity": "warning",
                    "message": warning.group(1),
                }
            )
            continue
        summary = _IDNITS_SUMMARY.match(line)
        if summary:
            idnits[summary.group(1)] = int(summary.group(2))
    return tuple(diagnostics), tuple(dict.fromkeys(broken)), idnits


def build(
    draft_repo: Path,
    *,
    toolchain: Toolchain,
    out: Path,
    ref: str = "HEAD",
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    date: str | None = None,
    refcache: Path | None = None,
    runner: Runner | None = None,
) -> BuildReport:
    """Build the draft as it stands at ``ref`` and write ``build-report.json``.

    Args:
        draft_repo: The nested prose-draft git repository.
        toolchain: Where the template and its tools are.
        out: Directory that receives ``build/`` (scratch clone, trace, report,
            rendered outputs).
        ref: Tag, branch or commit to build; the working tree is never built.
        targets: The make targets, in order.
        date: ``YYYY-MM-DD`` for xml2rfc ``-D``; defaults to the commit date of
            ``ref`` so a rebuild reproduces the same bytes.
        refcache: A reference cache overriding the toolchain's (a workspace's
            sealed ``refcache/``).
        runner: A ``subprocess.run`` stand-in; tests inject a fake make.

    Returns:
        The report, also written to ``out/build/build-report.json``.

    Raises:
        BuildError: If ``ref`` does not resolve or names no single draft file.
    """
    run = runner or DEFAULT_RUNNER
    draft_repo = draft_repo.resolve()
    out = out.resolve()
    resolved = _git(draft_repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if resolved.returncode != 0:
        raise BuildError(
            f"{ref}: not a commit in {draft_repo}: {resolved.stderr.strip()}"
        )
    commit = resolved.stdout.strip()
    try:
        draft, text = draft_text(draft_repo, commit)
    except GateError as error:
        raise BuildError(str(error)) from None
    built_date = (
        date or _git(draft_repo, "log", "-1", "--format=%cs", commit).stdout.strip()
    )
    cache = refcache or toolchain.refcache

    build_dir = out / BUILD_DIR
    scratch = build_dir / SCRATCH_DIR
    if scratch.exists():
        shutil.rmtree(scratch)
    build_dir.mkdir(parents=True, exist_ok=True)
    cloned = _git(
        build_dir,
        "clone",
        "-q",
        "--no-hardlinks",
        str(draft_repo),
        str(scratch),
    )
    if cloned.returncode != 0:
        raise BuildError(f"could not clone {draft_repo}: {cloned.stderr.strip()}")
    checked = _git(scratch, "checkout", "-q", "--detach", commit)
    if checked.returncode != 0:
        raise BuildError(f"could not check out {commit}: {checked.stderr.strip()}")
    trace = build_dir / TRACE_FILE
    if trace.exists():
        trace.unlink()

    argv = [
        toolchain.make,
        "-C",
        str(scratch),
        "-f",
        str(toolchain.template_home / "main.mk"),
        f"LIBDIR={toolchain.template_home}",
        f"GEM_PATH={toolchain.gem_path}",
        f"GEM_HOME={toolchain.gem_path}",
        f"kramdown-rfc={toolchain.kramdown_rfc}",
        "DEFAULT_BRANCH=main",
        "BRANCH_FETCH=false",
        "NO_NODEJS=true",
        f"KRAMDOWN_REFCACHEDIR={cache}",
        "KRAMDOWN_OFFLINE=1",
        f"XML2RFC_OPTS={XML2RFC_BASE_OPTS} --cache={cache} -D {built_date}",
        f"idnits={toolchain.idnits}",
        "idnits_bin=",
        f"TRACE_FILE={trace}",
        *targets,
    ]
    env = {
        "PATH": ":".join((*toolchain.bin_dirs, "/usr/bin", "/bin")),
        "HOME": os.environ.get("HOME", str(build_dir)),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "KRAMDOWN_OFFLINE": "1",
        "KRAMDOWN_REFCACHEDIR": str(cache),
        "XML2RFC_REFCACHEDIR": str(cache),
        "http_proxy": BLACKHOLE_PROXY,
        "https_proxy": BLACKHOLE_PROXY,
        "HTTP_PROXY": BLACKHOLE_PROXY,
        "HTTPS_PROXY": BLACKHOLE_PROXY,
    }
    result = run(argv, capture_output=True, text=True, env=env, cwd=str(scratch))
    diagnostics, broken, idnits = _parse_output(
        (result.stdout or "") + "\n" + (result.stderr or "")
    )

    outputs: dict[str, dict[str, str]] = {}
    for produced in sorted(scratch.iterdir()):
        if produced.suffix in OUTPUT_SUFFIXES and produced.name.startswith("draft-"):
            copied = build_dir / produced.name
            shutil.copyfile(produced, copied)
            outputs[produced.name] = {
                "path": str(copied),
                "sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
            }

    report = BuildReport(
        ref=ref,
        commit=commit,
        draft=draft,
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        date=built_date,
        targets=tuple(targets),
        exit_code=result.returncode,
        argv=tuple(argv),
        template={
            "path": str(toolchain.template_home),
            "commit": toolchain.template_commit,
        },
        refcache=str(cache),
        stages=_parse_trace(trace),
        diagnostics=diagnostics,
        broken_references=broken,
        idnits=idnits,
        outputs=outputs,
    )
    (build_dir / REPORT_FILE).write_text(report.to_json())
    return report
