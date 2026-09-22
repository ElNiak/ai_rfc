import json
from pathlib import Path

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
from ai_rfc.report import build, to_json, to_markdown, to_yaml

from .draft.conftest import _manifest_text

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


@pytest.fixture
def mixed_manifest():
    return Manifest(
        rfc="SPEC-1",
        title="An Example Specification",
        claims=(
            _claim(
                id="spec:1.1",
                status=Status.CONFIRMED,
                anchors=(Anchor(EvidenceClass.RUNTIME, "run/42"),),
            ),
            _claim(
                id="spec:2.1",
                text="The system tolerates a duplicate identifier.",
                intent=Intent.ACCIDENTAL,
                status=Status.INFERRED,
                anchors=(Anchor(EvidenceClass.PAPER, "10.1000/xyz"),),
            ),
            _claim(
                id="spec:3.1",
                text="The system rejects an oversized frame.",
                status=Status.CONFIRMED,
                anchors=(Anchor(EvidenceClass.ADR, "adr/0007.md"),),
            ),
        ),
    )


def test_report_counts_by_status(mixed_manifest):
    report = build(mixed_manifest)
    assert report.manifest.count_by_status == {
        "gap": 0,
        "inferred": 1,
        "confirmed": 2,
    }


def test_report_finds_the_overstated_claim(mixed_manifest):
    report = build(mixed_manifest)
    assert [violation.claim_id for violation in report.violations] == ["spec:3.1"]


def test_json_carries_derived_metrics(mixed_manifest):
    payload = json.loads(to_json(build(mixed_manifest)))
    assert payload["count_by_status"]["confirmed"] == 2
    assert "checked_fraction_by_req_class" in payload
    assert payload["checked_fraction_by_req_class"]["protocol-behavioral"] == 0.5


def test_json_is_byte_stable(mixed_manifest):
    report = build(mixed_manifest)
    assert to_json(report) == to_json(report)


def test_markdown_excludes_accidental_claims_from_the_normative_section(
    mixed_manifest,
):
    markdown = to_markdown(build(mixed_manifest))
    normative, _, descriptive = markdown.partition("## Descriptive")
    assert "spec:1.1" in normative
    assert "spec:2.1" not in normative
    assert "spec:2.1" in descriptive
    assert "(MUST," in normative
    assert "Level." not in normative


def test_markdown_names_every_violation(mixed_manifest):
    markdown = to_markdown(build(mixed_manifest))
    assert "spec:3.1" in markdown
    assert "supports only inferred" in markdown


def test_yaml_round_trips_as_a_mapping(mixed_manifest):
    import yaml

    payload = yaml.safe_load(to_yaml(build(mixed_manifest)))
    assert payload["rfc"] == "SPEC-1"
    assert payload["count_by_status"]["confirmed"] == 2


def test_unverified_anchors_are_listed_when_a_repo_is_given(
    mixed_manifest, fixture_repo: Path
):
    manifest = Manifest(
        rfc="SPEC-1",
        title="x",
        claims=(
            _claim(
                id="spec:9.1",
                anchors=(
                    Anchor(
                        EvidenceClass.CODE,
                        "does_not_exist.txt",
                        commit=(fixture_repo / "FIRST_SHA").read_text().strip(),
                    ),
                ),
            ),
        ),
    )
    report = build(manifest, repo=fixture_repo)
    assert len(report.unverified) == 1
    assert report.unverified[0].startswith("spec:9.1: does_not_exist.txt (")
    assert "does not exist at" in report.unverified[0]


def test_no_repo_means_no_anchor_verification_attempted(mixed_manifest):
    assert build(mixed_manifest).unverified == ()


def test_payload_reports_supported_status_beside_stored():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.CODE, "a.py", commit="0" * 40),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        ),
        status=Status.GAP,
    )
    payload = json.loads(to_json(build(Manifest(rfc="S", title="t", claims=(claim,)))))
    assert payload["claims"] == [
        {
            "id": "spec:1.1",
            "stored": "gap",
            "supported": "confirmed",
            "promotable": True,
        }
    ]
    assert payload["promotable_count"] == 1


def test_stored_at_supported_level_is_not_promotable(mixed_manifest):
    payload = json.loads(to_json(build(mixed_manifest)))
    by_id = {entry["id"]: entry for entry in payload["claims"]}
    assert by_id["spec:1.1"]["promotable"] is False
    assert by_id["spec:3.1"]["supported"] == "inferred"
    assert by_id["spec:3.1"]["promotable"] is False
    assert payload["promotable_count"] == 0


