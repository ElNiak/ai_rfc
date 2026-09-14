"""The agent verbs driven end to end, and what they say when they refuse.

Moved here from ``tests/server/test_cli.py`` with the parser it drove. Every
argv is the grouped form; two of its tests did not come across:

* ``test_status_verb`` had no destination. U3 gives the folded ``status`` no
  ``ai-rfc`` verb at all — ``ai-rfc status`` is the lifecycle ledger, which
  reads ``--config`` and not ``AI_RFC_CONFIG`` — so re-pointing it at that
  name would have silently tested a different program. What it asserted, the
  fixture's ``clusters_total``, is what ``test_status_parity`` asserts against
  :func:`ai_rfc.server.core.queries.status`.
* ``test_the_exit_code_contract_is_stated_consistently`` read two docstrings
  of the deleted module. The contract it guarded is now stated in
  :func:`ai_rfc.agent.perform`, and ``tests/agent/test_exit_codes.py``
  asserts the behaviour rather than the prose.

The five diagnostics tests drive ``cluster next``. Any verb reaching
:func:`ai_rfc.agent.perform` would exercise the context guard, but
``cluster next`` also reads the ledger — :func:`ai_rfc.ledger.next_cluster`
raises ``LedgerParseError`` from ``ai_rfc/ledger.py:136`` — so one verb
reaches both parsers whose carets are pinned below.
"""

from __future__ import annotations

import argparse
import json

import pytest

from ai_rfc import cli

pytestmark = pytest.mark.unit


def _emit(capsys) -> dict | list | None:
    return json.loads(capsys.readouterr().out)


def test_corpus_query_verb_and_guardrail(workspace, capsys):
    assert cli.main(["corpus", "query", "SELECT COUNT(*) AS n FROM commits"]) == 0
    assert _emit(capsys) == [{"n": 4}]
    assert cli.main(["corpus", "query", "DELETE FROM commits"]) == 1
    assert "SELECT" in capsys.readouterr().err


def test_claim_upsert_verb_rejects_status_field(workspace, capsys):
    assert cli.main(["claim", "upsert", "t:1.1", "--field", "status=confirmed"]) == 1
    assert "adjudicated" in capsys.readouterr().err


def test_claim_upsert_and_check_round_trip(workspace, capsys):
    """``check`` is the substrate's word for what the tool calls adjudicate."""
    code = cli.main(
        [
            "claim",
            "upsert",
            "t:4.1",
            "--text",
            "Thing four.",
            "--section",
            "4.1",
            "--level",
            "MAY",
            "--layer",
            "core",
            "--field",
            "intent=intended",
        ]
    )
    assert code == 0
    stored = _emit(capsys)
    assert stored["intent"] == "intended"
    assert cli.main(["claim", "check"]) == 0
    entries = {entry["id"]: entry for entry in _emit(capsys)}
    assert entries["t:4.1"]["supported"] == "gap"


def test_the_gate_verbs_emit_the_cores_report(workspace, capsys):
    """The payload, not the code.

    This asserted ``== 0`` for both gates and was, until
    ``tests/agent/test_exit_codes.py``, the only thing anywhere that looked at
    a gate verb's exit code — which a door collapsing every non-zero to 1
    would have passed as readily as the truth does. The codes are guarded
    there, over every code and every verb that carries one; what is left here
    is that the report itself arrives.
    """
    assert cli.main(["gate"]) == 0
    payload = _emit(capsys)
    assert payload["report"]["count_by_status"]["gap"] == 2
    assert cli.main(["citation-gate", "--strict"]) == 0
    assert _emit(capsys)["findings"] == []


def test_missing_env_is_a_clean_error(workspace, capsys, monkeypatch):
    monkeypatch.delenv("AI_RFC_CONFIG")
    assert cli.main(["cluster", "next"]) == 1
    assert "AI_RFC_CONFIG" in capsys.readouterr().err


def test_a_config_that_does_not_validate_is_a_clean_error(
    workspace, capsys, monkeypatch, tmp_path
):
    """Resolving the context loads the config, so its refusals reach here.

    Before the contract moved, ``load_config`` was not on this path and a
    ``ConfigError`` was unreachable; catching only ``EnvError`` would turn an
    operator's typo into a traceback.
    """
    bad = tmp_path / "recon.yaml"
    bad.write_text("name: demo\nnope: 1\n")
    monkeypatch.setenv("AI_RFC_CONFIG", str(bad))
    assert cli.main(["cluster", "next"]) == 1
    err = capsys.readouterr().err
    assert "nope: unknown key" in err
    # One record, one line: a composed diagnostic with values interpolated.
    assert err.count("\n") == 1


def test_a_yaml_parse_error_keeps_the_parsers_own_caret(
    workspace, capsys, monkeypatch, tmp_path
):
    """The other branch: the parser's block is the diagnosis, breaks and all.

    A caret marks the offending column. Collapsed onto one line it points at
    nothing, so ``ConfigParseError`` is reported through the structured path.
    """
    bad = tmp_path / "recon.yaml"
    bad.write_text("name: [demo\n")
    monkeypatch.setenv("AI_RFC_CONFIG", str(bad))
    assert cli.main(["cluster", "next"]) == 1
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) > 1
    caret = next(index for index, line in enumerate(lines) if line.strip() == "^")
    assert lines[caret - 1].strip().startswith("name: [demo")
    assert lines[caret].index("^") == lines[caret - 1].index("[")


