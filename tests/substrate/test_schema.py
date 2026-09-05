from pathlib import Path

import pytest

from ai_rfc.models import EvidenceClass, Intent, RequirementClass, Status
from ai_rfc.schema import SchemaError, dump, load

from .draft.conftest import _manifest_text

pytestmark = pytest.mark.unit


def reload_from_text(text: str, tmp_path: Path):
    """Write ``text`` to a scratch file and load it back."""
    scratch = tmp_path / "round_trip.yaml"
    scratch.write_text(text)
    return load(scratch)


def load_text(text, path):
    path.write_text(text)
    return load(path)


def test_base_only_manifest_loads_with_restrictive_defaults(base_only_manifest: Path):
    manifest = load(base_only_manifest)
    assert manifest.rfc == "SPEC-1"
    assert len(manifest.claims) == 1
    claim = manifest.claims[0]
    assert claim.status is Status.GAP
    assert claim.anchors == ()
    assert claim.intent is Intent.UNKNOWN
    assert claim.testable is True


def test_extended_manifest_preserves_every_field(extended_manifest: Path):
    manifest = load(extended_manifest)
    by_id = {claim.id: claim for claim in manifest.claims}

    first = by_id["spec:1.1"]
    assert first.status is Status.CONFIRMED
    assert first.req_class is RequirementClass.PROTOCOL_BEHAVIORAL
    assert first.intent is Intent.INTENDED
    assert first.signed_off_by == "dev-01"
    assert first.question_id == "q-007"
    assert len(first.anchors) == 2
    assert first.anchors[0].evidence_class is EvidenceClass.CODE
    assert first.anchors[0].line == 42

    second = by_id["spec:2.1"]
    assert second.intent is Intent.ACCIDENTAL
    assert second.anchors[0].evidence_class is EvidenceClass.ADR
    assert second.anchors[0].commit is None


def test_section_identifiers_are_strings(extended_manifest: Path):
    manifest = load(extended_manifest)
    for claim in manifest.claims:
        assert isinstance(claim.section, str)
        assert isinstance(claim.id, str)


def test_unquoted_section_is_rejected_loudly(unquoted_sections_manifest: Path):
    with pytest.raises(SchemaError) as excinfo:
        load(unquoted_sections_manifest)
    message = str(excinfo.value)
    assert "section" in message
    assert "quote" in message.lower()


@pytest.mark.parametrize(
    "field,written,coerced",
    [
        # YAML 1.1 reads these as bool and int. `signed_off_by` is the worse of
        # the two: `adjudicate` returns CONFIRMED for any truthy value, so an
        # unquoted `yes` promotes a claim to the strongest status with no signer
        # behind it, and `is_externally_checked` then inflates checked_fraction.
        ("signed_off_by", "yes", "bool"),
        ("signed_off_by", "true", "bool"),
        # A claim whose question-id is an int can never match the register,
        # which holds strings, so the gate reports a question that does exist
        # as missing.
        ("question-id", "7", "int"),
    ],
)
def test_unquoted_extended_identifiers_are_rejected_loudly(
    tmp_path: Path, field: str, written: str, coerced: str
):
    """Every field the schema gates must be gated, not just some of them.

    The README documents `signed_off_by` and `question-id` as strings, but they
    were absent from the checked set, so YAML coerced them silently.
    """
    document = (
        "rfc: test\n"
        "title: t\n"
        "requirements:\n"
        '  "spec:1":\n'
        "    text: t\n"
        '    section: "1"\n'
        "    level: MUST\n"
        "    layer: app\n"
        f"    {field}: {written}\n"
    )
    path = tmp_path / "manifest.yaml"
    path.write_text(document)

    with pytest.raises(SchemaError) as excinfo:
        load(path)
    message = str(excinfo.value)
    assert field in message
    assert coerced in message
    assert "quote" in message.lower()


def test_load_of_dump_is_a_fixed_point(extended_manifest: Path, tmp_path: Path):
    manifest = load(extended_manifest)
    assert reload_from_text(dump(manifest), tmp_path) == manifest


def test_dump_is_byte_stable(extended_manifest: Path, tmp_path: Path):
    manifest = load(extended_manifest)
    once = dump(manifest)
    twice = dump(reload_from_text(once, tmp_path))
    assert once == twice


def test_anchor_missing_evidence_class_is_a_schema_error(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "rfc: SPEC-1\n"
        "title: 'x'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: 'x'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: timing\n"
        "    anchors:\n"
        "      - locator: src/a.py\n"
    )
    with pytest.raises(SchemaError) as excinfo:
        load(path)
    assert "evidence_class" in str(excinfo.value)


