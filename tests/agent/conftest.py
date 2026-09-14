"""Twin workspaces for the grouped agent verbs.

``tests/server/conftest.py`` holds the same factory, and a ``conftest`` reaches
only its own directory, so the pair is rebuilt here rather than imported across
suites. Both spell it over :func:`ai_rfc.server.testing.build_workspace`, which
is the single builder either way.
"""

from pathlib import Path

import pytest

from ai_rfc.server.testing import build_workspace


@pytest.fixture
def make_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A workspace factory plus an env switcher, for twin-workspace tests."""

    def build(name: str) -> Path:
        return build_workspace(tmp_path / name)

    def use(root: Path) -> None:
        monkeypatch.setenv("AI_RFC_CONFIG", str(root / "recon.yaml"))

    return build, use


@pytest.fixture
def workspace(make_workspace):
    """One resolved workspace, for the tests that drive a single arm."""
    build, use = make_workspace
    root = build("ws")
    use(root)

    from ai_rfc.server.paths import resolve_context

    return resolve_context()
