"""``draft build``: the template's make, offline, in a scratch clone."""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ai_rfc.draft import build as build_module
from ai_rfc.draft.build import BuildError, build, load_toolchain, probe_toolchain
from ai_rfc.draft.cli import main

from .conftest import git

pytestmark = pytest.mark.unit

DATE = "2026-01-01T00:00:09+00:00"


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def toolchain(tmp_path: Path) -> Path:
    """A toolchain record whose executables exist and do nothing."""
    home = tmp_path / "tools" / "i-d-template"
    (home / "main.mk").parent.mkdir(parents=True)
    (home / "main.mk").write_text("txt:\n")
    refcache = tmp_path / "tools" / ".refcache"
    refcache.mkdir()
    (refcache / "reference.RFC.2119.xml").write_text("<reference/>\n")
    record = {
        "template_home": str(home),
        "template_commit": "0" * 40,
        "make": {"path": str(_executable(tmp_path / "bin" / "make"))},
        "python": {"venv": str(home / ".venv")},
        "ruby": {
            "bin_dir": str(tmp_path / "ruby-bin"),
            "gem_path": str(home / ".gems" / "ruby" / "4.0.0"),
            "kramdown_rfc": str(
                _executable(home / ".gems" / "ruby" / "4.0.0" / "bin" / "kramdown-rfc")
            ),
        },
        "node": {
            "bin_dir": str(tmp_path / "node-bin"),
            "idnits": str(
                _executable(tmp_path / "tools" / "node_modules" / ".bin" / "idnits")
            ),
        },
        "refcache": {"dir": str(refcache)},
    }
    path = tmp_path / "tools" / "toolchain.json"
    path.write_text(json.dumps(record, indent=2))
    return path


@pytest.fixture
def draft_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "draft"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "draft-test-spec.md").write_text("---\ntitle: T\n---\n\n# Intro\n")
    git(repo, "add", "draft-test-spec.md")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "revision 00"],
        check=True,
        env={**os.environ, "GIT_AUTHOR_DATE": DATE, "GIT_COMMITTER_DATE": DATE},
    )
    git(repo, "tag", "draft-test-spec-00")
    return repo


def _fake_make(*, returncode=0, trace=(), stderr="", outputs=("draft-test-spec.txt",)):
    """A runner standing in for make: writes the trace and the outputs."""
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        scratch = Path(argv[argv.index("-C") + 1])
        trace_path = Path(
            next(a for a in argv if a.startswith("TRACE_FILE=")).split("=", 1)[1]
        )
        trace_path.write_text("".join(line + "\n" for line in trace))
        for name in outputs:
            (scratch / name).write_text(f"built {name}\n")
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)

    runner.calls = calls
    return runner


def test_load_toolchain_names_the_missing_key(tmp_path):
    path = tmp_path / "toolchain.json"
    path.write_text(json.dumps({"template_home": "/x", "refcache": {"dir": "/x"}}))
    with pytest.raises(BuildError) as excinfo:
        load_toolchain(path)
    assert "'make'" in str(excinfo.value)


def test_probe_names_missing_executables(toolchain):
    record = load_toolchain(toolchain)
    assert probe_toolchain(record) == ()
    Path(record.idnits).unlink()
    assert probe_toolchain(record) == (f"idnits: {record.idnits}",)


