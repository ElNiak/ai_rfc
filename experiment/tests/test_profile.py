from pathlib import Path

from experiment import cli
from experiment.paths import default_root, profile_dir
from experiment.profile import init_profile, login_command


def test_default_root_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ARFC_EXPERIMENTS_ROOT", str(tmp_path / "exp"))
    assert default_root() == tmp_path / "exp"
    monkeypatch.delenv("ARFC_EXPERIMENTS_ROOT")
    assert default_root() == Path("~/arfc-experiments").expanduser()


def test_init_profile_creates_dir_and_names_login(tmp_path):
    profile = init_profile(tmp_path)
    assert profile == profile_dir(tmp_path) and profile.is_dir()
    assert (profile / "README-arfc.txt").exists()
    assert init_profile(tmp_path) == profile
    assert login_command(tmp_path) == (f"CLAUDE_CONFIG_DIR={profile} claude auth login")


def test_cli_profile_init_prints_login_command(tmp_path, capsys):
    assert cli.main(["profile", "init", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "claude auth login" in out and str(tmp_path / "profile") in out
