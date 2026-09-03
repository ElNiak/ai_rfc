"""The plugin launches the installed server; nothing bootstraps a path."""

import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "ai-rfc"


def test_the_mcp_manifest_runs_the_installed_server():
    server = json.loads((PLUGIN / ".mcp.json").read_text())["ai_rfc"]
    assert server["command"] == "${AI_RFC_PYTHON}"
    assert server["args"] == ["-m", "ai_rfc.server"]
    assert set(server["env"]) == {"AI_RFC_WORKSPACE"}


def test_no_command_names_the_retired_door_or_variable():
    offenders = [
        path.name
        for path in (PLUGIN / "commands").glob("*.md")
        if "panther.plugins" in path.read_text() or "PANTHER_REPO" in path.read_text()
    ]
    assert offenders == []
