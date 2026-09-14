"""One core, not two: the grouped verb and the parity verb do the same thing.

``tests/server/test_parity.py`` pins the MCP tool arm against the ``ai_rfc``
CLI. These pin the *new* front door against that same CLI, one verb per group,
so a group that re-implemented an operation rather than calling
:mod:`ai_rfc.server.core` fails here rather than after the fold.

Task 8 deletes ``ai_rfc/server/cli.py``, at which point these re-point at
``ai_rfc.server.tools`` exactly as the twins do; until then the CLI it deletes
is the arm with a verb to compare against.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ai_rfc import cli as root_cli
from ai_rfc.server import cli as parity_cli
from ai_rfc.server import tools

pytestmark = pytest.mark.unit

#: The day the question register is frozen at. ``asked_at`` and ``answered_at``
#: default to today and both arms write them into ``questions.yaml``, which the
#: twins compare byte for byte; a run straddling midnight would otherwise
#: compare two different days.
_PINNED_DAY = date(2026, 1, 2)

#: A structure body worth declaring: the render and the manifest digest both
#: change with it, so a twin over it is not degenerate.
_FIELDS = {
    "kind": "record",
    "title": "Message header",
    "section": "4",
    "fields": [{"name": "version", "type": "uint8", "claim": "t:1.1"}],
}

_QUESTION = "Which profiles does thing one hold in?"


def _twins(make_workspace):
    """Two byte-identical workspaces and the switcher between them."""
    build, use = make_workspace
    return build("root-arm"), build("parity-arm"), use


def _pin_the_register_clock(monkeypatch) -> None:
    """Freeze ``date.today()`` as the question register sees it.

    Args:
        monkeypatch: The test's patcher; the substitution is undone with it.
    """
    from ai_rfc.server.core import questions as core_questions

    monkeypatch.setattr(
        core_questions,
        "datetime",
        SimpleNamespace(date=SimpleNamespace(today=lambda: _PINNED_DAY)),
    )


def test_corpus_query_reaches_one_core(make_workspace, capsys):
    root_arm, parity_arm, use = _twins(make_workspace)
    sql = "SELECT sha, subject FROM commits ORDER BY sha"
    use(root_arm)
    assert root_cli.main(["corpus", "query", sql]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["corpus-query", sql]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity
    # Two empty result sets compare equal; the fixture corpus holds four
    # commits, so this is what proves either arm reached the index.
    assert len(from_root) == 4


def test_cluster_next_reaches_one_core(make_workspace, capsys):
    root_arm, parity_arm, use = _twins(make_workspace)
    use(root_arm)
    assert root_cli.main(["cluster", "next"]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["cluster-next"]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity
    # ``null`` decodes to the same None the verb returns when nothing is
    # outstanding: without this the twin would pass on two empty reads.
    assert from_root is not None and from_root["id"]


def test_claim_upsert_reaches_one_core(make_workspace, capsys):
    root_arm, parity_arm, use = _twins(make_workspace)
    fields = {
        "text": "Thing five.",
        "section": "5.1",
        "level": "MAY",
        "layer": "core",
    }
    argv = [
        "t:5.1",
        "--text",
        fields["text"],
        "--section",
        fields["section"],
        "--level",
        fields["level"],
        "--layer",
        fields["layer"],
        "--field",
        "intent=intended",
    ]
    use(root_arm)
    assert root_cli.main(["claim", "upsert", *argv]) == 0
    use(parity_arm)
    assert parity_cli.main(["claim-upsert", *argv]) == 0
    capsys.readouterr()
    assert (root_arm / "manifest.yaml").read_bytes() == (
        parity_arm / "manifest.yaml"
    ).read_bytes()
    # Equal-and-unwritten is the failure this guards: both manifests would
    # still be the fixture's.
    assert "t:5.1" in (root_arm / "manifest.yaml").read_text()


def test_claim_check_reaches_one_core(make_workspace, capsys):
    """``claim-adjudicate`` under the substrate's own word for it (§6)."""
    root_arm, parity_arm, use = _twins(make_workspace)
    use(root_arm)
    assert root_cli.main(["claim", "check"]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["claim-adjudicate"]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity
    # The fixture's two claims, adjudicated; an empty preview compares equal.
    assert {row["id"] for row in from_root} == {"t:1.1", "t:2.1"}


def test_question_draft_reaches_one_core(make_workspace, capsys, monkeypatch):
    root_arm, parity_arm, use = _twins(make_workspace)
    _pin_the_register_clock(monkeypatch)
    argv = [_QUESTION, "--claim", "t:1.1"]
    use(root_arm)
    assert root_cli.main(["question", "draft", *argv]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["question-draft", *argv]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity
    # Linking the claim is the manifest half of this verb; an entry written to
    # the register and linked to nothing returns [] on both arms.
    assert from_root["linked"] == ["t:1.1"]
    for name in ("questions.yaml", "manifest.yaml"):
        assert (root_arm / name).read_bytes() == (parity_arm / name).read_bytes()


def test_question_export_emits_the_cores_string_unchanged(
    make_workspace, capsys, monkeypatch
):
    """The verb's stdout is the core's return value, byte for byte.

    Compared against the core function and not only against the parity CLI,
    because the defect ``3cdeb29`` fixed was a bare ``print()`` appending a
    newline the tool arm never produces. Lifting a pre-fix reading of
    ``server/cli.py:311`` would put that newline back, and a CLI-to-CLI
    comparison would only catch it while the CLI it was lifted from still
    exists — which Task 8 ends.
    """
    root_arm, parity_arm, use = _twins(make_workspace)
    _pin_the_register_clock(monkeypatch)
    for root in (root_arm, parity_arm):
        use(root)
        tools.ai_rfc_question_draft(_QUESTION, ["t:1.1"])
    use(root_arm)
    from_core = tools.ai_rfc_question_export()
    assert root_cli.main(["question", "export"]) == 0
    from_root = capsys.readouterr().out
    use(parity_arm)
    assert parity_cli.main(["question-export"]) == 0
    from_parity = capsys.readouterr().out
    # An empty register renders "No open questions.\n" on every side, which
    # would agree without any arm having rendered a question at all.
    assert _QUESTION in from_core
    assert from_root == from_core and from_root == from_parity


def test_answer_record_reaches_one_core(make_workspace, capsys, monkeypatch):
    root_arm, parity_arm, use = _twins(make_workspace)
    _pin_the_register_clock(monkeypatch)
    quote = "it holds in every profile"
    for root in (root_arm, parity_arm):
        use(root)
        tools.ai_rfc_question_draft(_QUESTION, ["t:1.1"])
        (root / "interviews" / "int-001.md").write_text(
            f"Q: which profiles?\nA: {quote}, without exception.\n"
        )
    argv = [
        "q-001",
        "--answer",
        "Every profile.",
        "--by",
        "the author",
        "--transcript",
        "int-001.md",
        "--quote",
        quote,
        "--exact-wording-confirmed",
    ]
    use(root_arm)
    assert root_cli.main(["answer", "record", *argv]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["answer-record", *argv]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity
    # The interview anchor and the sign-off are the two manifest writes this
    # verb exists for, and the sign-off is the half the flag decides: both are
    # empty lists on a refusal, which would compare equal across the arms.
    assert from_root["anchored"] == ["t:1.1"]
    assert from_root["signed_off"] == ["t:1.1"]
    for name in ("questions.yaml", "manifest.yaml"):
        assert (root_arm / name).read_bytes() == (parity_arm / name).read_bytes()


def test_revision_record_reaches_one_core(make_workspace, capsys):
    root_arm, parity_arm, use = _twins(make_workspace)
    for root in (root_arm, parity_arm):
        use(root)
        cluster_id = tools.ai_rfc_cluster_next()["id"]
        assert tools.ai_rfc_checkpoint(cluster_id)["exit_code"] == 0
    argv = [
        "draft-test-spec-00",
        "--cluster",
        cluster_id,
        "--normative",
        "--note",
        "first",
    ]
    use(root_arm)
    assert root_cli.main(["revision", "record", *argv]) == 0
    use(parity_arm)
    assert parity_cli.main(["revision-record", *argv]) == 0
    capsys.readouterr()
    assert (root_arm / "revisions.yaml").read_bytes() == (
        parity_arm / "revisions.yaml"
    ).read_bytes()
    # An unwritten register is byte-identical on both arms.
    assert "draft-test-spec-00" in (root_arm / "revisions.yaml").read_text()


def test_checkpoint_reaches_one_core(make_workspace, capsys):
    """The one payload whose twins cannot be compared whole.

    ``stderr`` carries the absolute path of the checkpoint just written, so the
    two arms differ there by construction and must. Rather than normalise the
    roots away — a comparison that normalises can pass while the two sides
    genuinely differ — the line is asserted to name each arm's *own* directory,
    which is the property the difference stands for.
    """
    root_arm, parity_arm, use = _twins(make_workspace)
    use(root_arm)
    cluster_id = tools.ai_rfc_cluster_next()["id"]
    assert root_cli.main(["checkpoint", cluster_id]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["checkpoint", cluster_id]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root["exit_code"] == from_parity["exit_code"] == 0
    # The digest is what proves both arms froze one manifest; an absent key on
    # both sides would compare equal, so it is read rather than compared alone.
    assert from_root["manifest_sha256"] == from_parity["manifest_sha256"]
    assert from_root["manifest_sha256"]
    record = Path("checkpoints") / cluster_id / "checkpoint.json"
    for arm, payload in ((root_arm, from_root), (parity_arm, from_parity)):
        assert payload["stderr"] == [
            f"note: checkpoint written to {arm / record.parent}"
        ]
    assert (root_arm / record).read_bytes() == (parity_arm / record).read_bytes()


def test_gate_reaches_one_core_and_keeps_its_exit_code(make_workspace, capsys):
    """Clean, then strict over one overstated claim.

    The strict half is the one that matters twice: it pins the twin *and* the
    exit code, which the door returns raw rather than collapsing to 1.
    """
    root_arm, parity_arm, use = _twins(make_workspace)
    report = Path("out") / "report.json"
    use(root_arm)
    assert root_cli.main(["gate"]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["gate"]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity and from_root["exit_code"] == 0
    assert (root_arm / report).read_bytes() == (parity_arm / report).read_bytes()

    for root in (root_arm, parity_arm):
        manifest = root / "manifest.yaml"
        document = yaml.safe_load(manifest.read_text())
        document["requirements"]["t:1.1"]["status"] = "confirmed"
        manifest.write_text(yaml.safe_dump(document, sort_keys=True))
    use(root_arm)
    assert root_cli.main(["gate", "--strict"]) == 3
    strict_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["gate", "--strict"]) == 3
    strict_parity = json.loads(capsys.readouterr().out)
    assert strict_root == strict_parity and strict_root["exit_code"] == 3


def test_citation_gate_reaches_one_core(make_workspace, capsys):
    root_arm, parity_arm, use = _twins(make_workspace)
    report = Path("out") / "gate-report.json"
    use(root_arm)
    assert root_cli.main(["citation-gate"]) == 0
    from_root = json.loads(capsys.readouterr().out)
    use(parity_arm)
    assert parity_cli.main(["citation-gate"]) == 0
    from_parity = json.loads(capsys.readouterr().out)
    assert from_root == from_parity and from_root["exit_code"] == 0
    assert from_root["stderr"] == ["note: gate clean"] and from_root["findings"] == []
    assert (root_arm / report).read_bytes() == (parity_arm / report).read_bytes()


def test_structure_upsert_reaches_one_core(make_workspace, capsys):
    root_arm, parity_arm, use = _twins(make_workspace)
    argv = ["header", "--json", json.dumps(_FIELDS)]
    use(root_arm)
    assert root_cli.main(["structure", "upsert", *argv]) == 0
    use(parity_arm)
    assert parity_cli.main(["structure-upsert", *argv]) == 0
    capsys.readouterr()
    assert (root_arm / "manifest.yaml").read_bytes() == (
        parity_arm / "manifest.yaml"
    ).read_bytes()
    # A refused upsert leaves both manifests the fixture's, byte for byte.
    assert "Message header" in (root_arm / "manifest.yaml").read_text()
