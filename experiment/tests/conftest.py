"""Path setup shared by the experiment tests.

The experiment package imports the server core and the PANTHER substrate
as libraries; the paths are derived from this file's location so the
tests run from any cwd without an installed package.
"""

import sys
from pathlib import Path

import pytest

AI_RFC_ROOT = Path(__file__).resolve().parents[2]
SERVER_SRC = AI_RFC_ROOT / "plugins" / "ai-rfc" / "server" / "src"
PANTHER_ROOT = AI_RFC_ROOT.parents[5]

for entry in (str(AI_RFC_ROOT), str(SERVER_SRC), str(PANTHER_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


@pytest.fixture
def panther_repo() -> Path:
    assert (PANTHER_ROOT / "panther" / "plugins").is_dir(), PANTHER_ROOT
    return PANTHER_ROOT


@pytest.fixture
def plugin_root() -> Path:
    return AI_RFC_ROOT / "plugins" / "ai-rfc"


@pytest.fixture
def fixture_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One complete fixture workspace with the env contract pointing at it."""
    from ai_rfc_server.testing import build_workspace

    root = build_workspace(tmp_path / "ws")
    monkeypatch.setenv("PANTHER_REPO", str(PANTHER_ROOT))
    monkeypatch.setenv("ARFC_WORKSPACE", str(root))
    return root