def test_markdown_lists_promotable_claims():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.CODE, "a.py", commit="0" * 40),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        ),
        status=Status.GAP,
    )
    markdown = to_markdown(build(Manifest(rfc="S", title="t", claims=(claim,))))
    assert "## Promotable" in markdown
    promotable_section = markdown.split("## Promotable")[1].split("##")[0]
    assert "spec:1.1" in promotable_section
    assert "confirmed" in promotable_section


def test_markdown_carries_the_externally_checked_fraction(mixed_manifest):
    markdown = to_markdown(build(mixed_manifest))
    assert "## Externally checked fraction" in markdown
    section = markdown.split("## Externally checked fraction")[1].split("##")[0]
    assert "protocol-behavioral" in section
    assert "0.50" in section


def test_markdown_distinguishes_an_unchecked_class_from_an_empty_one(mixed_manifest):
    """A 0.0 fraction and "no confirmed claims here" must not read alike.

    ``checked_fraction_by_req_class`` reports 0.0 for both, which is the one
    ambiguity that makes the metric misreadable on its own.
    """
    markdown = to_markdown(build(mixed_manifest))
    section = markdown.split("## Externally checked fraction")[1].split("##")[0]
    lines = {
        line.lstrip("- ").split(":")[0].strip(): line
        for line in section.splitlines()
        if line.startswith("- ")
    }
    assert "2 confirmed" in lines["protocol-behavioral"]
    assert "no confirmed claims" in lines["algorithmic"]
    assert "0.0" not in lines["algorithmic"]


def test_report_records_whether_anchors_were_checked(mixed_manifest, fixture_repo):
    assert build(mixed_manifest).anchors_checked is False
    assert build(mixed_manifest, repo=fixture_repo).anchors_checked is True


def test_report_counts_the_anchors_a_repo_would_have_verified():
    claim = _claim(
        anchors=(
            Anchor(EvidenceClass.CODE, "a.py", commit="0" * 40),
            Anchor(EvidenceClass.PAPER, "10.1000/xyz"),
        ),
    )
    report = build(Manifest(rfc="S", title="t", claims=(claim,)))
    assert report.verifiable_anchor_count == 1


def test_markdown_says_not_checked_rather_than_none_failed(mixed_manifest):
    """Without a repo the section must not read as a clean bill of health."""
    section = to_markdown(build(mixed_manifest)).split("## Unverified anchors")[1]
    assert "Not checked" in section
    assert "none failed" not in section


def test_markdown_says_none_failed_when_a_repo_verified_every_anchor(fixture_repo):
    manifest = Manifest(
        rfc="SPEC-1",
        title="x",
        claims=(_claim(id="spec:9.1", anchors=(Anchor(EvidenceClass.ADR, "a.md"),)),),
    )
    section = to_markdown(build(manifest, repo=fixture_repo)).split(
        "## Unverified anchors"
    )[1]
    assert "None failed" in section
    assert "Not checked" not in section


def _structure_manifest_text() -> str:
    """Manifest text whose one structure binds ``spec:1.1`` and not ``spec:2.1``.

    Two asymmetries make the assertions below discriminating. The bound
    ``spec:1.1`` is a stored ``gap`` that its code anchor promotes to
    ``inferred``, so the two axes differ and a renderer that swapped them is
    caught. The unbound ``spec:2.1`` has no anchor and is a ``gap`` on both, so
    a renderer scoring the whole manifest rather than the structure's own
    claims prints ``supported gap`` and is caught too.
    """
    return (
        _manifest_text(with_second_claim=True) + "structures:\n"
        "  header:\n"
        "    kind: record\n"
        "    title: Message header\n"
        "    section: '4'\n"
        "    fields:\n"
        "      - name: a\n"
        "        claim: spec:1.1\n"
    )


def test_the_markdown_report_names_every_structure_and_its_status(tmp_path):
    from ai_rfc.report import build, to_markdown
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_structure_manifest_text())
    text = to_markdown(build(load(path)))

    assert "## Structures" in text
    section = text.split("## Structures")[1].split("##")[0]
    assert section.strip() == (
        "- `header` (record) Message header §4: "
        "stored gap, supported inferred, 1 claims"
    )


def test_the_payload_carries_each_structures_status(tmp_path):
    """JSON and YAML must carry the same pairs the Markdown section prints."""
    import yaml

    from ai_rfc.report import build, to_json, to_yaml
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_structure_manifest_text())
    report = build(load(path))
    expected = {"header": {"stored": "gap", "supported": "inferred"}}

    assert json.loads(to_json(report))["structures"] == expected
    assert yaml.safe_load(to_yaml(report))["structures"] == expected


def test_a_structure_free_payload_carries_an_empty_structures_mapping(tmp_path):
    from ai_rfc.report import build, to_json
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=False))
    assert json.loads(to_json(build(load(path))))["structures"] == {}


