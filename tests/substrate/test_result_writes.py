"""Every verb that writes a result reports an unwritable ``--out``.

The defect this file guards is a family: a verb reads its inputs inside a
``try``, reports whatever the read raised, and then writes its result
*outside* that ``try``. An ``OSError`` from the write — a full disk, a
read-only mount, a ``--out`` whose parent is a file — therefore reaches the
operator as a stack trace rather than as the ``error:`` line every other
refusal in the same verb prints.

Each case runs at the real process boundary (``python -m``), because in
process the exception is the same object whether or not it is caught, and only
the boundary shows what an operator sees. The bench below builds each verb's
inputs by running the shipped commands that produce them — ``history`` makes
the corpus ``timeline`` reads, ``draft checkpoint`` makes the checkpoints
``gate`` and ``completeness`` read — so nothing here restates a fixture that
the package already knows how to build.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

MANIFEST = (
    "rfc: SPEC-1\n"
    "title: 'A reconstructed specification'\n"
    "requirements:\n"
    "  'spec:1.1':\n"
    "    text: 'The system does the thing.'\n"
    "    section: '1.1'\n"
    "    level: MUST\n"
    "    layer: behaviour\n"
    "    anchors:\n"
    "      - evidence_class: code\n"
    "        locator: first.txt\n"
    "        commit: '{commit}'\n"
    "        line: 1\n"
)

#: A one-line JaCoCo report naming the fixture repository's one covered line.
#: ``coverage`` writes its proposals whether or not any anchor matched, so the
#: report only has to parse; what it corroborates is another module's subject.
JACOCO = (
    '<report name="fixture">\n'
    '  <package name="">\n'
    '    <sourcefile name="first.txt">\n'
    '      <line nr="1" mi="0" ci="3" mb="0" cb="0"/>\n'
    "    </sourcefile>\n"
    "  </package>\n"
    "</report>\n"
)

QUESTIONS = "questions: {}\n"


def _git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` and return its stdout, stripped."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init(repo: Path) -> None:
    """Create an empty repository with an author, so commits are possible."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture Author")


