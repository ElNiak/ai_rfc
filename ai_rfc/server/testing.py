"""Fixture workspaces built through the substrate's own code.

Commit dates are pinned so two builds produce byte-identical corpora; the
parity tests compare twin workspaces byte-for-byte, and the experiment
harness reuses the same builder for its own tests. Importable without the
``mcp`` package.
"""

import os
import subprocess
from pathlib import Path


def git(repo: Path, *args: str, date: str | None = None) -> str:
    """Run a checked git command against a repository.

    Args:
        repo: Working tree to run the command in.
        *args: Git subcommand and its arguments.
        date: ISO-8601 timestamp to pin as both the author and committer
            date, or ``None`` to let git use the current time.

    Returns:
        The command's stdout, stripped of surrounding whitespace.

    Raises:
        subprocess.CalledProcessError: If git exits with a non-zero status.
    """
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def build_workspace(root: Path) -> Path:
    """Build one complete workspace through the substrate's own code.

    Commit dates are pinned so two builds produce byte-identical corpora —
    the parity tests compare twin workspaces byte-for-byte.

    Args:
        root: Directory to create and populate; must not already exist.

    Returns:
        The ``root`` path, once the workspace is fully built.
    """
    from ai_rfc.history import cli as history_cli
    from ai_rfc.timeline import cli as timeline_cli
    from ai_rfc.views import cli as views_cli

    root.mkdir()
    clone = root / "clone"
    clone.mkdir()
    git(clone, "init", "-b", "main")
    git(clone, "config", "user.email", "t@t")
    git(clone, "config", "user.name", "t")
    (clone / "a.txt").write_text("one\n")
    git(clone, "add", "a.txt")
    git(clone, "commit", "-m", "root", date="2026-01-01T00:00:01+00:00")
    git(clone, "checkout", "-b", "feat")
    (clone / "b.txt").write_text("two\n")
    git(clone, "add", "b.txt")
    git(clone, "commit", "-m", "feat work", date="2026-01-01T00:00:02+00:00")
    git(clone, "checkout", "main")
    (clone / "c.txt").write_text("three\n")
    git(clone, "add", "c.txt")
    git(clone, "commit", "-m", "direct push", date="2026-01-01T00:00:03+00:00")
    git(
        clone,
        "merge",
        "--no-ff",
        "feat",
        "-m",
        "Merge branch 'feat'",
        date="2026-01-01T00:00:04+00:00",
    )
    head = git(clone, "rev-parse", "HEAD")

    assert history_cli.main([str(clone), "--out", str(root / "corpus")]) == 0
    assert (
        timeline_cli.main([str(root / "corpus"), "--out", str(root / "timeline")]) == 0
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
        f"        locator: {head}\n"
    )
    (root / "questions.yaml").write_text("questions: {}\n")
    (root / "revisions.yaml").write_text("revisions: {}\n")
    (root / "interviews").mkdir()

    draft = root / "draft"
    draft.mkdir()
    git(draft, "init", "-b", "main")
    git(draft, "config", "user.email", "t@t")
    git(draft, "config", "user.name", "t")
    (draft / "draft-test-spec.md").write_text(
        "# Spec\n\nThing one MUST hold. `ai_rfc:t:1.1`\n"
    )
    git(draft, "add", "draft-test-spec.md")
    git(
        draft,
        "commit",
        "-m",
        "revision 00 content",
        date="2026-01-01T00:00:05+00:00",
    )
    return root
