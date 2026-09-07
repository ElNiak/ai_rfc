"""Declare and render manifest structures."""

from __future__ import annotations

from typing import Any

from ai_rfc.draft.structures import render_all
from ai_rfc.schema import SchemaError, load

from ..paths import Context
from . import CoreError
from .claims import _document, _normalize_and_write

_REQUIRED = ("kind", "title", "section")


def upsert_structure(
    ctx: Context, structure_id: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Create or replace one structure in the manifest.

    Args:
        ctx: The resolved workspace.
        structure_id: The structure's id.
        fields: Its body — ``kind``, ``title``, ``section`` and the members its
            kind requires.

    Returns:
        The stored structure body.

    Raises:
        CoreError: If a required field is missing, or the schema refuses the
            result — which is where an unknown bound claim is caught.
    """
    missing = [key for key in _REQUIRED if key not in fields]
    if missing:
        raise CoreError(
            f"{structure_id}: missing required field(s) {', '.join(missing)}"
        )
    document = _document(ctx)
    document.setdefault("structures", {})[structure_id] = dict(fields)
    try:
        _normalize_and_write(ctx, document)
    except SchemaError as error:
        raise CoreError(str(error)) from error
    # Return what was actually stored, re-read after the round trip, exactly as
    # upsert_claim does — the write normalises, so the input is not the record.
    return dict(_document(ctx)["structures"][structure_id])


def render_structures(ctx: Context) -> str:
    """Render every declared structure as delimited kramdown blocks.

    Args:
        ctx: The resolved workspace.

    Returns:
        The blocks, ready to paste into the draft. Empty when none are declared.
    """
    return render_all(load(ctx.manifest))
