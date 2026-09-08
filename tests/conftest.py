import json
import stat
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Rewrite the structure goldens from the current renderer.",
    )


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def template_repo(tmp_path: Path) -> tuple[str, str]:
    """A local stand-in for auto-i-d-template: library root, template/, example/."""
    from ai_rfc.server.testing import git

    repo = tmp_path / "template"
    (repo / "template").mkdir(parents=True)
    (repo / "example").mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "main.mk").write_text("txt::\n\t@echo build\n")
    (repo / "CLAUDE.md").write_text("template agent notes\n")
    (repo / "template" / "Makefile").write_text(
        "LIBDIR := lib\ninclude $(LIBDIR)/main.mk\n"
    )
    (repo / "template" / ".gitignore").write_text("*.txt\n*.html\n*.xml\n/versioned\n")
    (repo / "template" / ".editorconfig").write_text("root = true\n")
    (repo / "example" / "draft-todo-yourname-protocol.md").write_text(
        "---\ntitle: TODO\ndocname: draft-todo-yourname-protocol-latest\n---\n\n"
        "--- abstract\n\nTODO\n\n--- middle\n\n# Introduction\n\nTODO\n\n--- back\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "template", date="2026-01-01T00:00:09+00:00")
    return str(repo), git(repo, "rev-parse", "HEAD")


@pytest.fixture
def toolchain_record(tmp_path: Path) -> Path:
    """A toolchain record whose executables exist and do nothing.

    Mirrors ``tests/substrate/draft/test_build.py``'s fixture of the same
    shape: real (no-op) executables so :func:`ai_rfc.draft.build.probe_toolchain`
    would pass it for real, even though ``verify`` is monkeypatched in
    ``tests/experiment`` for every campaign fixture that consumes this record.
    """
    home = tmp_path / "tools" / "i-d-template"
    (home / "main.mk").parent.mkdir(parents=True)
    (home / "main.mk").write_text("txt:\n")
    refcache = tmp_path / "tools" / ".refcache"
    refcache.mkdir()
    for number in ("2119", "8174", "9000"):
        (refcache / f"reference.RFC.{number}.xml").write_text(
            f"<reference anchor='RFC{number}'/>\n"
        )
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
def source_repo(tmp_path: Path) -> Path:
    """A tiny local repository standing in for the implementation."""
    from ai_rfc.server.testing import git

    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("print(1)\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "first", date="2026-01-01T00:00:09+00:00")
    (repo / "a.py").write_text("print(2)\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "second", date="2026-01-02T00:00:09+00:00")
    return repo


@pytest.fixture
def initialised(
    tmp_path: Path, source_repo: Path, template_repo: tuple[str, str]
) -> tuple[Path, Path]:
    """A config file and the workspace ``ai-rfc init`` built from it.

    Host ``none`` and no references, so nothing here reaches the network or
    needs a toolchain record.

    Returns:
        The config file's path and the workspace root it names.
    """
    from ai_rfc import cli

    template, commit = template_repo
    config_path = tmp_path / "recon.yaml"
    config_path.write_text(
        "name: fixture\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "source:\n"
        f"  repo: {source_repo}\n"
        "  host: none\n"
        "  pin: main\n"
        "draft:\n"
        "  name: draft-test-fixture\n"
    )
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 0
    )
    return config_path, tmp_path / "ws"