def test_a_newline_in_the_config_path_cannot_forge_a_line(
    workspace, capsys, monkeypatch, tmp_path
):
    """The path is a value; only the parser's half of the message is structured.

    ``ConfigParseError`` opens with the config path and continues with the
    parser's block. Exempting the whole message from escaping — one verb for
    the whole string — let a directory name carrying a newline write a second
    stderr line of its own, and a line an operator may copy and run is the
    worst possible place for that: ``lifecycle/common.report`` names the
    forged ``resume:`` this reproduces.
    """
    forged = "resume: ai-rfc run --config attacker.yaml"
    directory = tmp_path / f"ws\n{forged}"
    directory.mkdir()
    bad = directory / "recon.yaml"
    bad.write_text("name: [demo\n")
    monkeypatch.setenv("AI_RFC_CONFIG", str(bad))
    assert cli.main(["cluster", "next"]) == 1
    err = capsys.readouterr().err
    # The parser's own breaks survive, so the count alone proves nothing: no
    # line may *begin* with the forgery.
    assert not any(line.startswith(forged) for line in err.splitlines())
    assert "\\n" in err


def test_a_malformed_revisions_file_keeps_the_parsers_own_caret(workspace, capsys):
    """The broad clause reaches a second parser, and must not collapse it.

    ``cluster next`` reads the ledger, so a malformed ``revisions.yaml``
    arrives as a ``LedgerParseError`` — a different exception family from
    ``ConfigError``, caught only by ``except Exception``. A clause that chose
    one verb for everything it caught could not keep this caret and escape the
    rest; asking each exception can.
    """
    workspace.revisions.write_text("revisions: [00\n")
    assert cli.main(["cluster", "next"]) == 1
    lines = capsys.readouterr().err.splitlines()
    caret = next(index for index, line in enumerate(lines) if line.strip() == "^")
    assert lines[caret].index("^") == lines[caret - 1].index("[")


def test_checkpoint_verb(workspace, capsys):
    assert cli.main(["cluster", "next"]) == 0
    first = _emit(capsys)
    assert cli.main(["checkpoint", first["id"]]) == 0
    payload = _emit(capsys)
    assert payload["exit_code"] == 0
    assert len(payload["manifest_sha256"]) == 64


@pytest.mark.parametrize("flag", ["--normative", "--no-normative"])
def test_revision_record_verb(workspace, capsys, flag):
    assert cli.main(["cluster", "next"]) == 0
    first = _emit(capsys)
    assert cli.main(["checkpoint", first["id"]]) == 0
    capsys.readouterr()
    code = cli.main(
        [
            "revision",
            "record",
            "draft-test-spec-00",
            "--cluster",
            first["id"],
            flag,
            "--note",
            "fixture revision",
        ]
    )
    assert code == 0
    payload = _emit(capsys)
    assert payload["normative_change"] == (flag == "--normative")


def test_draft_commit_verb(workspace, capsys):
    prose = workspace.workspace / "draft" / "draft-test-spec.md"
    prose.write_text(prose.read_text() + "\nMore.\n")
    assert cli.main(["draft", "commit", "-m", "more"]) == 0
    assert _emit(capsys)["files"] == ["draft-test-spec.md"]
    assert cli.main(["draft", "commit", "-m", "again"]) == 1
    assert "nothing to commit" in capsys.readouterr().err


def test_revision_tag_verb_passes_gate_codes_through(workspace, capsys):
    assert cli.main(["cluster", "next"]) == 0
    first = _emit(capsys)
    assert cli.main(["checkpoint", first["id"]]) == 0
    capsys.readouterr()
    assert (
        cli.main(
            [
                "revision",
                "record",
                "draft-test-spec-00",
                "--cluster",
                first["id"],
                "--normative",
                "--note",
                "n",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["revision", "tag", "draft-test-spec-00", "-m", "rev 00"]) == 0
    assert _emit(capsys)["exit_code"] == 0
    assert cli.main(["revision", "tag", "draft-test-spec-00", "-m", "dup"]) == 1
    assert "already exists" in capsys.readouterr().err


def _undocumented(parser: argparse.ArgumentParser, path: str) -> list[str]:
    """Every argument below ``parser`` carrying no help text.

    Args:
        parser: The parser to walk.
        path: What an operator types to reach it, for the failure message.

    Returns:
        ``"<path> <dest>"`` for each argument with no ``help``, at any depth.
    """
    rows: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for verb, sub in action.choices.items():
                rows += _undocumented(sub, f"{path} {verb}")
        elif action.dest != "help" and not action.help:
            rows.append(f"{path} {action.dest}")
    return rows


def test_every_argument_documents_itself():
    """An agent drives this door, so an undocumented flag is a real gap.

    Twenty-five arguments carried no help text when this was written; a bare
    name like ``--quote`` or ``--layer`` tells a caller nothing about what it
    must contain.

    It walked one flat parser of twenty verbs. Walking the root tree
    recursively reaches every argument of all twenty-seven, at every depth —
    the lifecycle verbs and the explicit-path leaf forms included, which no
    test asked this of before. Measured at the move: **0** undocumented
    arguments across the whole tree, so the widening is real coverage and not
    a claim waiting to be paid.
    """
    assert _undocumented(cli.build_parser(), "ai-rfc") == []