def _module(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run one ``python -m`` invocation, capturing both streams."""
    return subprocess.run(
        [sys.executable, "-m", *argv], capture_output=True, text=True, **kwargs
    )


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Inputs every verb below needs, laid out as one workspace.

    Returns:
        A mapping of input name to path, plus ``commit`` (the source
        repository's HEAD) and ``blocker`` (a regular file, so that
        ``blocker/out`` is a ``--out`` whose parent cannot hold a directory).
    """
    root = tmp_path_factory.mktemp("bench")

    source = root / "source"
    _init(source)
    (source / "first.txt").write_text("first\n")
    _git(source, "add", "first.txt")
    _git(source, "commit", "-q", "-m", "first")
    (source / "second.txt").write_text("second\n")
    _git(source, "add", "second.txt")
    _git(source, "commit", "-q", "-m", "second")
    commit = _git(source, "rev-parse", "HEAD")

    manifest = root / "manifest.yaml"
    manifest.write_text(MANIFEST.format(commit=commit))
    (root / "jacoco.xml").write_text(JACOCO)
    (root / "questions.yaml").write_text(QUESTIONS)

    corpus, timeline = root / "corpus", root / "timeline"
    assert (
        _module(["ai_rfc.history", str(source), "--out", str(corpus)]).returncode == 0
    )
    assert (
        _module(["ai_rfc.timeline", str(corpus), "--out", str(timeline)]).returncode
        == 0
    )

    clusters = [
        json.loads(line)
        for line in (timeline / "clusters.jsonl").read_text().splitlines()
        if line
    ]
    cluster_id = clusters[0]["id"]
    checkpoints = root / "checkpoints"
    assert (
        _module(
            [
                "ai_rfc.draft",
                "checkpoint",
                str(manifest),
                "--timeline",
                str(timeline),
                "--cluster",
                cluster_id,
                "--out",
                str(checkpoints),
            ]
        ).returncode
        == 0
    )
    frozen = json.loads((checkpoints / cluster_id / "checkpoint.json").read_text())

    draft = root / "draft"
    _init(draft)
    (draft / "draft-test-spec.md").write_text(
        "# Spec\n\nThe system does the thing. `ai_rfc:spec:1.1`\n"
    )
    _git(draft, "add", "draft-test-spec.md")
    _git(draft, "commit", "-q", "-m", "revision 00")
    _git(draft, "tag", "draft-test-spec-00")

    (root / "revisions.yaml").write_text(
        "revisions:\n"
        "  draft-test-spec-00:\n"
        f"    cluster_id: {cluster_id}\n"
        f"    checkpoint_manifest_sha256: {frozen['manifest_sha256']}\n"
        "    normative_change: true\n"
        "    note: 'initial reconstruction'\n"
    )

    blocker = root / "blocker"
    blocker.write_text("a regular file, so nothing can be created beneath it\n")

    return {
        "root": root,
        "source": source,
        "commit": commit,
        "manifest": manifest,
        "jacoco": root / "jacoco.xml",
        "questions": root / "questions.yaml",
        "revisions": root / "revisions.yaml",
        "corpus": corpus,
        "timeline": timeline,
        "checkpoints": checkpoints,
        "draft": draft,
        "blocker": blocker,
    }


def _argv(verb: str, bench: dict[str, Any], out: Path) -> list[str]:
    """The ``python -m`` argument vector for ``verb``, writing into ``out``."""
    if verb == "check":
        return ["ai_rfc.check", str(bench["manifest"]), "--out", str(out)]
    if verb == "coverage":
        return [
            "ai_rfc.coverage",
            str(bench["manifest"]),
            "--coverage",
            str(bench["jacoco"]),
            "--repo",
            str(bench["source"]),
            "--commit",
            bench["commit"],
            "--out",
            str(out),
        ]
    if verb == "draft render":
        return ["ai_rfc.draft", "render", str(bench["manifest"]), "--out", str(out)]
    if verb == "draft completeness":
        return [
            "ai_rfc.draft",
            "completeness",
            str(bench["root"]),
            "--out",
            str(out),
        ]
    if verb == "draft lint":
        return [
            "ai_rfc.draft",
            "lint",
            str(bench["draft"]),
            "--worktree",
            "--out",
            str(out),
        ]
    if verb == "draft gate":
        return [
            "ai_rfc.draft",
            "gate",
            str(bench["draft"]),
            "--timeline",
            str(bench["timeline"]),
            "--checkpoints",
            str(bench["checkpoints"]),
            "--questions",
            str(bench["questions"]),
            "--revisions",
            str(bench["revisions"]),
            "--out",
            str(out),
        ]
    if verb == "history":
        return ["ai_rfc.history", str(bench["source"]), "--out", str(out)]
    if verb == "timeline":
        return ["ai_rfc.timeline", str(bench["corpus"]), "--out", str(out)]
    raise AssertionError(f"unhandled verb {verb!r}")


#: Every verb of row #22, with the artifact that proves its write ran. The
#: plan counted seven; ``draft`` holds four write sites, not three, so the
#: table below is eight rows. Dropping one to match the count would leave a
#: listed site with no test.
VERBS = (
    ("check", "report.json"),
    ("coverage", "runtime-anchors.json"),
    ("draft render", "structures.md"),
    ("draft completeness", "completeness.json"),
    ("draft lint", "lint-report.json"),
    ("draft gate", "gate-report.json"),
    ("history", "commits.jsonl"),
    ("timeline", "timeline.json"),
)


@pytest.mark.parametrize("verb, artifact", VERBS, ids=[v for v, _ in VERBS])
def test_the_bench_reaches_each_verbs_write(
    verb: str, artifact: str, bench: dict[str, Any], tmp_path: Path
):
    """The inputs are good enough to get past the reads and write something.

    Without this, the refusal test below is unfalsifiable: a verb that exits 1
    on a malformed input prints an ``error:`` line and no traceback, which is
    exactly what the refusal test asserts. This case pins that each verb got
    as far as its result write when nothing was in the way.
    """
    out = tmp_path / "out"
    result = _module(_argv(verb, bench, out))
    assert result.returncode == 0, result.stderr
    assert (out / artifact).exists()


@pytest.mark.parametrize("verb, artifact", VERBS, ids=[v for v, _ in VERBS])
def test_an_unwritable_out_is_reported_rather_than_raised(
    verb: str, artifact: str, bench: dict[str, Any]
):
    """A ``--out`` under a regular file is a refusal, not a stack trace.

    ``blocker`` is a file, so creating ``blocker/out`` raises
    ``NotADirectoryError`` — an ``OSError``, the same family every one of
    these verbs already catches around its reads.
    """
    result = _module(_argv(verb, bench, bench["blocker"] / "out"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert any(
        line.startswith("error:") for line in result.stderr.splitlines()
    ), result.stderr