def test_build_runs_make_offline_in_a_scratch_clone(toolchain, draft_repo, tmp_path):
    record = load_toolchain(toolchain)
    runner = _fake_make(
        trace=("draft-test-spec kramdown-rfc 0", "draft-test-spec xml2rfc-txt 0")
    )
    out = tmp_path / "out"
    report = build(draft_repo, toolchain=record, out=out, runner=runner)

    argv, kwargs = runner.calls[0]
    scratch = out / "build" / "scratch"
    assert argv[:3] == [record.make, "-C", str(scratch)]
    assert argv[3:5] == ["-f", str(record.template_home / "main.mk")]
    assert f"LIBDIR={record.template_home}" in argv
    assert f"kramdown-rfc={record.kramdown_rfc}" in argv
    assert (
        f"GEM_PATH={record.gem_path}" in argv and f"GEM_HOME={record.gem_path}" in argv
    )
    assert "KRAMDOWN_OFFLINE=1" in argv and "NO_NODEJS=true" in argv
    assert f"idnits={record.idnits}" in argv and "idnits_bin=" in argv
    assert "idnits_mode=submission" in argv
    opts = next(a for a in argv if a.startswith("XML2RFC_OPTS="))
    assert f"-N --cache={record.refcache} -D 2026-01-01" in opts
    assert argv[-4:] == ["txt", "html", "lint", "idnits"]
    env = kwargs["env"]
    assert env["PATH"].startswith(f"{record.bin_dirs[0]}:")
    assert env["http_proxy"] == "http://127.0.0.1:9" and env["KRAMDOWN_OFFLINE"] == "1"
    assert env["KRAMDOWN_REFCACHEDIR"] == str(record.refcache)

    assert (
        git(scratch, "rev-parse", "HEAD").strip()
        == git(draft_repo, "rev-parse", "HEAD").strip()
    )
    assert git(draft_repo, "status", "--porcelain") == ""
    assert report.exit_code == 0 and report.findings == ()
    assert [s["stage"] for s in report.stages] == ["kramdown-rfc", "xml2rfc-txt"]
    assert (
        report.outputs["draft-test-spec.txt"]["sha256"]
        == hashlib.sha256(b"built draft-test-spec.txt\n").hexdigest()
    )
    assert (
        out / "build" / "draft-test-spec.txt"
    ).read_text() == "built draft-test-spec.txt\n"
    expected_source = hashlib.sha256(
        (draft_repo / "draft-test-spec.md").read_bytes()
    ).hexdigest()
    assert report.source_sha256 == expected_source and report.date == "2026-01-01"
    written = json.loads((out / "build" / "build-report.json").read_text())
    assert written["exit_code"] == 0 and written["offline"] is True


def test_build_accepts_relative_paths_from_another_cwd(
    toolchain, draft_repo, tmp_path, monkeypatch
):
    """A nested relative draft path and a relative --out, from a working
    directory above both, must resolve correctly rather than have git's -C
    re-root them against each other."""
    monkeypatch.chdir(tmp_path.parent)
    report = build(
        Path(tmp_path.name) / "draft",
        toolchain=load_toolchain(toolchain),
        out=Path(tmp_path.name) / "out",
        runner=_fake_make(trace=("draft-test-spec kramdown-rfc 0",)),
    )
    assert report.exit_code == 0
    assert Path(report.argv[2]) == tmp_path / "out" / "build" / "scratch"
    assert (tmp_path / "out" / "build" / "scratch" / ".git").exists()
    assert not (tmp_path / tmp_path.name).exists()
    assert (tmp_path / "out" / "build" / "build-report.json").exists()


def test_a_broken_reference_is_a_finding_even_when_make_exits_zero(
    toolchain, draft_repo, tmp_path
):
    record = load_toolchain(toolchain)
    runner = _fake_make(
        trace=("draft-test-spec kramdown-rfc 0",),
        stderr="*** KRAMDOWN_OFFLINE: Inserting broken reference for RFC9999\n",
    )
    report = build(draft_repo, toolchain=record, out=tmp_path / "out", runner=runner)
    assert report.broken_references == ("RFC9999",)
    assert report.findings == ("broken reference RFC9999 (not in the refcache)",)


@pytest.mark.parametrize(
    ("token", "key"),
    (
        ("reference.RFC.9999.xml", "RFC9999"),
        ("reference.I-D.ietf-quic-http-22.xml", "I-D.ietf-quic-http-22"),
        ("RFC9999", "RFC9999"),
    ),
)
def test_reference_key_recovers_the_citation_key(token, key):
    assert build_module._reference_key(token) == key


def test_a_broken_reference_names_the_real_toolchains_actual_wording(
    toolchain, draft_repo, tmp_path
):
    """The regex fires on the real toolchain's actual wording too, and the
    reported identifier is normalized back to the citation key.

    Verified against a real build on 2026-09-04 (Task 1 report, Deviations):
    kramdown-rfc names the refcache file it tried to fetch
    (``reference.RFC.9999.xml``), not the bare citation key written in the
    front matter (``RFC9999``); the finding must name the key so an author
    knows which entry to fix.
    """
    record = load_toolchain(toolchain)
    runner = _fake_make(
        trace=("draft-test-spec kramdown-rfc 0",),
        stderr=(
            "*** KRAMDOWN_OFFLINE: Inserting broken reference for "
            "reference.RFC.9999.xml\n"
        ),
    )
    report = build(draft_repo, toolchain=record, out=tmp_path / "out", runner=runner)
    assert report.broken_references == ("RFC9999",)
    assert report.findings == ("broken reference RFC9999 (not in the refcache)",)


