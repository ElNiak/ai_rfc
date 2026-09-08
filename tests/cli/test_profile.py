import importlib
from pathlib import Path

import pytest

from ai_rfc.config import experiments_root, profile_dir
from ai_rfc.experiment import cli
from ai_rfc.lifecycle.profile import init_profile, login_command, profile_env


def test_experiments_root_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "exp"))
    assert experiments_root() == tmp_path / "exp"
    monkeypatch.delenv("AI_RFC_EXPERIMENTS_ROOT")
    assert experiments_root() == Path("~/ai-rfc-experiments").expanduser()


def test_the_instrument_has_no_paths_forwarder_onto_the_config():
    """The harness imports production directly, not through a module of aliases.

    ``experiment/paths.py`` held nothing but ``experiments_root`` under a
    second name and ``profile_dir``. A forwarder is what lets an import creep
    back the wrong way round, so its absence is the thing worth asserting.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ai_rfc.experiment.paths")


def test_init_profile_creates_dir_and_names_login(tmp_path):
    profile = init_profile(tmp_path)
    assert profile == profile_dir(tmp_path) and profile.is_dir()
    assert (profile / "README-ai_rfc.txt").exists()
    assert init_profile(tmp_path) == profile
    assert login_command(tmp_path) == (f"CLAUDE_CONFIG_DIR={profile} claude auth login")


def test_cli_profile_init_prints_login_command(tmp_path, capsys):
    assert cli.main(["profile", "init", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "claude auth login" in out and str(tmp_path / "profile") in out


def test_profile_env_carries_the_five_variables_a_login_needs(tmp_path, monkeypatch):
    """USER is load-bearing: without it the CLI answers "Not logged in"."""
    monkeypatch.setenv("HOME", "/home/t")
    monkeypatch.setenv("USER", "t")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    env = profile_env(tmp_path / "profile")

    assert env == {
        "HOME": "/home/t",
        "USER": "t",
        "PATH": "/usr/bin",
        "LANG": "en_US.UTF-8",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "profile"),
    }


def test_profile_env_inherits_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("SSLKEYLOGFILE", "/tmp/keys")

    env = profile_env(tmp_path / "profile")

    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDECODE" not in env
    assert "SSLKEYLOGFILE" not in env


def test_profile_env_defaults_lang_when_the_shell_has_none(tmp_path, monkeypatch):
    monkeypatch.delenv("LANG", raising=False)

    assert profile_env(tmp_path / "profile")["LANG"] == "C.UTF-8"
