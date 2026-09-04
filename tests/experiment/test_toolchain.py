"""Provisioning writes a record a build can trust; verify re-checks it offline."""

import json
import stat
import subprocess
from pathlib import Path

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.toolchain import (
    DEFAULT_REFERENCES,
    RECORD_FILE,
    REFCACHE_DIGEST,
    TOOLS_DIR,
    provision,
    verify,
)


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_tools(root: Path):
    """A runner that pretends make, npm and the tools succeeded."""
    calls = []
    home = root / TOOLS_DIR / "i-d-template"

    def run(argv, **kwargs):
        calls.append(argv)
        program = Path(argv[0]).name
        if program == "make":
            if "deps" in argv:
                _make_executable(home / ".venv" / "bin" / "xml2rfc")
                _make_executable(
                    home / ".gems" / "ruby" / "4.0.0" / "bin" / "kramdown-rfc"
                )
                (home / "Gemfile.lock").write_text("GEM\n  kramdown-rfc (1.7.43)\n")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            if "txt" in argv:
                scratch = Path(argv[argv.index("-C") + 1])
                cache = Path(
                    next(
                        a for a in argv if a.startswith("KRAMDOWN_REFCACHEDIR=")
                    ).split("=", 1)[1]
                )
                cache.mkdir(parents=True, exist_ok=True)
                for reference in DEFAULT_REFERENCES:
                    number = reference[3:]
                    (cache / f"reference.RFC.{number}.xml").write_text(
                        f"<reference anchor='{reference}'/>\n"
                    )
                trace = next((a for a in argv if a.startswith("TRACE_FILE=")), None)
                if trace:
                    Path(trace.split("=", 1)[1]).write_text(
                        "draft-todo-yourname-protocol xml2rfc-txt 0\n"
                    )
                for source in scratch.glob("draft-*.md"):
                    (scratch / (source.stem + ".txt")).write_text(
                        f"rendered {source.stem}\n"
                    )
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            # A bare version probe (`make --version`), not a deps/build
            # invocation: falls through to the generic version stub below.
        if program == "npm":
            _make_executable(root / TOOLS_DIR / "node_modules" / ".bin" / "idnits")
            _make_executable(root / TOOLS_DIR / "node_modules" / ".bin" / "aasvg")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="9.9.9\n", stderr="")

    run.calls = calls
    return run


def test_provision_writes_a_complete_record(tmp_path, template_repo):
    template, commit = template_repo
    root = tmp_path / "root"
    fake = _fake_tools(root)
    record = provision(root, template=template, template_commit=commit, runner=fake)
    assert record == root / TOOLS_DIR / RECORD_FILE
    payload = json.loads(record.read_text())
    home = root / TOOLS_DIR / "i-d-template"
    assert (
        payload["template_home"] == str(home) and payload["template_commit"] == commit
    )
    assert payload["ruby"]["kramdown_rfc"] == str(
        home / ".gems" / "ruby" / "4.0.0" / "bin" / "kramdown-rfc"
    )
    assert payload["ruby"]["gem_path"] == str(home / ".gems" / "ruby" / "4.0.0")
    assert payload["node"]["idnits"].endswith("node_modules/.bin/idnits")
    assert payload["refcache"]["entries"] == list(DEFAULT_REFERENCES)
    assert (root / TOOLS_DIR / REFCACHE_DIGEST).read_text().count("\n") == len(
        DEFAULT_REFERENCES
    )
    assert (home / "main.mk").exists() and not (home / ".git").exists()
    deps = [
        argv for argv in fake.calls if Path(argv[0]).name == "make" and "deps" in argv
    ]
    assert deps and f"LIBDIR={home}" in deps[0] and "NO_NODEJS=true" in deps[0]
    seed = [
        argv for argv in fake.calls if Path(argv[0]).name == "make" and "txt" in argv
    ]
    assert seed and "KRAMDOWN_OFFLINE=1" not in seed[0]


def test_provision_refuses_an_existing_record(tmp_path, template_repo):
    template, commit = template_repo
    root = tmp_path / "root"
    provision(root, template=template, template_commit=commit, runner=_fake_tools(root))
    with pytest.raises(ExperimentError) as excinfo:
        provision(
            root, template=template, template_commit=commit, runner=_fake_tools(root)
        )
    assert "exists" in str(excinfo.value)


def test_verify_passes_a_fresh_record_and_names_what_broke(tmp_path, template_repo):
    template, commit = template_repo
    root = tmp_path / "root"
    fake = _fake_tools(root)
    record = provision(root, template=template, template_commit=commit, runner=fake)
    ok, reasons = verify(record, runner=fake)
    assert ok and reasons == ()
    (root / TOOLS_DIR / ".refcache" / "reference.RFC.9000.xml").write_text("changed\n")
    ok, reasons = verify(record, runner=fake)
    assert not ok and any("refcache" in reason for reason in reasons)