def test_an_xml2rfc_unresolved_request_is_a_broken_reference(
    toolchain, draft_repo, tmp_path
):
    record = load_toolchain(toolchain)
    runner = _fake_make(
        trace=("draft-test-spec xml2rfc-txt 0",),
        stderr="Unable to resolve external request: reference.RFC.9999.xml\n",
    )
    report = build(draft_repo, toolchain=record, out=tmp_path / "out", runner=runner)
    assert report.broken_references == ("RFC9999",)
    assert report.findings == ("broken reference RFC9999 (not in the refcache)",)
    assert report.diagnostics == (
        {
            "tool": "xml2rfc",
            "severity": "error",
            "message": "Unable to resolve external request: reference.RFC.9999.xml",
        },
    )


def test_a_failed_stage_is_named_with_its_stderr(toolchain, draft_repo, tmp_path):
    record = load_toolchain(toolchain)
    runner = _fake_make(
        returncode=2,
        trace=(
            "draft-test-spec kramdown-rfc 1",
            "draft-test-spec kramdown-rfc Error: bad front matter",
        ),
        outputs=(),
    )
    report = build(draft_repo, toolchain=record, out=tmp_path / "out", runner=runner)
    assert report.exit_code == 2
    assert report.stages[0]["status"] == 1
    assert report.stages[0]["stderr"] == ["Error: bad front matter"]
    assert report.findings == (
        "draft-test-spec: stage kramdown-rfc failed (Error: bad front matter)",
    )


def test_build_refuses_an_unknown_ref(toolchain, draft_repo, tmp_path):
    with pytest.raises(BuildError) as excinfo:
        build(
            draft_repo,
            toolchain=load_toolchain(toolchain),
            out=tmp_path / "out",
            ref="nope",
            runner=_fake_make(),
        )
    assert "nope" in str(excinfo.value)


def test_a_stale_build_report_does_not_survive_a_refused_build(
    toolchain, draft_repo, tmp_path
):
    """A build that raises before writing a fresh report — here, an unknown
    ref — must not leave a previous run's report looking current."""
    record = load_toolchain(toolchain)
    out = tmp_path / "out"
    stale = out / "build" / "build-report.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    with pytest.raises(BuildError):
        build(draft_repo, toolchain=record, out=out, ref="nope", runner=_fake_make())
    assert not stale.exists()


def test_cli_build_exits_three_only_under_strict(
    toolchain, draft_repo, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        build_module,
        "DEFAULT_RUNNER",
        _fake_make(
            stderr="*** KRAMDOWN_OFFLINE: Inserting broken reference for RFC9999\n"
        ),
    )
    argv = [
        "build",
        str(draft_repo),
        "--out",
        str(tmp_path / "out"),
        "--toolchain",
        str(toolchain),
    ]
    assert main(argv) == 0
    assert "broken reference RFC9999" in capsys.readouterr().err
    assert main(argv + ["--strict"]) == 3


def test_cli_build_without_a_toolchain_exits_one(
    draft_repo, tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    assert main(["build", str(draft_repo), "--out", str(tmp_path / "out")]) == 1
    assert "AI_RFC_TOOLCHAIN" in capsys.readouterr().err


@pytest.mark.skipif(
    not os.environ.get("AI_RFC_TOOLCHAIN"), reason="needs a provisioned toolchain"
)
def test_the_template_example_builds_with_the_real_toolchain(tmp_path):
    record = load_toolchain(Path(os.environ["AI_RFC_TOOLCHAIN"]))
    repo = tmp_path / "example"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    example = record.template_home / "example" / "draft-todo-yourname-protocol.md"
    (repo / example.name).write_text(example.read_text())
    (repo / "Makefile").write_text(
        (record.template_home / "template" / "Makefile").read_text()
    )
    git(repo, "add", example.name, "Makefile")
    git(repo, "commit", "-q", "-m", "example")
    report = build(
        repo, toolchain=record, out=tmp_path / "out", targets=("txt", "html")
    )
    assert report.exit_code == 0 and report.findings == ()
    assert "draft-todo-yourname-protocol.txt" in report.outputs