def test_a_structure_free_report_has_no_structures_section(tmp_path):
    from ai_rfc.report import build, to_markdown
    from ai_rfc.schema import load

    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=False))
    assert "## Structures" not in to_markdown(build(load(path)))


# --- What the Markdown rendering does to a value it did not choose ---------
#
# `ai_rfc/report.py` writes the specification a reconstruction session
# produced, and every string in a manifest — the identifier, the title, a
# claim's id and its text, a structure's id, title and section — was written
# by that session. The grammar it lands in is Markdown, where a line break
# followed by `#` or `-` is a new heading or a new list item, and a backtick
# closes a code span.

import re  # noqa: E402

from ai_rfc.promotion import Violation  # noqa: E402
from ai_rfc.report import ManifestReport  # noqa: E402

#: One representative of each character that ends a line: CR and LF are
#: CommonMark's two, and the remaining five additionally split
#: :meth:`str.splitlines`. Built with :func:`chr` so this file carries no
#: unprintable character of its own — ``test_source_hygiene`` forbids that —
#: and named as a *sample* of the category, never as the category: the
#: renderer's defence is :func:`ai_rfc.driver.printable`'s predicate over
#: ``str.isprintable``, which is why a list like this one is only a witness.
LINE_BREAKERS = (
    chr(0x0D),
    chr(0x0A),
    chr(0x0B),
    chr(0x0C),
    chr(0x85),
    chr(0x2028),
    chr(0x2029),
)

#: A value carrying a backtick run *and* markup that only renders if the run
#: escapes the span. The `</code>` is what a single-backtick span used to
#: close into; the `<b>` is what went live after it.
HOSTILE = "x`</code><b>bold"

#: A code span, fence included. The backreference is what makes it a span
#: rather than a pair of backticks: a fence closes on a run of its own length.
CODE_SPAN = re.compile(r"(`+)(.*?)\1")


def _outside_spans(line: str) -> str:
    """What is left of a line once every code span is removed.

    Args:
        line: One rendered line.

    Returns:
        The line's text with each ``code span`` cut out, which is the part a
        Markdown reader renders as markup.
    """
    return CODE_SPAN.sub("", line)


def _longest_run(text: str) -> int:
    """The longest run of consecutive backticks in ``text``."""
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


def _line_starting(text: str, prefix: str) -> str:
    """The one rendered line beginning with ``prefix``.

    Args:
        text: The whole rendered report.
        prefix: What the wanted line starts with.

    Returns:
        That line.

    Raises:
        AssertionError: If the report has no such line, or more than one —
            either is the forgery this file is looking for.
    """
    lines = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, lines
    return lines[0]


def _structure(**overrides):
    """One record structure binding ``spec:1.1``, hostile where asked."""
    from ai_rfc.models import Field, Structure, StructureKind

    base = dict(
        id="header",
        kind=StructureKind.RECORD,
        title="Message header",
        section="4",
        fields=(Field(name="a", claim="spec:1.1"),),
    )
    base.update(overrides)
    return Structure(**base)


def _one_claim_manifest(structure=None, **overrides):
    """A one-claim manifest, optionally carrying one structure."""
    base = dict(rfc="SPEC-1", title="An Example Specification")
    base.update(overrides)
    return Manifest(
        claims=(_claim(status=Status.GAP),),
        structures=(structure,) if structure is not None else (),
        **base,
    )


def test_a_backtick_in_the_identifier_cannot_break_its_code_span():
    """`report.py:148` fenced the rfc with one backtick, so the value closed it.

    Asserted as the fence rule rather than as a rendering: the fence must be
    longer than the longest run inside it, which is what CommonMark requires
    and what a hand-checked example does not establish. The second assertion
    is the consequence — the markup the escape exists to contain stays inside
    the span, where it is literal text.
    """
    text = to_markdown(build(_one_claim_manifest(rfc=HOSTILE)))
    line = _line_starting(text, "Identifier:")
    span = CODE_SPAN.search(line)

    assert span is not None
    assert len(span.group(1)) > _longest_run(HOSTILE)
    assert span.group(2) == HOSTILE
    assert "<b>" not in _outside_spans(line)


