"""`ai-rfc doctor`: each question asked once, each failure named with its fix."""

import json
import stat
from pathlib import Path

import pytest

from ai_rfc import cli, toolchain
from ai_rfc.lifecycle.doctor import cli as doctor  # noqa: F401 - the verb's cli, per D1


def _fake_claude(tmp_path: Path) -> Path:
    binary = tmp_path / "bin" / "claude"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\necho '2.1.258 (Claude Code)'\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def _config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "recon.yaml"
    path.write_text(
        "name: fixture\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "source:\n  repo: https://github.com/example/project\n  pin: main\n"
        "draft:\n  name: draft-test-fixture\n" + extra
    )
    return path


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    """`doctor` falls back to $AI_RFC_CONFIG, which a developer may have set."""
    monkeypatch.delenv("AI_RFC_CONFIG", raising=False)


def test_doctor_reports_a_missing_claude_binary_as_an_error(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(["doctor", "--config", str(_config(tmp_path))]) == 1
    out = capsys.readouterr().out
    assert "claude: error" in out and "not found on PATH" in out


def test_doctor_passes_with_a_binary_a_profile_and_a_verified_toolchain(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "root"
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(root))
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path).parent))
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    record = root / "tools" / "toolchain.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")
    (root / "profile").mkdir()
    monkeypatch.setattr(toolchain, "verify", lambda record, runner=None: (True, ()))
    assert cli.main(["doctor", "--config", str(_config(tmp_path)), "--json"]) == 0
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["claude"]["ok"] and "2.1.258" in checks["claude"]["detail"]
    assert checks["profile"]["ok"] and "is present" in checks["profile"]["detail"]
    assert checks["toolchain"]["ok"]
    assert checks["token"]["ok"] and checks["deps"]["ok"]


def test_doctor_reports_a_missing_profile_without_creating_it(
    tmp_path, monkeypatch, capsys
):
    """A diagnostic answers questions; it does not quietly fix what it found.

    Creating the directory made the check unfalsifiable — it could never
    report a missing profile, because asking created one — and made every
    ``doctor`` run a filesystem write.
    """
    root = tmp_path / "root"
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(root))
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path).parent))
    assert cli.main(["doctor", "--config", str(_config(tmp_path)), "--json"]) == 0
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert not checks["profile"]["ok"]
    assert checks["profile"]["severity"] == "warning"
    assert str(root / "profile") in checks["profile"]["detail"]
    assert "claude auth login" in checks["profile"]["fix"]
    assert not (root / "profile").exists()


def test_doctor_reports_a_check_that_itself_failed_rather_than_raising(
    tmp_path, monkeypatch, capsys
):
    """The one command run when the environment is broken must not traceback.

    ``toolchain.verify`` reads the refcache and builds in a temporary
    directory, so an unreadable or full filesystem raises ``OSError`` out of
    it. Unguarded, that reaches the operator as a stack trace.
    """
    root = tmp_path / "root"
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(root))
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path).parent))
    record = root / "tools" / "toolchain.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")

    def _explode(record, runner=None):
        raise OSError("Read-only file system")

    monkeypatch.setattr(toolchain, "verify", _explode)
    assert cli.main(["doctor", "--json"]) == 1
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["toolchain"]["severity"] == "error"
    assert "Read-only file system" in checks["toolchain"]["detail"]


def test_doctor_warns_about_a_claude_md_ancestor_and_a_missing_token(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "root"
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(root))
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path).parent))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "CLAUDE.md").write_text("project notes\n")
    assert cli.main(["doctor", "--config", str(_config(tmp_path)), "--json"]) == 0
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert (
        checks["workspace"]["severity"] == "warning"
        and "CLAUDE.md" in checks["workspace"]["detail"]
    )
    assert (
        checks["token"]["severity"] == "warning"
        and "GITHUB_TOKEN" in checks["token"]["detail"]
    )
    assert (
        checks["toolchain"]["severity"] == "warning"
        and "toolchain provision" in checks["toolchain"]["fix"]
    )


def test_doctor_fails_when_a_present_toolchain_record_does_not_verify(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "root"
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(root))
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path).parent))
    record = root / "tools" / "toolchain.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")
    monkeypatch.setattr(
        toolchain,
        "verify",
        lambda record, runner=None: (
            False,
            ("refcache contents differ from the recorded digest",),
        ),
    )
    assert cli.main(["doctor"]) == 1
    assert "refcache contents differ" in capsys.readouterr().out


def test_toolchain_verbs_are_mounted_on_the_root(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["toolchain", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "provision" in out and "verify" in out
