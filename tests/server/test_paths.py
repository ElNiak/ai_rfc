"""The environment contract is one variable: the workspace."""

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
