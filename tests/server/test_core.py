import pytest

from ai_rfc.server.core import CoreError, GuardrailError
from ai_rfc.server.core.claims import (
    adjudicate_preview,
    record_statuses,
    upsert_claim,
)
from ai_rfc.server.core.gates import citation_gate, manifest_gate, write_checkpoint
from ai_rfc.server.core.queries import (
    cluster_get,
    cluster_next,
    corpus_query,
    status,
)
from ai_rfc.server.core.questions import draft_question, export_open, record_answer
from ai_rfc.server.core.revisions import record_revision


def test_upsert_rejects_status(workspace):
    with pytest.raises(GuardrailError) as excinfo:
        upsert_claim(workspace, "t:1.1", {"status": "confirmed"})
    assert "adjudicated" in str(excinfo.value)


def test_upsert_rejects_unknown_fields(workspace):
    with pytest.raises(GuardrailError):
        upsert_claim(workspace, "t:1.1", {"severity": "high"})


def test_upsert_new_claim_requires_base_fields(workspace):
    with pytest.raises(CoreError) as excinfo:
        upsert_claim(workspace, "t:9.9", {"text": "incomplete"})
    assert "required" in str(excinfo.value)


def test_upsert_adds_a_valid_claim(workspace):
    stored = upsert_claim(
        workspace,
        "t:3.1",
        {
            "text": "Thing three.",
            "section": "3.1",
            "level": "MAY",
            "layer": "core",
            "intent": "intended",
        },
    )
    assert stored["text"] == "Thing three."
    assert stored["status"] == "gap"
    preview = {entry["id"]: entry for entry in adjudicate_preview(workspace)}
    assert preview["t:3.1"]["supported"] == "gap"


def test_invalid_upsert_leaves_manifest_untouched(workspace):
    before = workspace.manifest.read_bytes()
    with pytest.raises(Exception):
        upsert_claim(
            workspace,
            "t:9.9",
            {
                "text": "bad",
                "section": "9.9",
                "level": "MUST",
                "layer": "core",
                "req_class": "not-a-class",
            },
        )
    assert workspace.manifest.read_bytes() == before


def test_adjudication_and_status_recording(workspace):
    preview = {entry["id"]: entry for entry in adjudicate_preview(workspace)}
    assert preview["t:1.1"]["supported"] == "inferred"
    assert preview["t:2.1"]["supported"] == "confirmed"
    changed = record_statuses(workspace)
    assert {entry["id"] for entry in changed} == {"t:1.1", "t:2.1"}
    assert record_statuses(workspace) == []
    with pytest.raises(CoreError):
        record_statuses(workspace, ["t:404"])


def test_corpus_query_is_select_only(workspace):
    rows = corpus_query(workspace, "SELECT COUNT(*) AS n FROM commits")
    assert rows == [{"n": 4}]
    with pytest.raises(GuardrailError):
        corpus_query(workspace, "DELETE FROM commits")
    with pytest.raises(GuardrailError):
        corpus_query(workspace, "SELECT 1; SELECT 2")


def test_cluster_navigation_and_get(workspace):
    first = cluster_next(workspace)
    assert first["ordinal"] == 1
    view = cluster_get(workspace, first["id"], include_patch=True, patch_limit=50)
    assert view["view"]["id"] == first["id"]
    assert view["patch_total_bytes"] > 0
    assert len(view["patch"]) <= 50
    with pytest.raises(CoreError):
        cluster_get(workspace, "c9999-pr-000000000000")


def test_a_cluster_is_processed_only_once_its_checkpoint_record_exists(workspace):
    """An empty directory named after a cluster does not process it.

    Counting the directory alone lets a run skip the whole window by
    creating one directory per cluster, with no checkpoint behind any of
    them.
    """
    first = cluster_next(workspace)
    bare = workspace.workspace / "checkpoints" / first["id"]
    bare.mkdir(parents=True)
    assert cluster_next(workspace)["id"] == first["id"]

    (bare / "checkpoint.json").write_text("{}")
    assert cluster_next(workspace)["id"] != first["id"]


