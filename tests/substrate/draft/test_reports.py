"""The report writers and the toolchain resolver, beside the APIs they serve.

Each writer used to live in the ``draft`` CLI branch that parses argv, so the
only way to reach one was to run the CLI as a program. These pin the bytes each
writer puts on disk against the branch it was lifted from: a writer that
serialised differently would still satisfy a "the file exists" assertion.
"""

import json
from pathlib import Path

import pytest

from ai_rfc.draft import completeness as completeness_module
from ai_rfc.draft.build import (
    TOOLCHAIN_ENV,
    BuildError,
    load_toolchain,
    resolve_toolchain,
)
from ai_rfc.draft.completeness import write_completeness_report
from ai_rfc.draft.gate import write_gate_report
from ai_rfc.draft.lint import lint, write_lint_report

pytestmark = pytest.mark.unit

#: What ``draft gate --out`` froze before the writer moved: a sorted-key object
#: under one ``findings`` key, two-space indent, one trailing newline.
GATE_REPORT_BYTES = b"""{
  "findings": [
    "one: a tag nothing registers",
    "two: a cluster ordinal that does not increase"
  ]
}
"""


def test_the_gate_report_holds_the_bytes_the_cli_branch_froze(tmp_path: Path):
    out = tmp_path / "out"
    path = write_gate_report(
        out,
        (
            "one: a tag nothing registers",
            "two: a cluster ordinal that does not increase",
        ),
    )

    assert path == out / "gate-report.json"
    assert path.read_bytes() == GATE_REPORT_BYTES


def test_the_gate_report_of_a_clean_run_is_an_empty_list(tmp_path: Path):
    """A clean gate still writes the file; a caller reads it either way."""
    path = write_gate_report(tmp_path / "out", ())

    assert path.read_bytes() == b'{\n  "findings": []\n}\n'


def test_the_gate_report_creates_its_output_directory(tmp_path: Path):
    """The CLI branch did the ``mkdir``; the writer owns it now."""
    out = tmp_path / "absent" / "deeper"
    assert not out.exists()

    assert write_gate_report(out, ()).exists()


def test_the_lint_report_holds_the_serialiser_the_cli_branch_used(tmp_path: Path):
    """``LintReport.to_json`` re-derives ``findings``; ``asdict`` would not."""
    report = lint("# Spec\n\nThe system does the thing.\n")
    path = write_lint_report(tmp_path / "out", report)

    assert path == tmp_path / "out" / "lint-report.json"
    assert path.read_bytes() == report.to_json().encode()
    assert "findings" in json.loads(path.read_text())


def test_the_lint_report_creates_its_output_directory(tmp_path: Path):
    out = tmp_path / "absent" / "deeper"
    assert write_lint_report(out, lint("# Spec\n")).exists()


def test_the_completeness_report_holds_the_serialiser_the_cli_branch_used(
    sparse_workspace: dict[str, Path], tmp_path: Path
):
    report = completeness_module.build(
        sparse_workspace["timeline"],
        sparse_workspace["checkpoints"],
        sparse_workspace["manifest"],
        sparse_workspace["revisions"],
        sparse_workspace["repo"],
    )
    out = tmp_path / "out"
    path = write_completeness_report(out, report)

    assert path == out / "completeness.json"
    assert path.read_bytes() == completeness_module.to_json(report).encode()


def test_the_completeness_report_creates_its_output_directory(
    sparse_workspace: dict[str, Path], tmp_path: Path
):
    report = completeness_module.build(
        sparse_workspace["timeline"],
        sparse_workspace["checkpoints"],
        sparse_workspace["manifest"],
        sparse_workspace["revisions"],
        sparse_workspace["repo"],
    )
    out = tmp_path / "absent" / "deeper"

    assert write_completeness_report(out, report).exists()


def test_an_explicit_toolchain_is_read(toolchain_record: Path, monkeypatch):
    monkeypatch.delenv(TOOLCHAIN_ENV, raising=False)

    assert resolve_toolchain(toolchain_record) == load_toolchain(toolchain_record)


def test_the_environment_names_the_toolchain_when_the_argument_does_not(
    toolchain_record: Path, monkeypatch
):
    monkeypatch.setenv(TOOLCHAIN_ENV, str(toolchain_record))

    assert resolve_toolchain(None) == load_toolchain(toolchain_record)


def test_the_argument_wins_over_the_environment(toolchain_record: Path, monkeypatch):
    monkeypatch.setenv(TOOLCHAIN_ENV, str(toolchain_record.parent / "absent.json"))

    assert resolve_toolchain(toolchain_record) == load_toolchain(toolchain_record)


def test_neither_an_argument_nor_the_environment_is_a_refusal(monkeypatch):
    monkeypatch.delenv(TOOLCHAIN_ENV, raising=False)

    with pytest.raises(BuildError) as excinfo:
        resolve_toolchain(None)
    assert str(excinfo.value) == (
        f"no toolchain; pass --toolchain or set {TOOLCHAIN_ENV} "
        "(ai-rfc toolchain provision writes it)"
    )


def test_an_empty_environment_variable_counts_as_unset(monkeypatch):
    """``AI_RFC_TOOLCHAIN=`` named no record before the resolver moved either.

    ``os.environ[...]`` would hand ``Path("")`` to the loader, whose refusal
    names the current directory rather than the missing configuration.
    """
    monkeypatch.setenv(TOOLCHAIN_ENV, "")

    with pytest.raises(BuildError) as excinfo:
        resolve_toolchain(None)
    assert TOOLCHAIN_ENV in str(excinfo.value)


def test_a_toolchain_missing_an_executable_is_a_refusal(
    toolchain_record: Path, monkeypatch
):
    monkeypatch.delenv(TOOLCHAIN_ENV, raising=False)
    Path(json.loads(toolchain_record.read_text())["node"]["idnits"]).unlink()

    with pytest.raises(BuildError) as excinfo:
        resolve_toolchain(toolchain_record)
    assert str(excinfo.value).startswith("toolchain incomplete: idnits: ")
