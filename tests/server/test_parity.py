"""Tool-arm vs CLI-arm parity: same operation, byte-identical results.

The twin workspaces are built with pinned commit dates, so before any
operation their files are byte-identical; after one operation through each
frontend they must still be.
"""

import json
from pathlib import Path

from ai_rfc.server import cli, tools
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
                "claim-upsert",
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
    assert cli.main(["claim-record-status"]) == 0
    capsys.readouterr()
    assert (tool_arm / "manifest.yaml").read_bytes() == (
        cli_arm / "manifest.yaml"
    ).read_bytes()


def test_read_parity_adjudicate(make_workspace, capsys):
    tool_arm, cli_arm, use = _twins(make_workspace)
    use(tool_arm)
    from_tool = tools.ai_rfc_claim_adjudicate()
    use(cli_arm)
    assert cli.main(["claim-adjudicate"]) == 0
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
    assert cli.main(["draft-commit", "-m", "more prose"]) == 0
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
    assert cli.main(["revision-tag", "draft-test-spec-00", "-m", "revision 00"]) == 0
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
    assert cli.main(["draft-lint"]) == 0
    via_cli = json.loads(capsys.readouterr().out)
    assert (
        via_tool["metrics"] == via_cli["metrics"]
        and via_tool["findings"] == via_cli["findings"]
    )
