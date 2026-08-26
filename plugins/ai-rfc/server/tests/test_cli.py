import json

import pytest

from ai_rfc_server import cli


def _emit(capsys) -> dict | list | None:
    return json.loads(capsys.readouterr().out)


def test_status_verb(workspace, capsys):
    assert cli.main(["status"]) == 0
    payload = _emit(capsys)
    assert payload["clusters_total"] == 2


def test_corpus_query_verb_and_guardrail(workspace, capsys):
    assert cli.main(["corpus-query", "SELECT COUNT(*) AS n FROM commits"]) == 0
    assert _emit(capsys) == [{"n": 4}]
    assert cli.main(["corpus-query", "DELETE FROM commits"]) == 1
    assert "SELECT" in capsys.readouterr().err


def test_claim_upsert_verb_rejects_status_field(workspace, capsys):
    assert cli.main(["claim-upsert", "t:1.1", "--field", "status=confirmed"]) == 1
    assert "adjudicated" in capsys.readouterr().err


def test_claim_upsert_and_adjudicate_round_trip(workspace, capsys):
    code = cli.main(
        [
            "claim-upsert",
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
    assert cli.main(["claim-adjudicate"]) == 0
    entries = {entry["id"]: entry for entry in _emit(capsys)}
    assert entries["t:4.1"]["supported"] == "gap"


def test_gate_verbs_pass_exit_codes_through(workspace, capsys):
    assert cli.main(["gate"]) == 0
    payload = _emit(capsys)
    assert payload["report"]["count_by_status"]["gap"] == 2
    assert cli.main(["citation-gate", "--strict"]) == 0
    assert _emit(capsys)["findings"] == []


def test_missing_env_is_a_clean_error(workspace, capsys, monkeypatch):
    monkeypatch.delenv("ARFC_WORKSPACE")
    assert cli.main(["status"]) == 1
    assert "ARFC_WORKSPACE" in capsys.readouterr().err


def test_checkpoint_verb(workspace, capsys):
    assert cli.main(["cluster-next"]) == 0
    first = _emit(capsys)
    assert cli.main(["checkpoint", first["id"]]) == 0
    payload = _emit(capsys)
    assert payload["exit_code"] == 0
    assert len(payload["manifest_sha256"]) == 64


@pytest.mark.parametrize("flag", ["--normative", "--no-normative"])
def test_revision_record_verb(workspace, capsys, flag):
    assert cli.main(["cluster-next"]) == 0
    first = _emit(capsys)
    assert cli.main(["checkpoint", first["id"]]) == 0
    capsys.readouterr()
    code = cli.main(
        [
            "revision-record",
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
    assert cli.main(["draft-commit", "-m", "more"]) == 0
    assert _emit(capsys)["files"] == ["draft-test-spec.md"]
    assert cli.main(["draft-commit", "-m", "again"]) == 1
    assert "nothing to commit" in capsys.readouterr().err


def test_revision_tag_verb_passes_gate_codes_through(workspace, capsys):
    assert cli.main(["cluster-next"]) == 0
    first = _emit(capsys)
    assert cli.main(["checkpoint", first["id"]]) == 0
    capsys.readouterr()
    assert (
        cli.main(
            [
                "revision-record",
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
    assert cli.main(["revision-tag", "draft-test-spec-00", "-m", "rev 00"]) == 0
    assert _emit(capsys)["exit_code"] == 0
    assert cli.main(["revision-tag", "draft-test-spec-00", "-m", "dup"]) == 1
    assert "already exists" in capsys.readouterr().err