def test_unknown_status_value_is_a_schema_error(tmp_path: Path):
    path = tmp_path / "bad_status.yaml"
    path.write_text(
        "rfc: SPEC-1\n"
        "title: 'x'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: 'x'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: timing\n"
        "    status: probably\n"
    )
    with pytest.raises(SchemaError) as excinfo:
        load(path)
    assert "probably" in str(excinfo.value)


def test_anchor_line_sha256_round_trips(tmp_path: Path):
    digest = "ab" * 32
    manifest = reload_from_text(
        "rfc: SPEC-1\n"
        "title: 'x'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: 'x'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: timing\n"
        "    anchors:\n"
        "      - evidence_class: code\n"
        "        locator: src/a.py\n"
        f"        commit: '{'0' * 40}'\n"
        "        line: 3\n"
        f"        line_sha256: '{digest}'\n",
        tmp_path,
    )
    anchor = manifest.claims[0].anchors[0]
    assert anchor.line_sha256 == digest
    assert f"line_sha256: {digest}" in dump(manifest)


def test_anchor_line_sha256_without_line_is_rejected(tmp_path: Path):
    with pytest.raises(SchemaError) as excinfo:
        reload_from_text(
            "rfc: SPEC-1\n"
            "title: 'x'\n"
            "requirements:\n"
            "  'spec:1.1':\n"
            "    text: 'x'\n"
            "    section: '1.1'\n"
            "    level: MUST\n"
            "    layer: timing\n"
            "    anchors:\n"
            "      - evidence_class: code\n"
            "        locator: src/a.py\n"
            f"        line_sha256: '{'ab' * 32}'\n",
            tmp_path,
        )
    assert "line_sha256" in str(excinfo.value)


def test_a_whitespace_only_signer_is_refused(tmp_path: Path):
    """`signed_off_by` is the strongest lever in the promotion rule.

    Blanks are not names, and `schema` refuses a malformed document rather than
    repairing it — treating whitespace as absent would silently downgrade a
    claim the author believed they had signed.
    """
    path = tmp_path / "blank_signer.yaml"
    path.write_text(
        "rfc: SPEC-1\n"
        "title: 'x'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: 'x'\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: timing\n"
        "    signed_off_by: '   '\n"
    )
    with pytest.raises(SchemaError) as excinfo:
        load(path)
    assert "signed_off_by" in str(excinfo.value)


def test_a_duplicated_requirement_id_is_refused(tmp_path: Path):
    """Two claims, one id: safe_load keeps the last and the count under-reports."""
    path = tmp_path / "duplicate_id.yaml"
    path.write_text(
        "rfc: SPEC-1\n"
        "title: 'x'\n"
        "requirements:\n"
        "  'spec:1.1':\n"
        "    text: first\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: timing\n"
        "  'spec:1.1':\n"
        "    text: second\n"
        "    section: '1.1'\n"
        "    level: MUST\n"
        "    layer: timing\n"
    )
    with pytest.raises(SchemaError) as excinfo:
        load(path)
    assert "spec:1.1" in str(excinfo.value)


def test_a_yaml_syntax_error_is_a_schema_error(tmp_path: Path):
    """Every caller of ``load`` catches ``SchemaError``, not raw YAML errors.

    A bare ``yaml.YAMLError`` escaping ``load`` crashes every one of them
    instead of reporting a finding.
    """
    path = tmp_path / "broken.yaml"
    path.write_text("claims: [\n")
    with pytest.raises(SchemaError) as excinfo:
        load(path)
    assert str(path) in str(excinfo.value)


STRUCTURES = """\
structures:
  header:
    kind: wire-format
    title: Message header
    section: "4.1"
    fields:
      - name: version
        width: 8
        claim: spec:1.1
        description: Protocol version.
      - name: payload
        width: variable
        claim: spec:2.1
"""

#: Appended after ``STRUCTURES`` to declare two structures out of id order.
SECOND_STRUCTURE = """\
  codes:
    kind: enum
    title: Error codes
    section: "6"
    values:
      - name: NO_ERROR
        value: "0"
        claim: spec:2.1
"""


def _with_structures(tmp_path, block=STRUCTURES, **kwargs):
    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=True, **kwargs) + block)
    return path


