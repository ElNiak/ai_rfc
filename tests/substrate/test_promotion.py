import pytest

from ai_rfc.models import (
    Anchor,
    EvidenceClass,
    Intent,
    Level,
    Manifest,
    RequirementClaim,
    RequirementClass,
    Status,
)
from ai_rfc.promotion import adjudicate, violations

from .draft.conftest import _manifest_text

pytestmark = pytest.mark.unit

SHA = "00112233445566778899aabbccddeeff00112233"


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


def test_claim_without_anchors_cannot_exceed_gap():
    assert adjudicate(_claim()) is Status.GAP
    assert adjudicate(_claim(status=Status.CONFIRMED)) is Status.GAP


def test_signoff_without_any_anchor_is_still_a_gap():
    assert adjudicate(_claim(signed_off_by="dev-01")) is Status.GAP


def test_adr_only_claim_is_capped_at_inferred():
    claim = _claim(anchors=(Anchor(EvidenceClass.ADR, "adr/0007.md"),))
    assert adjudicate(claim) is Status.INFERRED


def test_paper_only_claim_is_capped_at_inferred():
    claim = _claim(anchors=(Anchor(EvidenceClass.PAPER, "10.1000/xyz"),))
    assert adjudicate(claim) is Status.INFERRED


def test_adr_and_paper_together_are_still_capped_at_inferred():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.ADR, "adr/0007.md"),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        )
    )
    assert adjudicate(claim) is Status.INFERRED


def test_single_code_anchor_alone_is_inferred():
    claim = _claim(anchors=(Anchor(EvidenceClass.CODE, "src/timer.py", commit=SHA),))
    assert adjudicate(claim) is Status.INFERRED


def test_runtime_anchor_reaches_confirmed():
    claim = _claim(anchors=(Anchor(EvidenceClass.RUNTIME, "run/42"),))
    assert adjudicate(claim) is Status.CONFIRMED


def test_signoff_over_narrative_evidence_is_capped_at_inferred():
    """A sign-off is one person's assertion; so is an ADR.

    The two-class route already refuses two narrative sources because they may
    be one person counted twice. A sign-off over narrative-only evidence is that
    same person counted twice, so it is capped the same way.
    """
    claim = _claim(
        anchors=(Anchor(EvidenceClass.ADR, "adr/0007.md"),),
        signed_off_by="dev-01",
    )
    assert adjudicate(claim) is Status.INFERRED


def test_signoff_beside_a_primary_anchor_reaches_confirmed():
    """One code anchor alone is inferred; a developer vouching for it is not."""
    claim = _claim(
        anchors=(Anchor(EvidenceClass.CODE, "src/timer.py", commit=SHA),),
        signed_off_by="dev-01",
    )
    assert adjudicate(claim) is Status.CONFIRMED


def test_two_distinct_evidence_classes_reach_confirmed():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.CODE, "src/timer.py", commit=SHA),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        )
    )
    assert adjudicate(claim) is Status.CONFIRMED


def test_two_narrative_classes_do_not_reach_confirmed():
    """An interview and a paper may be one person's account counted twice."""
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.INTERVIEW, "interview-03"),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        )
    )
    assert adjudicate(claim) is Status.INFERRED


def test_interview_plus_adr_does_not_reach_confirmed():
    """Two classes, but neither is evidence the system itself produced."""
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.INTERVIEW, "interview-03"),
            Anchor(EvidenceClass.ADR, "adr/0007.md"),
        )
    )
    assert adjudicate(claim) is Status.INFERRED


def test_interview_plus_code_reaches_confirmed():
    """A narrative source corroborated by a primary artefact does promote."""
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.INTERVIEW, "interview-03"),
            Anchor(EvidenceClass.CODE, "src/timer.py", commit=SHA),
        )
    )
    assert adjudicate(claim) is Status.CONFIRMED


def test_two_anchors_of_the_same_class_do_not_reach_confirmed():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.CODE, "src/a.py", commit=SHA),
            Anchor(EvidenceClass.CODE, "src/b.py", commit=SHA),
        )
    )
    assert adjudicate(claim) is Status.INFERRED


