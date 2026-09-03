"""Claim operations: schema-validated writes and adjudication previews.

Writes go through a full round-trip — mutate the document, load it with the
substrate's strict schema, dump the normalized form, atomic rename — so an
invalid manifest can never land on disk, and a landed manifest is always in
citable byte-stable form. The one thing no path here can do is set a status
above what the evidence supports.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ..paths import Context
from . import CoreError, GuardrailError

#: Claim fields a caller may set. ``status`` is deliberately absent — a
#: claim's standing is adjudicated from its evidence, never asserted.
_WRITABLE_FIELDS = frozenset(
    {
        "text",
        "section",
        "level",
        "layer",
        "req_class",
        "intent",
        "question-id",
        "testable",
        "anchors",
    }
)

_REQUIRED_FIELDS = ("text", "section", "level", "layer")


def _substrate(ctx: Context):  # noqa: ANN202 - substrate modules, resolved lazily
    """Import the substrate lazily; it resolves because the package is installed."""
    del ctx
    from ai_rfc import promotion, schema

    return schema, promotion


def _atomic_write(path: Path, text: str) -> None:
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".ai-rfc-")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _normalize_and_write(ctx: Context, document: dict[str, Any]) -> None:
    schema, _ = _substrate(ctx)
    with tempfile.TemporaryDirectory() as scratch:
        candidate = Path(scratch) / "manifest.yaml"
        candidate.write_text(yaml.safe_dump(document, sort_keys=True))
        manifest = schema.load(candidate)
    _atomic_write(ctx.manifest, schema.dump(manifest))


def _document(ctx: Context) -> dict[str, Any]:
    document = yaml.safe_load(ctx.manifest.read_text())
    if not isinstance(document, dict) or "requirements" not in document:
        raise CoreError(f"{ctx.manifest} is not a manifest document")
    return document


def upsert_claim(ctx: Context, claim_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Add or update one claim through the strict schema.

    Args:
        ctx: The resolved context.
        claim_id: The claim to write.
        fields: Field values; only ``_WRITABLE_FIELDS`` are accepted, and a
            new claim must carry all of ``text``, ``section``, ``level``,
            ``layer``.

    Returns:
        The claim body as stored (post-normalization).

    Raises:
        GuardrailError: If ``fields`` tries to set ``status`` or any
            unknown field.
        CoreError: If a new claim misses a required field.
        SchemaError: If the resulting manifest does not validate.
    """
    if "status" in fields:
        raise GuardrailError(
            "status is adjudicated from evidence, never asserted; omit it "
            "and use record_statuses after adjudication"
        )
    unknown = set(fields) - _WRITABLE_FIELDS
    if unknown:
        raise GuardrailError(
            f"unknown claim fields {sorted(unknown)}; writable fields are "
            f"{sorted(_WRITABLE_FIELDS)}"
        )

    document = _document(ctx)
    requirements = document["requirements"]
    existing = requirements.get(claim_id)
    body = dict(existing) if isinstance(existing, dict) else {}
    body.update(fields)
    for required in _REQUIRED_FIELDS:
        if required not in body:
            raise CoreError(f"{claim_id}: missing required field {required}")
    requirements[claim_id] = body
    _normalize_and_write(ctx, document)

    stored = yaml.safe_load(ctx.manifest.read_text())["requirements"][claim_id]
    return stored


def adjudicate_preview(ctx: Context) -> list[dict[str, Any]]:
    """Report every claim's stored status beside what its evidence supports.

    Args:
        ctx: The resolved context.

    Returns:
        One entry per claim: ``{id, stored, supported, promotable}``.
    """
    schema, promotion = _substrate(ctx)
    from ai_rfc.models import STATUS_RANK

    manifest = schema.load(ctx.manifest)
    entries = []
    for claim in manifest.claims:
        supported = promotion.adjudicate(claim)
        entries.append(
            {
                "id": claim.id,
                "stored": claim.status.value,
                "supported": supported.value,
                "promotable": STATUS_RANK[supported] > STATUS_RANK[claim.status],
            }
        )
    return entries


def record_statuses(
    ctx: Context, claim_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Set claims' stored statuses to exactly what adjudication supports.

    Args:
        ctx: The resolved context.
        claim_ids: Claims to update; ``None`` updates every claim whose
            stored status differs from its supported one.

    Returns:
        The entries that changed, in ``adjudicate_preview`` shape.

    Raises:
        CoreError: If a named claim does not exist.
    """
    preview = {entry["id"]: entry for entry in adjudicate_preview(ctx)}
    targets = claim_ids if claim_ids is not None else list(preview)
    missing = [claim_id for claim_id in targets if claim_id not in preview]
    if missing:
        raise CoreError(f"no such claim(s): {', '.join(sorted(missing))}")

    document = _document(ctx)
    changed = []
    for claim_id in targets:
        entry = preview[claim_id]
        if entry["stored"] == entry["supported"]:
            continue
        document["requirements"][claim_id]["status"] = entry["supported"]
        changed.append(entry)
    if changed:
        _normalize_and_write(ctx, document)
    return changed