def test_a_backtick_in_a_structure_id_cannot_break_its_code_span():
    """`:192`'s span carries an id the schema pattern never saw.

    ``schema.py:144`` validates a structure id, so this value cannot arrive
    through ``load``; a ``Manifest`` built in process — which is what the MCP
    door and every library caller build — has no such gate, and the renderer
    is the last place the difference can be caught.
    """
    manifest = _one_claim_manifest(structure=_structure(id=HOSTILE))
    line = _line_starting(to_markdown(build(manifest)), "- `")
    span = CODE_SPAN.search(line)

    assert span is not None
    assert len(span.group(1)) > _longest_run(HOSTILE)
    assert span.group(2) == HOSTILE
    assert "<b>" not in _outside_spans(line)


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
@pytest.mark.parametrize(
    "field", ["rfc", "title", "claim_id", "claim_text", "structure_title", "section"]
)
def test_no_line_ending_in_a_manifest_value_can_add_a_line(breaker, field):
    """Every interpolated value is one line, whatever the session wrote in it.

    A break followed by ``#`` or ``-`` is a heading or a list item, so a
    forged line is a forged *claim* in a document whose whole purpose is to
    say what was established. The report is compared against the same report
    built from a benign value, so the assertion is the count the renderer
    controls rather than a needle that a wording change would retire.
    """
    hostile = "a" + breaker + "## forged"

    def _report(value: str) -> str:
        claim = _claim(
            id=f"spec:{value}" if field == "claim_id" else "spec:1.1",
            text=value if field == "claim_text" else "Some text.",
            status=Status.GAP,
        )
        structure = _structure(
            title=value if field == "structure_title" else "Message header",
            section=value if field == "section" else "4",
        )
        manifest = Manifest(
            rfc=value if field == "rfc" else "SPEC-1",
            title=value if field == "title" else "An Example Specification",
            claims=(claim,),
            structures=(structure,),
        )
        return to_markdown(build(manifest))

    assert len(_report(hostile).splitlines()) == len(_report("a## forged").splitlines())


def test_a_line_ending_in_a_finding_cannot_add_a_line():
    """`:224` and `:239` interpolate text composed outside this module.

    A violation's reason is written by ``promotion``; an unverified anchor's
    is a caught ``AnchorError`` carrying ``git``'s own stderr, which is the
    clone's to control. Both land in a list, one item per line.
    """
    forged = "b" + chr(0x0A) + "- forged finding"
    report = ManifestReport(
        manifest=_one_claim_manifest(),
        violations=(Violation("spec:1.1", Status.CONFIRMED, Status.GAP, forged),),
        unverified=(forged,),
        anchors_checked=True,
        verifiable_anchor_count=1,
    )
    benign = ManifestReport(
        manifest=_one_claim_manifest(),
        violations=(Violation("spec:1.1", Status.CONFIRMED, Status.GAP, "b"),),
        unverified=("b",),
        anchors_checked=True,
        verifiable_anchor_count=1,
    )

    assert len(to_markdown(report).splitlines()) == len(
        to_markdown(benign).splitlines()
    )


def test_a_structure_binding_no_known_claim_is_reported_not_raised():
    """``statuses[structure.id]`` raised ``KeyError`` and lost the whole report.

    ``promotion.structure_statuses`` skips a structure with no bound claim —
    it has no claim to be as strong as — so the report's own lookup had no
    entry for it and the renderer crashed on a manifest it exists to
    describe. Two structures, because one bound structure is what puts the
    section on the page at all and so makes the missing entry reachable.

    Built programmatically because :func:`ai_rfc.schema.load` refuses this
    shape at the door (``header: binds spec:9.9, which is not a
    requirement``). The renderer is reached by every caller that assembles a
    :class:`~ai_rfc.models.Manifest` itself, and it must not be the thing
    that decides whether a report exists.
    """
    from ai_rfc.models import (
        Field,
        Intent,
        Level,
        Manifest,
        RequirementClaim,
        RequirementClass,
        Structure,
        StructureKind,
    )
    from ai_rfc.report import build, to_markdown

    claim = RequirementClaim(
        id="spec:1.1",
        text="The system responds within the configured interval.",
        section="1.1",
        level=Level.MUST,
        layer="timing",
        req_class=RequirementClass.PROTOCOL_BEHAVIORAL,
        intent=Intent.INTENDED,
    )
    manifest = Manifest(
        rfc="SPEC-1",
        title="x",
        claims=(claim,),
        structures=(
            Structure(
                id="alpha",
                kind=StructureKind.RECORD,
                title="Bound",
                section="3",
                fields=(Field(name="a", claim="spec:1.1"),),
            ),
            Structure(
                id="header",
                kind=StructureKind.RECORD,
                title="Message header",
                section="4",
                fields=(Field(name="a", claim="spec:9.9"),),
            ),
        ),
    )

    text = to_markdown(build(manifest))

    section = text.split("## Structures")[1].split("##")[0]
    assert "`alpha`" in section
    assert "`header`" in section
    assert "binds no claim this manifest declares" in section
