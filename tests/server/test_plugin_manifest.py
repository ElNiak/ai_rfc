"""The plugin launches the installed server; nothing bootstraps a path."""

import json
import os
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "ai-rfc"

#: What a launcher puts in each ``${...}`` slot the manifest declares, for one
#: workspace. Keyed by variable name so the test below can fill exactly the set
#: the manifest asks for and notice when that set grows past what is known here.
SUBSTITUTIONS = {
    "AI_RFC_CONFIG": lambda workspace: str(workspace / "recon.yaml"),
    "AI_RFC_WORKSPACE": str,
}


def test_the_mcp_manifest_runs_the_installed_server():
    server = json.loads((PLUGIN / ".mcp.json").read_text())["ai_rfc"]
    assert server["command"] == "${AI_RFC_PYTHON}"
    assert server["args"] == ["-m", "ai_rfc.server"]
    assert set(server["env"]) == {"AI_RFC_CONFIG", "AI_RFC_WORKSPACE"}


def test_the_manifest_declares_every_variable_the_resolver_needs(tmp_path, monkeypatch):
    """Asserted by running the resolver, not by copying a list of names.

    The manifest is the launcher an operator's Claude Code actually uses, and
    the only thing that had ever checked it read its JSON. So this builds an
    environment holding **exactly** what the ``env`` block declares — nothing
    else — and calls ``resolve_context()`` in it. A contract move that adds a
    variable the resolver needs and nobody adds here raises ``EnvError`` in
    this test, rather than at an operator's first tool call, which is how the
    move to ``AI_RFC_CONFIG`` slipped past the suite.

    Sufficiency is the property, not minimality: ``AI_RFC_WORKSPACE`` is
    declared and deliberately unread — arm B's and arm C's prompts spell paths
    with it (R8).
    """
    from ai_rfc.server.paths import resolve_context
    from ai_rfc.server.testing import seal

    workspace = tmp_path / "ws"
    workspace.mkdir()
    seal(workspace)
    declared = json.loads((PLUGIN / ".mcp.json").read_text())["ai_rfc"]["env"]
    unknown = sorted(set(declared) - set(SUBSTITUTIONS))
    assert not unknown, f"no launcher value known for {unknown}; extend it above"

    monkeypatch.setattr(
        os, "environ", {name: SUBSTITUTIONS[name](workspace) for name in declared}
    )
    assert resolve_context().workspace == workspace.resolve()


def test_no_command_names_the_retired_door_or_variable():
    offenders = [
        path.name
        for path in (PLUGIN / "commands").glob("*.md")
        if "panther.plugins" in path.read_text() or "PANTHER_REPO" in path.read_text()
    ]
    assert offenders == []
