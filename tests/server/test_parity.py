"""Tool-arm vs CLI-arm parity: same operation, byte-identical results.

The twin workspaces are built with pinned commit dates, so before any
operation their files are byte-identical; after one operation through each
frontend they must still be.

The CLI arm is ``ai-rfc <group> <verb>`` — the one door. It was
``ai_rfc <verb>`` until that second parser was deleted, and the twins moved
with it rather than being retired: what they pin is that the two *frontends*
agree, and which program the CLI arm is has never been the claim. The grouped
argv are the argv an agent types; the folded ``draft`` verbs are spelled in
their **workspace** form, with no path, because a path selects the explicit
form instead — which prints no JSON and reads a different revision.
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml

from ai_rfc import cli
from ai_rfc.draft.build import BUILD_DIR
from ai_rfc.draft.build import REPORT_FILE as BUILD_REPORT
from ai_rfc.draft.build import BuildError
from ai_rfc.server import tools
from ai_rfc.server.core import build as build_core
from ai_rfc.server.core import queries
from ai_rfc.server.paths import resolve_context
from ai_rfc.server.testing import git


def _twins(make_workspace):
    build, use = make_workspace
    return build("tool-arm"), build("cli-arm"), use


def test_twin_workspaces_start_identical(make_workspace):
    tool_arm, cli_arm, _ = _twins(make_workspace)
    for name in ("manifest.yaml", "corpus/commits.jsonl", "timeline/timeline.json"):
        assert (tool_arm / name).read_bytes() == (cli_arm / name).read_bytes()


def test_claim_upsert_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    fields = {
        "text": "Thing five.",
        "section": "5.1",
        "level": "MAY",
        "layer": "core",
        "intent": "intended",
    }
    use(tool_arm)
    tools.ai_rfc_claim_upsert("t:5.1", dict(fields))
    use(cli_arm)
    assert (
        cli.main(
            [
                "claim",
                "upsert",
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
        )
        == 0
    )
    capsys.readouterr()
    assert (tool_arm / "manifest.yaml").read_bytes() == (
        cli_arm / "manifest.yaml"
    ).read_bytes()


def test_record_status_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    tools.ai_rfc_claim_record_status()
    use(cli_arm)
    assert cli.main(["claim", "record-status"]) == 0
    capsys.readouterr()
    assert (tool_arm / "manifest.yaml").read_bytes() == (
        cli_arm / "manifest.yaml"
    ).read_bytes()


def test_read_parity_adjudicate(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    from_tool = tools.ai_rfc_claim_adjudicate()
    use(cli_arm)
    assert cli.main(["claim", "check"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli


def test_every_tool_is_in_the_parity_table():
    table = (Path(__file__).resolve().parents[2] / "docs" / "parity.md").read_text()
    for tool in tools.ALL_TOOLS:
        assert f"`{tool.__name__}`" in table, tool.__name__


_PINNED = "2026-01-02T00:00:00+00:00"


def test_draft_commit_parity(make_workspace, capsys, monkeypatch):
    tool_arm, cli_arm, use = _twins(make_workspace)
    monkeypatch.setenv("GIT_AUTHOR_DATE", _PINNED)
    monkeypatch.setenv("GIT_COMMITTER_DATE", _PINNED)
    for root in (tool_arm, cli_arm):
        prose = root / "draft" / "draft-test-spec.md"
        prose.write_text(prose.read_text() + "\nMore prose.\n")
    use(tool_arm)
    from_tool = tools.ai_rfc_draft_commit("more prose")
    use(cli_arm)
    assert cli.main(["draft", "commit", "-m", "more prose"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli
    assert git(tool_arm / "draft", "rev-parse", "HEAD") == git(
        cli_arm / "draft", "rev-parse", "HEAD"
    )


def test_revision_tag_parity(make_workspace, capsys, monkeypatch):
    tool_arm, cli_arm, use = _twins(make_workspace)
    monkeypatch.setenv("GIT_AUTHOR_DATE", _PINNED)
    monkeypatch.setenv("GIT_COMMITTER_DATE", _PINNED)
    for root in (tool_arm, cli_arm):
        use(root)
        first = tools.ai_rfc_cluster_next()
        tools.ai_rfc_checkpoint(first["id"])
        tools.ai_rfc_revision_record("draft-test-spec-00", first["id"], True, "initial")
    use(tool_arm)
    from_tool = tools.ai_rfc_revision_tag("draft-test-spec-00", "revision 00")
    use(cli_arm)
    assert cli.main(["revision", "tag", "draft-test-spec-00", "-m", "revision 00"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli and from_tool["exit_code"] == 0
    assert git(tool_arm / "draft", "cat-file", "-p", "draft-test-spec-00") == git(
        cli_arm / "draft", "cat-file", "-p", "draft-test-spec-00"
    )


def test_draft_lint_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    via_tool = tools.ai_rfc_draft_lint()
    use(cli_arm)
    assert cli.main(["draft", "lint"]) == 0
    via_cli = json.loads(capsys.readouterr().out)
    assert (
        via_tool["metrics"] == via_cli["metrics"]
        and via_tool["findings"] == via_cli["findings"]
    )


FIELDS = {
    "kind": "record",
    "title": "Message header",
    "section": "4",
    "fields": [{"name": "version", "type": "uint8", "claim": "t:1.1"}],
}


def test_structure_upsert_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    tools.ai_rfc_structure_upsert("header", FIELDS)
    use(cli_arm)
    assert (
        cli.main(["structure", "upsert", "header", "--json", json.dumps(FIELDS)]) == 0
    )
    capsys.readouterr()
    assert (tool_arm / "manifest.yaml").read_bytes() == (
        cli_arm / "manifest.yaml"
    ).read_bytes()


def test_draft_render_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    tools.ai_rfc_structure_upsert("header", FIELDS)
    from_tool = tools.ai_rfc_draft_render()
    use(cli_arm)
    tools.ai_rfc_structure_upsert("header", FIELDS)
    assert cli.main(["draft", "render"]) == 0
    from_cli = capsys.readouterr().out
    # print() adds no quotes; _emit would have.
    assert not from_cli.lstrip().startswith('"')
    # Byte-for-byte, not stripped: end="" is part of C25, and a bare print()
    # would add a trailing newline the tool arm never produces.
    assert from_cli == from_tool


def test_consolidation_checkpoint_and_revision_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)

    use(tool_arm)
    first = tools.ai_rfc_cluster_next()["id"]
    assert tools.ai_rfc_checkpoint(first)["exit_code"] == 0
    tools.ai_rfc_revision_record("draft-test-spec-00", first, True, "first")
    # A consolidation that changed nothing digests the same manifest as the
    # cluster checkpoint, which would leave both arms comparing a degenerate
    # record. Declaring a structure is the change a consolidation is for.
    tools.ai_rfc_structure_upsert("header", FIELDS)
    assert (
        tools.ai_rfc_checkpoint(first, consolidation=1, base=f"checkpoints/{first}")[
            "exit_code"
        ]
        == 0
    )
    tools.ai_rfc_revision_record(
        "draft-test-spec-01",
        first,
        False,
        "consolidated",
        kind="consolidation",
        checkpoint="consolidations/01",
    )

    use(cli_arm)
    assert cli.main(["checkpoint", first]) == 0
    assert (
        cli.main(
            [
                "revision",
                "record",
                "draft-test-spec-00",
                "--cluster",
                first,
                "--normative",
                "--note",
                "first",
            ]
        )
        == 0
    )
    assert (
        cli.main(["structure", "upsert", "header", "--json", json.dumps(FIELDS)]) == 0
    )
    assert (
        cli.main(
            [
                "checkpoint",
                first,
                "--consolidation",
                "1",
                "--base",
                f"checkpoints/{first}",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "revision",
                "record",
                "draft-test-spec-01",
                "--cluster",
                first,
                "--no-normative",
                "--note",
                "consolidated",
                "--kind",
                "consolidation",
                "--checkpoint",
                "consolidations/01",
            ]
        )
        == 0
    )
    capsys.readouterr()
    for name in ("revisions.yaml", "consolidations/01/checkpoint.json"):
        assert (tool_arm / name).read_bytes() == (cli_arm / name).read_bytes()
    record = json.loads((tool_arm / "consolidations/01/checkpoint.json").read_text())
    # Both arms share one core, so byte equality alone cannot tell a real
    # consolidation from a degenerate re-freeze of the cluster's manifest.
    assert record["kind"] == "consolidation"
    assert record["structures_sha256"]


def test_every_tool_still_appears_in_the_table():
    # ALL_TOOLS grew to 20; the table must have kept up.
    assert len(tools.ALL_TOOLS) == 20


def test_the_lint_metrics_carry_the_structures_block(workspace):
    # _METRIC_KEYS is a closed tuple; without `extra` in it the whole block is
    # computed, written to lint-report.json, and dropped before the tool returns.
    from ai_rfc.server.core import structures as core_structures

    core_structures.upsert_structure(workspace, "header", FIELDS)
    metrics = tools.ai_rfc_draft_lint(worktree=True)["metrics"]
    assert "structures" in metrics["extra"]
    assert "data_model_claims_unbound" in metrics["extra"]
    # Key presence alone passes on two empty values; the count is what proves
    # the workspace manifest reached the lint.
    assert metrics["extra"]["structures"]["defined"] == 1


#: The day the question register is frozen at, matching ``_PINNED``'s date.
_PINNED_DAY = date(2026, 1, 2)


def _pin_the_register_clock(monkeypatch) -> None:
    """Freeze ``date.today()`` as the question register sees it.

    ``asked_at`` and ``answered_at`` default to today and both arms write them
    into ``questions.yaml``, which the twins compare byte for byte. A run
    straddling midnight would otherwise compare two different days and fail for
    a reason that has nothing to do with parity.

    Args:
        monkeypatch: The test's patcher; the substitution is undone with it.
    """
    from ai_rfc.server.core import questions as core_questions

    monkeypatch.setattr(
        core_questions,
        "datetime",
        SimpleNamespace(date=SimpleNamespace(today=lambda: _PINNED_DAY)),
    )


def test_status_parity(make_workspace):
    """The tool against ``queries.status``, not against an ``ai_rfc`` verb (D16).

    Shaped unlike its nine siblings on purpose, and not to be "fixed" into
    their shape. U3 gives the folded ``status`` no ``ai-rfc`` verb at all — the
    MCP tool stays, and ``ai-rfc status`` keeps its existing meaning as the
    operator's ledger — and ``ai_rfc/server/cli.py``, which had one, is gone. A
    tool-versus-CLI twin here would survive neither ruling, so the two arms
    meet at the core function both frontends would have shared, and the
    re-point of the other nineteen twins left this one alone.
    """
    tool_arm, core_arm, use = _twins(make_workspace)
    use(tool_arm)
    from_tool = tools.ai_rfc_status()
    use(core_arm)
    from_core = queries.status(resolve_context())
    assert from_tool == from_core
    # Equality alone would hold over two empty reads; the fixture's own
    # cluster count is what proves each arm reached the artifacts.
    assert from_tool["clusters_total"] == 2


def test_corpus_query_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    sql = "SELECT sha, subject FROM commits ORDER BY sha"
    use(tool_arm)
    from_tool = tools.ai_rfc_corpus_query(sql)
    use(cli_arm)
    assert cli.main(["corpus", "query", sql]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli
    # Two empty result sets compare equal; the corpus holds four commits.
    assert len(from_tool) == 4


def test_cluster_next_parity(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    from_tool = tools.ai_rfc_cluster_next()
    use(cli_arm)
    assert cli.main(["cluster", "next"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli
    # The verb returns None when nothing is outstanding, and ``null`` decodes
    # to the same None: without this the twin would pass on two empty reads.
    assert from_tool is not None and from_tool["id"]


def test_cluster_get_parity(make_workspace, capsys):
    """Both arms slice one patch, at bounds neither of them defaults to.

    ``--patch-offset`` and ``--patch-limit`` default to 0 and 20000, and the
    fixture's whole diff fits inside that window: a twin left on the defaults
    would agree whether or not either flag ever reached the core.
    """
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    cluster_id = tools.ai_rfc_cluster_next()["id"]
    from_tool = tools.ai_rfc_cluster_get(
        cluster_id, include_patch=True, patch_offset=10, patch_limit=40
    )
    use(cli_arm)
    assert (
        cli.main(
            [
                "cluster",
                "get",
                cluster_id,
                "--patch",
                "--patch-offset",
                "10",
                "--patch-limit",
                "40",
            ]
        )
        == 0
    )
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli
    # An interior slice, so both bounds were honoured rather than ignored.
    assert len(from_tool["patch"]) == 40
    assert from_tool["patch_total_bytes"] > 50


_QUESTION = "Which profiles does thing one hold in?"


def test_question_draft_parity(make_workspace, capsys, monkeypatch):
    tool_arm, cli_arm, use = _twins(make_workspace)
    _pin_the_register_clock(monkeypatch)
    use(tool_arm)
    from_tool = tools.ai_rfc_question_draft(_QUESTION, ["t:1.1"])
    use(cli_arm)
    assert cli.main(["question", "draft", _QUESTION, "--claim", "t:1.1"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli
    # Linking the claim is the manifest half of this verb; an entry that was
    # written to the register and linked to nothing returns [] on both arms.
    assert from_tool["linked"] == ["t:1.1"]
    for name in ("questions.yaml", "manifest.yaml"):
        assert (tool_arm / name).read_bytes() == (cli_arm / name).read_bytes()
    # The clock really was frozen: unpinned, the register would carry the day
    # the suite happened to run on.
    assert str(_PINNED_DAY) in (tool_arm / "questions.yaml").read_text()


def test_question_export_parity(make_workspace, capsys, monkeypatch):
    """A string verb's stdout is its return value, byte for byte.

    ``test_draft_render_parity`` states that contract and its comment names
    this exact failure mode: a bare ``print()`` adds a trailing newline the
    tool arm never produces, which is why ``draft render``'s branch passes
    ``end=""`` (``ai_rfc/draft/cli.py:118``). ``question export`` is the only
    other string-returning verb and was the one that never got it: at
    ``bd03cb7`` this twin was RED, because the CLI's branch was a bare
    ``print()``. ``3cdeb29`` gave that site the same ``end=""``, and the lift
    into ``agent/question/cli.py:83`` carried it across.

    The assertion is unchanged across that fix, and deliberately so. It was
    written as equality while equality was false, because a twin bent to fit a
    divergence destroys the only evidence the divergence exists — which is what
    this file is for. It is a pin now, over a verb that has since moved houses
    twice without the contract moving with it.
    """
    tool_arm, cli_arm, use = _twins(make_workspace)
    _pin_the_register_clock(monkeypatch)
    for root in (tool_arm, cli_arm):
        use(root)
        tools.ai_rfc_question_draft(_QUESTION, ["t:1.1"])
    use(tool_arm)
    from_tool = tools.ai_rfc_question_export()
    use(cli_arm)
    assert cli.main(["question", "export"]) == 0
    from_cli = capsys.readouterr().out
    # An empty register renders "No open questions.\n" on both sides, which
    # would agree without either arm having rendered a question at all.
    assert _QUESTION in from_tool
    assert from_cli == from_tool


def test_answer_record_parity(make_workspace, capsys, monkeypatch):
    tool_arm, cli_arm, use = _twins(make_workspace)
    _pin_the_register_clock(monkeypatch)
    quote = "it holds in every profile"
    for root in (tool_arm, cli_arm):
        use(root)
        tools.ai_rfc_question_draft(_QUESTION, ["t:1.1"])
        (root / "interviews" / "int-001.md").write_text(
            f"Q: which profiles?\nA: {quote}, without exception.\n"
        )
    use(tool_arm)
    from_tool = tools.ai_rfc_answer_record(
        "q-001",
        "Every profile.",
        "the author",
        "int-001.md",
        quote,
        author_confirmed_exact_text=True,
    )
    use(cli_arm)
    assert (
        cli.main(
            [
                "answer",
                "record",
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
        )
        == 0
    )
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli
    # The interview anchor and the sign-off are the two manifest writes this
    # verb exists for, and the sign-off is the half the flag decides: both are
    # empty lists on a refusal, which would compare equal across the arms.
    assert from_tool["anchored"] == ["t:1.1"]
    assert from_tool["signed_off"] == ["t:1.1"]
    for name in ("questions.yaml", "manifest.yaml"):
        assert (tool_arm / name).read_bytes() == (cli_arm / name).read_bytes()


def _overstate(root: Path) -> None:
    """Record one claim above what its evidence supports, earning a violation.

    Both arms are rewritten through the same emitter from byte-identical
    manifests, so the twins are still byte-identical going into the gate.

    Args:
        root: The workspace root whose manifest is overstated.
    """
    manifest = root / "manifest.yaml"
    document = yaml.safe_load(manifest.read_text())
    document["requirements"]["t:1.1"]["status"] = "confirmed"
    manifest.write_text(yaml.safe_dump(document, sort_keys=True))


def test_gate_parity(make_workspace, capsys):
    """Clean, then strict over one overstated claim.

    A gate twin that only ever sees 0 pins no exit code at all, and 3 is the
    number the parity table's exit-code contract turns on.
    """
    tool_arm, cli_arm, use = _twins(make_workspace)
    report = Path("out") / "report.json"
    use(tool_arm)
    from_tool = tools.ai_rfc_gate()
    use(cli_arm)
    assert cli.main(["gate"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli and from_tool["exit_code"] == 0
    assert (tool_arm / report).read_bytes() == (cli_arm / report).read_bytes()

    for root in (tool_arm, cli_arm):
        _overstate(root)
    use(tool_arm)
    strict_tool = tools.ai_rfc_gate(strict=True)
    use(cli_arm)
    assert cli.main(["gate", "--strict"]) == 3
    strict_cli = json.loads(capsys.readouterr().out)
    assert strict_tool == strict_cli and strict_tool["exit_code"] == 3
    # Quoted verbatim so a real violation is told from an empty one, which
    # makes this the one assertion here that a reword in ai_rfc.report will
    # break. Such a failure is not a parity break: both arms would still agree.
    # Re-pin the wording rather than reaching for the frontends.
    assert strict_tool["stderr"] == [
        "violation: t:1.1: recorded as confirmed but its evidence supports "
        "only inferred"
    ]
    assert (tool_arm / report).read_bytes() == (cli_arm / report).read_bytes()


def test_citation_gate_parity(make_workspace, capsys):
    """Clean, then strict over a revision recorded but never tagged.

    The clean half asserts the ``note: gate clean`` verdict where the findings
    really are empty. That line is also what a cluster id carrying a line break
    can forge while findings are not empty; the values driven here are
    ordinary, so what this twin pins is the honest verdict and not the forged
    one.
    """
    tool_arm, cli_arm, use = _twins(make_workspace)
    report = Path("out") / "gate-report.json"
    use(tool_arm)
    from_tool = tools.ai_rfc_citation_gate()
    use(cli_arm)
    assert cli.main(["citation-gate"]) == 0
    from_cli = json.loads(capsys.readouterr().out)
    assert from_tool == from_cli and from_tool["exit_code"] == 0
    assert from_tool["stderr"] == ["note: gate clean"] and from_tool["findings"] == []
    assert (tool_arm / report).read_bytes() == (cli_arm / report).read_bytes()

    for root in (tool_arm, cli_arm):
        use(root)
        cluster_id = tools.ai_rfc_cluster_next()["id"]
        assert tools.ai_rfc_checkpoint(cluster_id)["exit_code"] == 0
        tools.ai_rfc_revision_record("draft-test-spec-00", cluster_id, True, "first")
    use(tool_arm)
    strict_tool = tools.ai_rfc_citation_gate(strict=True)
    use(cli_arm)
    assert cli.main(["citation-gate", "--strict"]) == 3
    strict_cli = json.loads(capsys.readouterr().out)
    assert strict_tool == strict_cli and strict_tool["exit_code"] == 3
    assert strict_tool["findings"] == [
        "draft-test-spec-00: registered in revisions.yaml but absent from "
        "the draft repository"
    ]
    assert (tool_arm / report).read_bytes() == (cli_arm / report).read_bytes()


#: What the stand-in build writes where the real one writes its report.
_BUILD_RECORD = {
    "commit": "b" * 40,
    "exit_code": 0,
    "findings": ["idnits: one comment"],
    "outputs": {"draft-test-spec.txt": {"bytes": 12}},
}


def _recording_build(calls: list[tuple[Path, str]]):
    """Stand in for ``ai_rfc.draft.build.build``, recording the ref it is given.

    A real compile needs a provisioned toolchain a fixture workspace has no
    business carrying. The stand-in sits on the substrate side of the seam this
    file tests and is the same object for both arms, so what the twin compares
    is still which core each frontend reached and with what.

    Args:
        calls: Appended to with ``(draft repository, ref)`` on every call.

    Returns:
        The stand-in, ready for ``monkeypatch.setattr``.
    """

    def build(draft_repo, **kwargs):
        calls.append((draft_repo, kwargs["ref"]))
        target = kwargs["out"] / BUILD_DIR
        target.mkdir(parents=True, exist_ok=True)
        (target / BUILD_REPORT).write_text(json.dumps(_BUILD_RECORD, sort_keys=True))
        return SimpleNamespace(
            findings=tuple(_BUILD_RECORD["findings"]),
            commit=_BUILD_RECORD["commit"],
            exit_code=_BUILD_RECORD["exit_code"],
        )

    return build


def _without_the_root(lines: list[str], root: Path) -> list[str]:
    """Replace one arm's own workspace root wherever a diagnostic names it.

    Args:
        lines: The arm's ``stderr`` list.
        root: That arm's workspace root.

    Returns:
        The lines with the root substituted out, comparable across arms.
    """
    return [line.replace(str(root), "<workspace>") for line in lines]


def test_draft_build_parity(make_workspace, capsys, monkeypatch, tmp_path):
    """Both arms compile one ref and surface one code, on success and refusal.

    ``--ref`` is given a value rather than left at its ``HEAD`` default: a twin
    on the default would agree whether or not the flag reached the core, so the
    ref each arm handed the substrate is asserted as well.
    """
    tool_arm, cli_arm, use = _twins(make_workspace)
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
    resolved = object()
    monkeypatch.setattr(build_core, "resolve_toolchain", lambda declared: resolved)
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(build_core, "build", _recording_build(calls))

    use(tool_arm)
    from_tool = tools.ai_rfc_draft_build("main")
    use(cli_arm)
    assert cli.main(["draft", "build", "--ref", "main"]) == 0
    from_cli = json.loads(capsys.readouterr().out)

    assert calls == [(tool_arm / "draft", "main"), (cli_arm / "draft", "main")]
    assert from_tool["exit_code"] == from_cli["exit_code"] == 0
    for key in ("findings", "commit", "outputs"):
        assert from_tool[key] == from_cli[key]
    # The note names the arm's own report path, so the two lists cannot be
    # equal as they stand; each arm's root is substituted out and the results
    # compared. The substitution is asserted to have fired, or two arms naming
    # one wrong path would pass this unchanged.
    localised = _without_the_root(from_tool["stderr"], tool_arm)
    assert localised != from_tool["stderr"]
    assert localised == _without_the_root(from_cli["stderr"], cli_arm)
    written = Path("out") / BUILD_DIR / BUILD_REPORT
    assert (tool_arm / written).read_bytes() == (cli_arm / written).read_bytes()

    def refuse(draft_repo, **kwargs):
        raise BuildError("no such ref")

    monkeypatch.setattr(build_core, "build", refuse)
    use(tool_arm)
    refused_tool = tools.ai_rfc_draft_build("main")
    use(cli_arm)
    assert cli.main(["draft", "build", "--ref", "main"]) == 1
    refused_cli = json.loads(capsys.readouterr().out)
    # A refusal names no path, so this half is compared whole — including the
    # non-zero code, which the success half cannot pin at all.
    assert refused_tool == refused_cli
    assert refused_tool["exit_code"] == 1
    assert refused_tool["stderr"] == ["error: no such ref"]


#: Tool name -> the test function that drives its twin. Written out rather than
#: derived (D6): one twin covers two verbs and one does not carry the
#: ``_parity`` suffix, so any expression over ``dir()`` undercounts and would
#: pin the wrong number. ``ai_rfc_status``'s twin compares the tool against
#: ``queries.status`` rather than a CLI verb, because U3 gives the folded
#: ``status`` no ``ai-rfc`` verb at all (D16).
TWINS: dict[str, str] = {
    "ai_rfc_status": "test_status_parity",
    "ai_rfc_corpus_query": "test_corpus_query_parity",
    "ai_rfc_cluster_next": "test_cluster_next_parity",
    "ai_rfc_cluster_get": "test_cluster_get_parity",
    "ai_rfc_claim_upsert": "test_claim_upsert_parity",
    "ai_rfc_claim_adjudicate": "test_read_parity_adjudicate",
    "ai_rfc_claim_record_status": "test_record_status_parity",
    "ai_rfc_question_draft": "test_question_draft_parity",
    "ai_rfc_question_export": "test_question_export_parity",
    "ai_rfc_answer_record": "test_answer_record_parity",
    "ai_rfc_revision_record": "test_consolidation_checkpoint_and_revision_parity",
    "ai_rfc_checkpoint": "test_consolidation_checkpoint_and_revision_parity",
    "ai_rfc_gate": "test_gate_parity",
    "ai_rfc_citation_gate": "test_citation_gate_parity",
    "ai_rfc_draft_commit": "test_draft_commit_parity",
    "ai_rfc_revision_tag": "test_revision_tag_parity",
    "ai_rfc_draft_build": "test_draft_build_parity",
    "ai_rfc_draft_lint": "test_draft_lint_parity",
    "ai_rfc_structure_upsert": "test_structure_upsert_parity",
    "ai_rfc_draft_render": "test_draft_render_parity",
}


def test_every_tool_has_a_parity_twin():
    """Twenty tools, twenty twins, each named by a function that is really here.

    The keys are what the coverage claim is about, but a typo in a *value*
    would leave the table pointing at nothing while still passing on its keys,
    so every named twin is resolved as well.
    """
    assert set(TWINS) == {tool.__name__ for tool in tools.ALL_TOOLS}
    assert all(callable(globals().get(name)) for name in TWINS.values())
