"""The environment contract: a required workspace, and an optional toolchain."""

import pytest

from ai_rfc.server.paths import EnvError, resolve_context


def test_the_contract_needs_only_the_workspace(tmp_path, monkeypatch):
    """The substrate is an installed package, so no checkout is located."""
    monkeypatch.delenv("PANTHER_REPO", raising=False)
    monkeypatch.setenv("AI_RFC_WORKSPACE", str(tmp_path))
    assert resolve_context().workspace == tmp_path.resolve()


def test_a_missing_workspace_is_refused(monkeypatch):
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    with pytest.raises(EnvError):
        resolve_context()


def test_a_workspace_that_is_not_a_directory_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RFC_WORKSPACE", str(tmp_path / "absent"))
    with pytest.raises(EnvError):
        resolve_context()


def test_the_toolchain_handle_is_optional_but_must_be_a_file(monkeypatch, tmp_path):
    from ai_rfc.server.paths import EnvError, resolve_context

    monkeypatch.setenv("AI_RFC_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    assert resolve_context().toolchain is None
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
    assert resolve_context().toolchain == record
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(tmp_path / "missing.json"))
    with pytest.raises(EnvError):
        resolve_context()
