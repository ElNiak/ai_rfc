from pathlib import Path

import pytest

from ai_rfc.server.testing import build_workspace


@pytest.fixture
def make_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A workspace factory plus an env switcher, for twin-workspace tests."""

    def build(name: str) -> Path:
        return build_workspace(tmp_path / name)

    def use(root: Path) -> None:
        monkeypatch.setenv("AI_RFC_WORKSPACE", str(root))

    return build, use


@pytest.fixture
def workspace(make_workspace):
    build, use = make_workspace
    root = build("ws")
    use(root)

    from ai_rfc.server.paths import resolve_context

    return resolve_context()
