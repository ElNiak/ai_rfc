"""Provisioning writes a record a build can trust; verify re-checks it offline."""

import json
import stat
import subprocess
from pathlib import Path

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.toolchain import (
    DEFAULT_REFERENCES,
    EXAMPLE_DRAFT,
    RECORD_FILE,
    REFCACHE_DIGEST,
    TOOLS_DIR,
    _version,
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


def test_verify_names_the_specific_refcache_entry_that_changed(tmp_path, template_repo):
    template, commit = template_repo
    root = tmp_path / "root"
    fake = _fake_tools(root)
    record = provision(root, template=template, template_commit=commit, runner=fake)
    (root / TOOLS_DIR / ".refcache" / "reference.RFC.9000.xml").write_text("changed\n")
    ok, reasons = verify(record, runner=fake)
    assert not ok
    assert any("reference.RFC.9000.xml" in reason for reason in reasons)


def test_verify_reports_a_reason_instead_of_crashing_when_the_example_is_missing(
    tmp_path, template_repo
):
    """`verify` wraps staging and building the example, not just the record load.

    Only ``load_toolchain`` was wrapped before; the copyfile/`_git`/`build`
    calls that stage and build the template's own example ran bare, so an
    incomplete ``template_home`` reached the caller as a traceback instead of
    a reason.
    """
    template, commit = template_repo
    root = tmp_path / "root"
    fake = _fake_tools(root)
    record = provision(root, template=template, template_commit=commit, runner=fake)
    home = root / TOOLS_DIR / "i-d-template"
    (home / "example" / EXAMPLE_DRAFT).unlink()
    ok, reasons = verify(record, runner=fake)
    assert not ok
    assert any("example" in reason for reason in reasons)


def test_provision_raises_and_leaves_no_record_when_its_own_verify_fails(
    tmp_path, template_repo, monkeypatch
):
    """A failed self-check must not poison a retry with a half-provisioned record.

    ``provision`` writes ``toolchain.json`` and then verifies it; if that
    verify fails, the record must not remain on disk, or a retry hits
    "exists; a toolchain is provisioned once" instead of actually retrying.
    """
    from ai_rfc.experiment import toolchain as toolchain_module

    template, commit = template_repo
    root = tmp_path / "root"
    fake = _fake_tools(root)
    monkeypatch.setattr(
        toolchain_module, "verify", lambda record, runner=None: (False, ("nope",))
    )
    with pytest.raises(ExperimentError) as excinfo:
        provision(root, template=template, template_commit=commit, runner=fake)
    assert "provisioned, but verify failed" in str(excinfo.value)
    assert not (root / TOOLS_DIR / RECORD_FILE).exists()


def test_version_takes_the_first_line_and_is_empty_on_a_nonzero_exit():
    """make's banner puts the version first; the last line is the build target."""

    def banner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "GNU Make 3.81\n"
                "Copyright (C) 2006  Free Software Foundation, Inc.\n"
                "This program built for i386-apple-darwin11.3.0\n"
            ),
            stderr="",
        )

    assert _version(banner, "make", "--version") == "GNU Make 3.81"

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not found")

    assert _version(failing, "make", "--version") == ""


def test_cli_toolchain_verify_reports_a_reason_naming_the_record_and_exits_one(
    tmp_path, capsys
):
    from ai_rfc.experiment.cli import main

    code = main(["toolchain", "verify", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert str(tmp_path / TOOLS_DIR / RECORD_FILE) in out


def test_cli_toolchain_help_is_wired_for_both_verbs(capsys):
    from ai_rfc.experiment.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["toolchain", "provision", "--help"])
    assert excinfo.value.code == 0
    assert "--template" in capsys.readouterr().out

    with pytest.raises(SystemExit) as excinfo:
        main(["toolchain", "verify", "--help"])
    assert excinfo.value.code == 0
    assert "--root" in capsys.readouterr().out