def test_overstated_status_is_reported_as_a_violation():
    manifest = Manifest(
        rfc="SPEC-1",
        title="x",
        claims=(
            _claim(
                id="spec:1.1",
                status=Status.CONFIRMED,
                anchors=(Anchor(EvidenceClass.ADR, "adr/0007.md"),),
            ),
        ),
    )
    found = violations(manifest)
    assert len(found) == 1
    assert found[0].claim_id == "spec:1.1"
    assert found[0].stored is Status.CONFIRMED
    assert found[0].supported is Status.INFERRED


def test_understated_status_is_not_a_violation():
    manifest = Manifest(
        rfc="SPEC-1",
        title="x",
        claims=(
            _claim(
                status=Status.INFERRED,
                anchors=(Anchor(EvidenceClass.RUNTIME, "run/42"),),
            ),
        ),
    )
    assert violations(manifest) == ()


def test_a_clean_manifest_reports_no_violations():
    manifest = Manifest(
        rfc="SPEC-1",
        title="x",
        claims=(
            _claim(
                id="spec:1.1",
                status=Status.CONFIRMED,
                anchors=(Anchor(EvidenceClass.RUNTIME, "run/42"),),
            ),
            _claim(
                id="spec:2.1",
                status=Status.INFERRED,
                anchors=(Anchor(EvidenceClass.PAPER, "10.1000/xyz"),),
            ),
        ),
    )
    assert violations(manifest) == ()


def _structure_manifest_text(*claim_ids: str) -> str:
    """Manifest text whose one structure binds exactly ``claim_ids``.

    The two claims differ on both axes, so which of them a structure binds
    changes its score: ``spec:1.1`` is stored ``inferred`` behind a code anchor
    and adjudicates to ``inferred``, while ``spec:2.1`` carries no anchor and is
    a ``gap`` on both. Without that difference every candidate scoring rule
    agrees and the assertions below prove nothing.
    """
    fields = "".join(
        f"      - name: f{index}\n        claim: {claim_id}\n"
        for index, claim_id in enumerate(claim_ids)
    )
    return (
        _manifest_text(with_second_claim=True).replace(
            "    level: MUST\n", "    level: MUST\n    status: inferred\n"
        )
        + "structures:\n"
        "  header:\n"
        "    kind: record\n"
        "    title: Message header\n"
        "    section: '4'\n"
        "    fields:\n" + fields
    )


def _assert_fixture_discriminates(manifest) -> None:
    """Fail loudly if the two fixture claims stop differing.

    Every assertion in this section reads as passing when both claims share a
    status, so the fixture's own premise is checked rather than assumed.
    """
    by_id = {claim.id: claim for claim in manifest.claims}
    assert by_id["spec:1.1"].status is Status.INFERRED
    assert adjudicate(by_id["spec:1.1"]) is Status.INFERRED
    assert by_id["spec:2.1"].status is Status.GAP
    assert adjudicate(by_id["spec:2.1"]) is Status.GAP


def test_a_structure_scores_only_the_claims_it_binds(tmp_path):
    """``spec:2.1`` is a gap the structure does not bind, so it must not count.

    A rule that ranged over ``manifest.claims`` instead of ``structure.claims``
    would answer ``gap`` on both axes here.
    """
    from ai_rfc.promotion import structure_statuses
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_structure_manifest_text("spec:1.1"))
    manifest = load(path)
    _assert_fixture_discriminates(manifest)

    stored, supported = structure_statuses(manifest)["header"]

    assert stored is Status.INFERRED
    assert supported is Status.INFERRED
    assert (stored, supported) != (Status.GAP, Status.GAP)


def test_a_structure_is_only_as_strong_as_its_weakest_claim(tmp_path):
    """Binding the gap alongside the inferred claim drags the structure down.

    The counterpart of the test above: a rule taking the *strongest* bound
    claim would answer ``inferred`` on both axes here.
    """
    from ai_rfc.promotion import structure_statuses
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_structure_manifest_text("spec:1.1", "spec:2.1"))
    manifest = load(path)
    _assert_fixture_discriminates(manifest)

    stored, supported = structure_statuses(manifest)["header"]

    assert stored is Status.GAP
    assert supported is Status.GAP


def test_a_manifest_without_structures_has_no_structure_statuses(tmp_path):
    from ai_rfc.promotion import structure_statuses
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=False))
    assert structure_statuses(load(path)) == {}
