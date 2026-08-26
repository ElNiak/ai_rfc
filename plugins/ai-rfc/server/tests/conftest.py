import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
PANTHER_ROOT = Path(__file__).resolve().parents[10]

for entry in (str(SERVER_ROOT / "src"), str(PANTHER_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ai_rfc_server.testing import build_workspace  # noqa: E402


@pytest.fixture
def make_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A workspace factory plus an env switcher, for twin-workspace tests."""

    def build(name: str) -> Path:
        return build_workspace(tmp_path / name)

    def use(root: Path) -> None:
        monkeypatch.setenv("PANTHER_REPO", str(PANTHER_ROOT))
        monkeypatch.setenv("ARFC_WORKSPACE", str(root))

    return build, use


@pytest.fixture
def workspace(make_workspace):
    build, use = make_workspace
    root = build("ws")
    use(root)

    from ai_rfc_server.paths import resolve_context

    return resolve_context()
