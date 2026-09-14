"""The build and lint cores over the substrate verbs.

The seam is the substrate API, not a subprocess: ``build`` and ``lint`` are
faked where the core calls them. What the old fakes pinned as an argument
vector is pinned here as the call the core actually makes, and the stderr they
pinned is preserved wherever the core still synthesises the same line.
"""

import json
from types import SimpleNamespace

import pytest

from ai_rfc.draft.build import BUILD_DIR, REPORT_FILE, BuildError
from ai_rfc.draft.lint import REPORT_FILE as LINT_REPORT
from ai_rfc.server import tools
from ai_rfc.server.core import build as build_core
from ai_rfc.server.core.build import CoreError, draft_build, draft_lint
from ai_rfc.server.paths import resolve_context


def _fake_build(report: dict, *, writes_report: bool = True):
    """Stand in for ``ai_rfc.draft.build.build``, recording its keyword call.

    Args:
        report: The record to write where the real build writes it.
        writes_report: Whether to write it at all.

    Returns:
        The fake and the list its calls are appended to.
    """
    calls = []

    def build(draft_repo, **kwargs):
        calls.append((draft_repo, kwargs))
        if writes_report:
            target = kwargs["out"] / BUILD_DIR
            target.mkdir(parents=True, exist_ok=True)
            (target / REPORT_FILE).write_text(json.dumps(report))
        return SimpleNamespace(
            findings=tuple(report.get("findings", ())),
            commit=report.get("commit", "c" * 40),
            exit_code=report.get("exit_code", 0),
        )

    return build, calls


def _toolchain(monkeypatch, tmp_path):
    """Configure a toolchain and skip the record's own validation."""
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
    resolved = object()
    monkeypatch.setattr(build_core, "resolve_toolchain", lambda explicit: resolved)
    return resolved


def test_draft_build_needs_a_toolchain(workspace, monkeypatch):
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    with pytest.raises(CoreError) as excinfo:
        draft_build(resolve_context())
    assert "AI_RFC_TOOLCHAIN" in str(excinfo.value)


def test_draft_build_runs_the_verb_and_reads_the_report(
    workspace, monkeypatch, tmp_path
):
    resolved = _toolchain(monkeypatch, tmp_path)
    refcache = workspace.workspace / "refcache"
    refcache.mkdir()
    fake, calls = _fake_build(
        {
            "commit": "c" * 40,
            "exit_code": 0,
            "findings": [],
            "outputs": {"draft-x.txt": {}},
        }
    )
    monkeypatch.setattr(build_core, "build", fake)
    result = draft_build(resolve_context(), ref="draft-test-spec-00")
    draft_repo, kwargs = calls[0]
    assert draft_repo == workspace.workspace / "draft"
    assert kwargs["ref"] == "draft-test-spec-00"
    assert kwargs["toolchain"] is resolved and kwargs["refcache"] == refcache
    assert result == {
        "exit_code": 0,
        "stderr": [
            f"note: build of {'c' * 12} exited 0; "
            f"report at {workspace.workspace / 'out' / BUILD_DIR / REPORT_FILE}"
        ],
        "findings": [],
        "commit": "c" * 40,
        "outputs": {"draft-x.txt": {}},
    }


def test_draft_lint_measures_the_worktree_by_default(workspace):
    result = draft_lint(resolve_context())
    assert result["exit_code"] == 0
    assert set(result["metrics"]) == {
        "sections",
        "abstract",
        "references",
        "keywords",
        "blocks",
        "citations",
        "narration",
        "extra",
    }
    assert isinstance(result["findings"], list)
    assert tools.ai_rfc_draft_lint()["metrics"] == result["metrics"]


def test_draft_build_does_not_read_a_stale_report_after_a_failed_run(
    workspace, monkeypatch, tmp_path
):
    _toolchain(monkeypatch, tmp_path)
    stale = workspace.workspace / "out" / BUILD_DIR
    stale.mkdir(parents=True)
    (stale / REPORT_FILE).write_text(
        json.dumps(
            {
                "commit": "old",
                "exit_code": 0,
                "findings": [],
                "outputs": {"draft-old.txt": {}},
            }
        )
    )

    def refuse(draft_repo, **kwargs):
        raise BuildError("nope: not a commit")

    monkeypatch.setattr(build_core, "build", refuse)
    result = draft_build(resolve_context(), ref="nope")
    assert result == {
        "exit_code": 1,
        "stderr": ["error: nope: not a commit"],
        "findings": ["error: nope: not a commit"],
        "commit": None,
        "outputs": {},
    }


def test_draft_lint_does_not_read_a_stale_report_after_a_failed_run(
    workspace, monkeypatch
):
    stale = workspace.workspace / "out"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / LINT_REPORT).write_text(
        json.dumps(
            {
                "source": {},
                "sections": {},
                "abstract": {},
                "references": {},
                "keywords": {},
                "blocks": {},
                "citations": {},
                "narration": [],
                "findings": [],
            }
        )
    )

    def refuse(text, **kwargs):
        raise ValueError("nope: not a commit")

    monkeypatch.setattr(build_core, "lint", refuse)
    result = draft_lint(resolve_context())
    assert result["exit_code"] == 1
    assert result["findings"] == result["stderr"] == ["error: nope: not a commit"]
    assert result["metrics"] == {}


def test_draft_lint_reads_the_worktree_or_a_ref(workspace, monkeypatch):
    """What ``--worktree`` used to decide in an argv, it now decides in a call.

    ``draft_text`` is the ref reader; the worktree branch must never reach it,
    and the ref branch must ask it for ``HEAD``. Both branches hand ``lint``
    the workspace manifest, which is what ``--manifest`` carried.
    """
    read = []
    linted = []

    def draft_text(draft_repo, ref):
        read.append((draft_repo, ref))
        return "sha", "# Spec\n"

    def lint(text, **kwargs):
        linted.append(kwargs)
        raise ValueError("stop here")

    monkeypatch.setattr(build_core, "draft_text", draft_text)
    monkeypatch.setattr(build_core, "lint", lint)

    draft_lint(resolve_context())
    assert read == []
    assert linted[0]["source"] == {
        "path": str(workspace.workspace / "draft"),
        "ref": "worktree",
    }

    draft_lint(resolve_context(), worktree=False)
    assert read == [(workspace.workspace / "draft", "HEAD")]
    assert linted[1]["source"]["ref"] == "HEAD"
    assert all(call["manifest"] is not None for call in linted)