def test_checkpoint_revision_and_next_advance(workspace):
    first = cluster_next(workspace)
    result = write_checkpoint(workspace, first["id"])
    assert result["exit_code"] == 0
    assert len(result["manifest_sha256"]) == 64
    second = cluster_next(workspace)
    assert second["ordinal"] == 2
    entry = record_revision(
        workspace, "draft-test-spec-00", first["id"], True, "initial"
    )
    assert entry["checkpoint_manifest_sha256"] == result["manifest_sha256"]
    with pytest.raises(CoreError):
        record_revision(workspace, "draft-test-spec-00", first["id"], True, "dup")
    with pytest.raises(CoreError):
        record_revision(workspace, "draft-test-spec-01", second["id"], True, "x")


def test_question_round_trip_and_sign_off_rule(workspace):
    drafted = draft_question(
        workspace, "Is 'Thing one.' deliberate?", ["t:1.1"]
    )
    assert drafted["id"] == "q-001"
    assert drafted["linked"] == ["t:1.1"]
    with pytest.raises(GuardrailError):
        draft_question(workspace, "again?", ["t:1.1"])
    with pytest.raises(CoreError):
        draft_question(workspace, "ghost?", ["t:404"])
    assert "q-001" in export_open(workspace)

    with pytest.raises(CoreError):
        record_answer(
            workspace,
            "q-001",
            "yes",
            "dev-01",
            "int-001.md",
            "yes it is deliberate",
        )
    transcript = workspace.workspace / "interviews" / "int-001.md"
    transcript.write_text("2026-08-25, dev-01: yes it is deliberate.\n")
    with pytest.raises(GuardrailError):
        record_answer(
            workspace, "q-001", "yes", "dev-01", "int-001.md", "not in there"
        )
    result = record_answer(
        workspace,
        "q-001",
        "Yes, deliberate.",
        "dev-01",
        "int-001.md",
        "yes it is deliberate",
    )
    assert result["anchored"] == ["t:1.1"]
    assert result["signed_off"] == []
    preview = {entry["id"]: entry for entry in adjudicate_preview(workspace)}
    assert preview["t:1.1"]["supported"] == "confirmed"

    drafted_two = draft_question(workspace, "Exact wording ok?", ["t:2.1"])
    transcript.write_text(
        transcript.read_text() + "dev-01: I confirm the exact wording.\n"
    )
    result_two = record_answer(
        workspace,
        drafted_two["id"],
        "Confirmed verbatim.",
        "dev-01",
        "int-001.md",
        "I confirm the exact wording",
        author_confirmed_exact_text=True,
    )
    assert result_two["signed_off"] == ["t:2.1"]


def test_gates_and_status(workspace):
    linted = manifest_gate(workspace)
    assert linted["exit_code"] == 0
    assert linted["report"]["count_by_status"]["gap"] == 2
    assert linted["report"]["unverified_anchors"] == []

    gate = citation_gate(workspace, strict=True)
    assert gate["findings"] == []
    assert gate["exit_code"] == 0

    composite = status(workspace)
    assert composite["clusters_total"] == 2
    assert composite["clusters_processed"] == 0
    assert composite["next_cluster"].startswith("c0001-")
    assert composite["questions"] == {"open": 0, "answered": 0, "withdrawn": 0}


@pytest.mark.parametrize("quote", ["", "   "])
def test_a_blank_quote_is_refused(workspace, quote):
    """A blank quote is a substring of every transcript, so it must be refused.

    Without this the sign-off path is reachable on evidence nobody quoted:
    ``"" in transcript`` is True, and a whitespace-only quote points at
    nothing an author can be held to either.
    """
    draft_question(workspace, "Is 'Thing one.' deliberate?", ["t:1.1"])
    transcript = workspace.workspace / "interviews" / "int-001.md"
    transcript.write_text("2026-08-25, dev-01: yes it is deliberate.\n")
    with pytest.raises(GuardrailError) as excinfo:
        record_answer(
            workspace,
            "q-001",
            "yes",
            "dev-01",
            "int-001.md",
            quote,
            author_confirmed_exact_text=True,
        )
    assert "non-empty" in str(excinfo.value)
