"""The structure_upsert and draft_render cores."""

from __future__ import annotations

import pytest

from ai_rfc.schema import load
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
    assert "structures:" in workspace.manifest.read_text()
    # The write path round-trips through schema.load/dump. Only a second write
    # targeting a different id proves the first structure's members survived it:
    # rewriting the same body would restore whatever the round trip had dropped.
    structures.upsert_structure(workspace, "trailer", {**FIELDS, "title": "Trailer"})
    stored = {s.id: s for s in load(workspace.manifest).structures}
    assert set(stored) == {"header", "trailer"}
    field = stored["header"].fields[0]
    assert (field.name, field.type, field.claim) == ("version", "uint8", "t:1.1")


def test_a_structure_binding_an_unknown_claim_is_refused(workspace):
    with pytest.raises(CoreError):
        structures.upsert_structure(
            workspace, "ghost", {**FIELDS, "fields": [{"name": "a", "claim": "t:9.9"}]}
        )


def test_a_body_that_is_not_a_mapping_is_refused(workspace):
    with pytest.raises(CoreError):
        structures.upsert_structure(workspace, "header", 5)


def test_a_present_but_null_structures_key_accepts_an_upsert(workspace):
    # schema.load reads `document.get("structures") or {}`, so a hand-authored
    # manifest may carry the key with no value. The writer must accept the same
    # document the loader does; arm C hand-edits this file by design.
    workspace.manifest.write_text(workspace.manifest.read_text() + "structures:\n")
    structures.upsert_structure(workspace, "header", FIELDS)
    assert "ai_rfc:struct:header begin" in structures.render_structures(workspace)


def test_render_returns_the_blocks_as_text(workspace):
    structures.upsert_structure(workspace, "header", FIELDS)
    text = structures.render_structures(workspace)
    assert text.startswith("{::comment}")
    assert "ai_rfc:struct:header begin" in text


def test_the_tool_wrappers_reach_the_cores(workspace):
    tools.ai_rfc_structure_upsert("header", FIELDS)
    assert "ai_rfc:struct:header begin" in tools.ai_rfc_draft_render()


def test_a_manifest_the_schema_refuses_reaches_the_caller_as_a_core_error(workspace):
    # upsert_structure already wraps the schema's refusal; a caller cannot be
    # asked to catch a second exception class for the same manifest.
    workspace.manifest.write_text(
        workspace.manifest.read_text().replace("level: MUST", "level: MAYBE")
    )
    with pytest.raises(CoreError) as error:
        structures.render_structures(workspace)
    assert "permitted values are" in str(error.value)
