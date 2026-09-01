"""Path setup shared by the experiment tests.

The experiment package imports the server core and the PANTHER substrate
as libraries; the paths are derived from this file's location so the
tests run from any cwd without an installed package.
"""

import json
import sys
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[2]
SERVER_SRC = HARNESS_ROOT / "plugins" / "ai-rfc" / "server" / "src"
PANTHER_ROOT = HARNESS_ROOT.parents[5]
FAKE_CLAUDE = Path(__file__).parent / "fake_claude" / "claude"

for entry in (str(HARNESS_ROOT), str(SERVER_SRC), str(PANTHER_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


@pytest.fixture
def panther_repo() -> Path:
    assert (PANTHER_ROOT / "panther" / "plugins").is_dir(), PANTHER_ROOT
    return PANTHER_ROOT


@pytest.fixture
def plugin_root() -> Path:
    return HARNESS_ROOT / "plugins" / "ai-rfc"


@pytest.fixture
def fixture_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One complete fixture workspace with the env contract pointing at it."""
    from ai_rfc_server.testing import build_workspace

    root = build_workspace(tmp_path / "ws")
    monkeypatch.setenv("PANTHER_REPO", str(PANTHER_ROOT))
    monkeypatch.setenv("AI_RFC_WORKSPACE", str(root))
    return root


@pytest.fixture
def template_repo(tmp_path: Path) -> tuple[str, str]:
    """A local stand-in for auto-i-d-template, carrying agent files to strip."""
    from ai_rfc_server.testing import git

    repo = tmp_path / "template"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("draft-*\n*.swp\n")
    (repo / "Makefile").write_text("all:\n\t@echo build\n")
    (repo / "CLAUDE.md").write_text("template agent notes\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "template", date="2026-01-01T00:00:09+00:00")
    return str(repo), git(repo, "rev-parse", "HEAD")


def fixture_target(source: Path, window: tuple[int, int] = (2, 2)):
    """The two-cluster fixture target every workspace test builds from.

    The default window holds one cluster, which is all most tests need. A test
    that has to observe several sessions widens it to both.
    """
    from experiment.workspace import Target

    return Target(
        name="fixture",
        source=source,
        forge_snapshot=None,
        window=window,
        draft_name="draft-test-fixture",
        rfc_id="FIX-1",
        title="Fixture",
        abbrev="Fix",
    )


@pytest.fixture
def pristine(fixture_workspace, panther_repo, template_repo, tmp_path) -> Path:
    """A prepared pristine workspace of the fixture target (window 2-2)."""
    from experiment.workspace import prepare

    template, commit = template_repo
    return prepare(
        fixture_target(fixture_workspace),
        root=tmp_path / "root",
        panther_repo=panther_repo,
        template=template,
        template_commit=commit,
    )


@pytest.fixture
def write_scenario():
    """Write one fake-claude scenario into an isolated profile directory."""

    def write(profile_dir: Path, run_id: str, payload: dict) -> Path:
        scenarios = profile_dir / "fake-scenarios"
        scenarios.mkdir(parents=True, exist_ok=True)
        path = scenarios / f"{run_id}.json"
        path.write_text(json.dumps(payload, indent=2))
        return path

    return write


COMPLETE_STEPS = [
    {"kind": "claim", "id": "t:3.1", "section": "3.1"},
    {"kind": "record_status"},
    {"kind": "checkpoint", "ordinal": 2},
    {"kind": "prose", "line": "Thing three MAY hold. `ai_rfc:t:3.1`"},
    {
        "kind": "revision",
        "ordinal": 2,
        "tag": "draft-test-fixture-00",
        "normative": True,
    },
    {"kind": "tag", "tag": "draft-test-fixture-00"},
]


@pytest.fixture
def campaign(pristine, panther_repo, plugin_root, tmp_path):
    """A frozen three-arm campaign whose launches go through the fake claude."""
    from experiment.config import CampaignConfig, init_campaign

    return init_campaign(
        CampaignConfig(
            root=tmp_path / "root",
            campaign_id="test",
            pristine_dir=pristine,
            arms=("A", "B", "C"),
            repeats=1,
            seed=7,
            model="fake-model",
            effort="high",
            budget_usd=1.0,
            # Generous on purpose: the fake finishes in about a second, but a
            # loaded machine can starve it well past a tight cap and the failure
            # then looks like a harness defect. The timeout path has its own
            # test, which sets timeout_s=1 explicitly.
            timeout_s=900,
            panther_repo=panther_repo,
            plugin_root=plugin_root,
            python=sys.executable,
            claude_bin=str(FAKE_CLAUDE),
            parity={"passed": True, "summary": "test"},
        )
    )
