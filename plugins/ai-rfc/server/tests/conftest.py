import subprocess
import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
PANTHER_ROOT = Path(__file__).resolve().parents[10]

for entry in (str(SERVER_ROOT / "src"), str(PANTHER_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A complete fixture workspace built through the substrate's own code."""
    from panther.plugins.services.testers.a_rfc.history import cli as history_cli
    from panther.plugins.services.testers.a_rfc.timeline import cli as timeline_cli
    from panther.plugins.services.testers.a_rfc.views import cli as views_cli

    root = tmp_path / "workspace"
    root.mkdir()

    clone = root / "clone"
    clone.mkdir()
    _run(clone, "init", "-b", "main")
    _run(clone, "config", "user.email", "t@t")
    _run(clone, "config", "user.name", "t")
    (clone / "a.txt").write_text("one\n")
    _run(clone, "add", "a.txt")
    _run(clone, "commit", "-m", "root")
    _run(clone, "checkout", "-b", "feat")
    (clone / "b.txt").write_text("two\n")
    _run(clone, "add", "b.txt")
    _run(clone, "commit", "-m", "feat work")
    _run(clone, "checkout", "main")
    (clone / "c.txt").write_text("three\n")
    _run(clone, "add", "c.txt")
    _run(clone, "commit", "-m", "direct push")
    _run(clone, "merge", "--no-ff", "feat", "-m", "Merge branch 'feat'")
    head = _run(clone, "rev-parse", "HEAD")
    merge_sha = head

    assert history_cli.main([str(clone), "--out", str(root / "corpus")]) == 0
    assert (
        timeline_cli.main(
            [str(root / "corpus"), "--out", str(root / "timeline")]
        )
        == 0
    )
    assert (
        views_cli.main(
            [
                str(root / "timeline"),
                "--corpus",
                str(root / "corpus"),
                "--repo",
                str(clone),
                "--out",
                str(root / "clusters"),
            ]
        )
        == 0
    )

    (root / "manifest.yaml").write_text(
        "rfc: T-1\n"
        "title: 'Fixture reconstruction'\n"
        "requirements:\n"
        "  't:1.1':\n"
        "    text: 'Thing one.'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: core\n"
        "    anchors:\n"
        "      - evidence_class: code\n"
        "        locator: a.txt\n"
        f"        commit: '{head}'\n"
        "  't:2.1':\n"
        "    text: 'Thing two.'\n"
        "    section: '2.1'\n"
        "    level: SHOULD\n"
        "    layer: core\n"
        "    anchors:\n"
        "      - evidence_class: code\n"
        "        locator: b.txt\n"
        f"        commit: '{head}'\n"
        "      - evidence_class: adr\n"
        f"        locator: {merge_sha}\n"
    )
    (root / "questions.yaml").write_text("questions: {}\n")
    (root / "revisions.yaml").write_text("revisions: {}\n")
    (root / "interviews").mkdir()

    draft = root / "draft"
    draft.mkdir()
    _run(draft, "init", "-b", "main")
    _run(draft, "config", "user.email", "t@t")
    _run(draft, "config", "user.name", "t")
    (draft / "draft-test-spec.md").write_text(
        "# Spec\n\nThing one MUST hold. `a_rfc:t:1.1`\n"
    )
    _run(draft, "add", "draft-test-spec.md")
    _run(draft, "commit", "-m", "revision 00 content")

    monkeypatch.setenv("PANTHER_REPO", str(PANTHER_ROOT))
    monkeypatch.setenv("ARFC_WORKSPACE", str(root))

    from ai_rfc_server.paths import resolve_context

    return resolve_context()
