import dataclasses

import pytest

from ai_rfc.models import (
    STATUS_RANK,
    Anchor,
    EvidenceClass,
    Intent,
    Level,
    Manifest,
    RequirementClaim,
    RequirementClass,
    Status,
)

pytestmark = pytest.mark.unit


def _claim(**overrides):
    base = dict(
        id="spec:1.1",
        text="The system responds within the configured interval.",
        section="1.1",
        level=Level.MUST,
        layer="timing",
        req_class=RequirementClass.PROTOCOL_BEHAVIORAL,
        intent=Intent.INTENDED,
    )
    base.update(overrides)
    return RequirementClaim(**base)


def test_status_default_is_the_most_restrictive():
    assert _claim().status is Status.GAP
    assert STATUS_RANK[Status.GAP] < STATUS_RANK[Status.INFERRED]
    assert STATUS_RANK[Status.INFERRED] < STATUS_RANK[Status.CONFIRMED]


def test_records_are_immutable():
    claim = _claim()
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.status = Status.CONFIRMED


def test_evidence_classes_deduplicates_anchors():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.CODE, "src/a.py", commit="a" * 40),
            Anchor(EvidenceClass.CODE, "src/b.py", commit="a" * 40),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        )
    )
    assert claim.evidence_classes == frozenset(
        {EvidenceClass.CODE, EvidenceClass.PAPER}
    )


def test_externally_checked_requires_runtime_or_signoff():
    assert not _claim(
        anchors=(Anchor(EvidenceClass.CODE, "src/a.py", commit="a" * 40),)
    ).is_externally_checked
    assert _claim(
        anchors=(Anchor(EvidenceClass.RUNTIME, "run/42"),)
    ).is_externally_checked
    assert _claim(signed_off_by="dev-01").is_externally_checked


def test_empty_manifest_reports_zeros_rather_than_raising():
    manifest = Manifest(rfc="SPEC-1", title="Untitled", claims=())
    assert manifest.count_by_status == {"gap": 0, "inferred": 0, "confirmed": 0}
    assert manifest.checked_fraction_by_req_class["protocol-behavioral"] == 0.0


def test_checked_fraction_counts_only_confirmed_claims():
    manifest = Manifest(
        rfc="SPEC-1",
        title="Untitled",
        claims=(
            _claim(id="a", status=Status.CONFIRMED, signed_off_by="dev-01"),
            _claim(id="b", status=Status.CONFIRMED),
            _claim(id="c", status=Status.INFERRED),
        ),
    )
    assert manifest.checked_fraction_by_req_class["protocol-behavioral"] == 0.5
    assert manifest.count_by_status == {"gap": 0, "inferred": 1, "confirmed": 2}


def test_every_req_class_appears_even_when_unused():
    manifest = Manifest(rfc="SPEC-1", title="Untitled", claims=())
    assert set(manifest.checked_fraction_by_req_class) == {
        "protocol-behavioral",
        "data-model",
        "algorithmic",
    }


def test_a_structure_lists_the_claims_it_binds_in_declaration_order():
    from ai_rfc.models import Field, Structure, StructureKind, Transition, Value

    wire = Structure(
        id="hdr",
        kind=StructureKind.WIRE_FORMAT,
        title="Header",
        section="4.1",
        fields=(
            Field(name="version", claim="spec:4.1", width=8),
            Field(name="length", claim="spec:4.2", width="variable"),
        ),
    )
    assert wire.claims == ("spec:4.1", "spec:4.2")

    machine = Structure(
        id="conn",
        kind=StructureKind.STATE_MACHINE,
        title="Connection",
        section="5",
        states=("idle", "open"),
        transitions=(
            Transition(source="idle", event="connect", target="open", claim="spec:5.1"),
        ),
    )
    assert machine.claims == ("spec:5.1",)

    codes = Structure(
        id="codes",
        kind=StructureKind.ENUM,
        title="Error codes",
        section="6",
        values=(Value(name="NO_ERROR", value="0", claim="spec:6.1"),),
    )
    assert codes.claims == ("spec:6.1",)
