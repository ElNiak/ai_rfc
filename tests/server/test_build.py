"""The build and lint cores over the substrate verbs."""

import json

import pytest

from ai_rfc.draft.build import BUILD_DIR, REPORT_FILE
from ai_rfc.draft.lint import REPORT_FILE as LINT_REPORT
from ai_rfc.server import tools
from ai_rfc.server.core import build as build_core
from ai_rfc.server.core.build import CoreError, draft_build, draft_lint
from ai_rfc.server.paths import resolve_context


def _fake_run(
    report: dict,
    code: int = 0,
    *,
    writes_report: bool = True,
    stderr: list[str] | None = None,
):
    calls = []
    lines = stderr if stderr is not None else ["note: fake"]

    def run(ctx, module, *args):
        calls.append((module, args))
        if writes_report:
            target = ctx.workspace / "out" / BUILD_DIR
            target.mkdir(parents=True, exist_ok=True)
            (target / REPORT_FILE).write_text(json.dumps(report))
        return code, lines

    run.calls = calls
    return run


def test_draft_build_needs_a_toolchain(workspace, monkeypatch):
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    with pytest.raises(CoreError) as excinfo:
        draft_build(resolve_context())
    assert "AI_RFC_TOOLCHAIN" in str(excinfo.value)


def test_draft_build_runs_the_verb_and_reads_the_report(
    workspace, monkeypatch, tmp_path
):
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
    (workspace.workspace / "refcache").mkdir()
    fake = _fake_run(
        {
            "commit": "c" * 40,
            "exit_code": 0,
            "findings": [],
            "outputs": {"draft-x.txt": {}},
        }
    )
    monkeypatch.setattr(build_core, "_run", fake)
    result = draft_build(resolve_context(), ref="draft-test-spec-00")
    module, args = fake.calls[0]
    assert module == "ai_rfc.draft" and args[0] == "build"
    assert "--ref" in args and args[args.index("--ref") + 1] == "draft-test-spec-00"
    assert "--toolchain" in args and "--refcache" in args
    assert result == {
        "exit_code": 0,
        "stderr": ["note: fake"],
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
    }
    assert isinstance(result["findings"], list)
    assert tools.ai_rfc_draft_lint()["metrics"] == result["metrics"]


def test_draft_build_does_not_read_a_stale_report_after_a_failed_run(
    workspace, monkeypatch, tmp_path
):
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
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
    fake = _fake_run(
        {}, code=1, writes_report=False, stderr=["error: nope: not a commit"]
    )
    monkeypatch.setattr(build_core, "_run", fake)
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
    fake = _fake_run(
        {}, code=1, writes_report=False, stderr=["error: nope: not a commit"]
    )
    monkeypatch.setattr(build_core, "_run", fake)
    result = draft_lint(resolve_context())
    assert result["exit_code"] == 1
    assert result["findings"] == result["stderr"] == ["error: nope: not a commit"]
    assert result["metrics"] == {}


def test_draft_lint_passes_worktree_or_not_in_the_argv(workspace, monkeypatch):
    fake = _fake_run({}, writes_report=False)
    monkeypatch.setattr(build_core, "_run", fake)

    draft_lint(resolve_context())
    module, args = fake.calls[0]
    assert module == "ai_rfc.draft" and args[0] == "lint"
    assert "--manifest" in args
    assert args[-1] == "--worktree"

    draft_lint(resolve_context(), worktree=False)
    module, args = fake.calls[1]
    assert module == "ai_rfc.draft" and args[0] == "lint"
    assert "--manifest" in args
    assert "--worktree" not in args