def test_every_bcp14_level_loads_and_anything_else_is_refused(tmp_path):
    from ai_rfc.models import Level

    for keyword in ("MUST", "MUST NOT", "SHOULD", "SHOULD NOT", "MAY"):
        path = tmp_path / "level.yaml"
        path.write_text(
            _manifest_text(with_second_claim=False).replace(
                "level: MUST", f"level: {keyword}"
            )
        )
        assert load(path).claims[0].level is Level(keyword)

    path = tmp_path / "bad.yaml"
    path.write_text(
        _manifest_text(with_second_claim=False).replace(
            "level: MUST", "level: descriptive"
        )
    )
    with pytest.raises(SchemaError) as error:
        load(path)
    assert "permitted values are" in str(error.value)
    assert "descriptive" in str(error.value)


def test_structures_load_with_their_members(tmp_path):
    from ai_rfc.models import StructureKind

    manifest = load(_with_structures(tmp_path))
    assert len(manifest.structures) == 1
    header = manifest.structures[0]
    assert header.id == "header"
    assert header.kind is StructureKind.WIRE_FORMAT
    assert header.claims == ("spec:1.1", "spec:2.1")
    assert header.fields[0].width == 8
    assert header.fields[1].width == "variable"


def test_a_bound_claim_must_exist_in_requirements(tmp_path):
    block = STRUCTURES.replace("claim: spec:2.1", "claim: spec:9.9")
    with pytest.raises(SchemaError) as error:
        load(_with_structures(tmp_path, block=block))
    assert "spec:9.9" in str(error.value)
    assert "not a requirement" in str(error.value)


def test_variable_width_is_refused_anywhere_but_the_last_field(tmp_path):
    block = STRUCTURES.replace("width: 8", "width: variable")
    with pytest.raises(SchemaError) as error:
        load(_with_structures(tmp_path, block=block))
    assert "only the last field" in str(error.value)


def test_a_transition_must_name_declared_states(tmp_path):
    block = """\
structures:
  conn:
    kind: state-machine
    title: Connection
    section: "5"
    states: [idle, open]
    transitions:
      - from: idle
        event: connect
        to: half-open
        claim: spec:1.1
"""
    with pytest.raises(SchemaError) as error:
        load(_with_structures(tmp_path, block=block))
    assert "half-open" in str(error.value)
    assert "not a declared state" in str(error.value)


def test_structure_ids_are_constrained(tmp_path):
    for bad in ("-header", "hea--der", "head er"):
        block = STRUCTURES.replace("  header:", f"  {bad}:")
        with pytest.raises(SchemaError) as error:
            load(_with_structures(tmp_path, block=block))
        assert bad in str(error.value)


def test_an_unquoted_structure_id_yaml_reads_as_a_number_is_refused(tmp_path):
    """An id YAML coerced to a float must not reach the id pattern.

    ``re.match`` raises ``TypeError`` on a non-string, which would escape
    ``load`` uncaught and defeat the rule that every malformed manifest
    surfaces as a ``SchemaError``.
    """
    block = STRUCTURES.replace("  header:", "  4.1:")
    with pytest.raises(SchemaError) as error:
        load(_with_structures(tmp_path, block=block))
    assert "4.1" in str(error.value)
    assert "float" in str(error.value)
    assert "quote" in str(error.value).lower()


def test_structures_round_trip_whatever_order_they_are_declared_in(tmp_path):
    """``structures:`` is a registry keyed by id; declaration order means nothing.

    ``dump`` sorts its keys, so a ``load`` preserving document order would make
    ``load(dump(m)) != m`` for any manifest declaring two structures out of id
    order — a fixed-point failure whose cause is invisible at the assertion.
    """
    path = _with_structures(tmp_path, block=STRUCTURES + SECOND_STRUCTURE)
    manifest = load(path)
    assert [structure.id for structure in manifest.structures] == ["codes", "header"]

    once = dump(manifest)
    assert manifest == load_text(once, tmp_path / "round.yaml")
    assert once == dump(load_text(once, tmp_path / "round.yaml"))


def test_a_structure_free_manifest_dumps_exactly_as_before(tmp_path):
    path = tmp_path / "plain.yaml"
    path.write_text(_manifest_text(with_second_claim=False))
    text = dump(load(path))
    assert "structures" not in text
    assert text == dump(load(path))


def test_structures_serialise_between_requirements_and_title(tmp_path):
    text = dump(load(_with_structures(tmp_path)))
    assert (
        text.index("requirements:") < text.index("structures:") < text.index("title:")
    )
    assert load(_with_structures(tmp_path)) == load_text(text, tmp_path / "round.yaml")
