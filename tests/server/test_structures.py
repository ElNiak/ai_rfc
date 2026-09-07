"""The structure_upsert and draft_render cores."""

from __future__ import annotations

import pytest

from ai_rfc.server import tools
from ai_rfc.server.core import CoreError, structures

pytestmark = pytest.mark.unit

FIELDS = {
    "kind": "record",
    "title": "Message header",
    "section": "4",
    "fields": [{"name": "version", "type": "uint8", "claim": "t:1.1"}],
}


def test_a_structure_survives_the_manifest_round_trip(workspace):
    structures.upsert_structure(workspace, "header", FIELDS)
    text = workspace.manifest.read_text()
    assert "structures:" in text
    # The write path round-trips through schema.load/dump; a key the schema did
    # not know would be silently dropped here.
    structures.upsert_structure(workspace, "header", FIELDS)
    assert "header" in workspace.manifest.read_text()


def test_a_structure_binding_an_unknown_claim_is_refused(workspace):
    with pytest.raises(CoreError):
        structures.upsert_structure(
            workspace, "ghost", {**FIELDS, "fields": [{"name": "a", "claim": "t:9.9"}]}
        )


def test_render_returns_the_blocks_as_text(workspace):
    structures.upsert_structure(workspace, "header", FIELDS)
    text = structures.render_structures(workspace)
    assert text.startswith("{::comment}")
    assert "ai_rfc:struct:header begin" in text


def test_the_tool_wrappers_reach_the_cores(workspace):
    tools.ai_rfc_structure_upsert("header", FIELDS)
    assert "ai_rfc:struct:header begin" in tools.ai_rfc_draft_render()
