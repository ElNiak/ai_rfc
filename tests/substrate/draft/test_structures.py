"""The structure renderer and the delimited-block reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_rfc.draft.structures import block_spans, parse_blocks, render, render_all
from ai_rfc.models import Field, Manifest, Structure, StructureKind, Transition, Value

pytestmark = pytest.mark.unit


def _wire():
    return Structure(
        id="header",
        kind=StructureKind.WIRE_FORMAT,
        title="Message header",
        section="4.1",
        fields=(
            Field(
                name="Version",
                claim="spec:1.1",
                width=4,
                description="Protocol version.",
            ),
            Field(name="Type", claim="spec:1.2", width=4),
            Field(name="Length", claim="spec:1.3", width=24),
            Field(name="Payload", claim="spec:1.4", width="variable"),
        ),
    )


def _enum():
    return Structure(
        id="codes",
        kind=StructureKind.ENUM,
        title="Error codes",
        section="6",
        values=(
            Value(
                name="NO_ERROR",
                value="0x00",
                claim="spec:6.1",
                description="Normal close.",
            ),
            Value(name="PROTO", value="0x01", claim="spec:6.2"),
        ),
    )


def _machine():
    return Structure(
        id="conn",
        kind=StructureKind.STATE_MACHINE,
        title="Connection lifecycle",
        section="5",
        states=("idle", "open", "closed"),
        transitions=(
            Transition(source="idle", event="connect", target="open", claim="spec:5.1"),
            Transition(
                source="open",
                event="timeout",
                target="closed",
                claim="spec:5.2",
                guard="no traffic",
            ),
        ),
    )


def test_a_block_is_delimited_by_its_own_id():
    text = render(_enum())
    assert "{::comment}\nai_rfc:struct:codes begin\n{:/comment}" in text
    assert "{::comment}\nai_rfc:struct:codes end\n{:/comment}" in text


def test_no_line_carries_trailing_whitespace():
    # The template's lint-whitespace target refuses it, and the bit ruler
    # produces it naturally, so this is the property most likely to regress.
    for structure in (_wire(), _enum(), _machine()):
        for line in render(structure).splitlines():
            assert line == line.rstrip(), repr(line)


def test_every_bound_claim_appears_as_a_citation_token_outside_the_artwork():
    text = render(_wire())
    artwork = text.split("~~~")[1]
    for claim in ("spec:1.1", "spec:1.2", "spec:1.3", "spec:1.4"):
        assert f"`ai_rfc:{claim}`" in text
        assert claim not in artwork


def test_the_bit_diagram_wraps_at_thirty_two_bits_and_fits_seventy_two_columns():
    text = render(_wire())
    artwork = text.split("~~~")[1]
    lines = [line for line in artwork.splitlines() if line]
    assert all(len(line) <= 72 for line in lines)
    # 4 + 4 + 24 bits exactly fill one row; the variable field takes its own.
    assert artwork.count("+-+") >= 2
    assert "Payload (variable)" in artwork


def test_a_field_wider_than_a_row_is_split_and_marked_continued():
    wide = Structure(
        id="big",
        kind=StructureKind.WIRE_FORMAT,
        title="Wide",
        section="4.2",
        fields=(Field(name="Nonce", claim="spec:1.1", width=48),),
    )
    artwork = render(wide).split("~~~")[1]
    assert "Nonce" in artwork
    assert "(cont.)" in artwork


def test_a_full_row_above_a_partial_one_is_closed_on_its_right():
    # The border between two rows belongs to the wider of them, or the full row
    # above is drawn open on the right where the partial row below stops.
    wide = Structure(
        id="big",
        kind=StructureKind.WIRE_FORMAT,
        title="Wide",
        section="4.2",
        fields=(Field(name="Nonce", claim="spec:1.1", width=48),),
    )
    lines = [line for line in render(wide).split("~~~")[1].splitlines() if line]
    continued = next(index for index, line in enumerate(lines) if "(cont.)" in line)
    assert lines[continued - 1] == "+" + "-+" * 32
    assert lines[-1] == "+" + "-+" * 16


def test_a_wire_format_field_without_a_width_is_refused():
    broken = Structure(
        id="big",
        kind=StructureKind.WIRE_FORMAT,
        title="Wide",
        section="4.2",
        fields=(
            Field(name="Nonce", claim="spec:1.1", width=8),
            Field(name="Rest", claim="spec:1.2"),
        ),
    )
    with pytest.raises(ValueError, match="Rest has no width"):
        render(broken)


def test_a_pipe_in_a_cell_is_escaped():
    odd = Structure(
        id="odd",
        kind=StructureKind.RECORD,
        title="Odd",
        section="7",
        fields=(Field(name="a|b", claim="spec:1.1", type="uint8", description="x|y"),),
    )
    body = render(odd)
    assert r"a\|b" in body and r"x\|y" in body


def test_each_kind_renders_its_own_legend():
    assert "| Field | Bits | Description | Claim |" in render(_wire())
    assert "| Value | Name | Description | Claim |" in render(_enum())
    assert "| From | Event | Guard | To | Claim |" in render(_machine())
    record = Structure(
        id="rec",
        kind=StructureKind.RECORD,
        title="A record",
        section="7",
        fields=(Field(name="n", claim="spec:1.1", type="uint8", description="d"),),
    )
    assert "| Field | Type | Size | Description | Claim |" in render(record)


def test_a_state_machine_draws_its_states_before_the_table():
    text = render(_machine())
    artwork = text.split("~~~")[1]
    for state in ("idle", "open", "closed"):
        assert state in artwork


def test_rendering_is_deterministic_and_ordered_by_id():
    manifest = Manifest(
        rfc="spec", title="T", claims=(), structures=(_machine(), _enum())
    )
    first = render_all(manifest)
    assert first == render_all(manifest)
    assert first.index("ai_rfc:struct:codes") < first.index("ai_rfc:struct:conn")


def test_parse_blocks_round_trips_what_render_produced():
    manifest = Manifest(rfc="spec", title="T", claims=(), structures=(_wire(), _enum()))
    bodies, findings = parse_blocks(render_all(manifest))
    assert findings == ()
    assert set(bodies) == {"header", "codes"}
    assert (
        bodies["codes"]
        == render(_enum()).split("{:/comment}\n", 1)[1].rsplit("{::comment}", 1)[0]
    )


@pytest.mark.parametrize(
    "text, needle",
    [
        ("{::comment}\nai_rfc:struct:a begin\n{:/comment}\nbody\n", "never closed"),
        ("{::comment}\nai_rfc:struct:a end\n{:/comment}\n", "closed but never opened"),
        (
            "{::comment}\nai_rfc:struct:a begin\n{:/comment}\n"
            "{::comment}\nai_rfc:struct:b end\n{:/comment}\n",
            "closed by b",
        ),
    ],
)
def test_malformed_delimiters_are_findings_not_exceptions(text, needle):
    bodies, findings = parse_blocks(text)
    assert bodies == {}
    assert any(needle in finding for finding in findings), findings


def test_block_spans_bounds_one_block_by_its_delimiter_lines():
    text = "x\n" + render(_enum()) + "y\n"
    lines = text.splitlines()
    (span,) = block_spans(text)
    first, last = span
    assert (first, last) == (1, len(lines) - 2)
    assert lines[first] == "{::comment}"
    assert lines[first + 1] == "ai_rfc:struct:codes begin"
    assert lines[last - 1] == "ai_rfc:struct:codes end"
    assert lines[last] == "{:/comment}"


def test_block_spans_finds_every_block_in_order():
    manifest = Manifest(rfc="spec", title="T", claims=(), structures=(_wire(), _enum()))
    spans = block_spans(render_all(manifest))
    assert len(spans) == 2
    assert spans[0][1] < spans[1][0]


def test_block_spans_skips_a_block_that_is_never_closed():
    text = "{::comment}\nai_rfc:struct:a begin\n{:/comment}\nbody\n"
    assert block_spans(text) == ()
    assert parse_blocks(text)[0] == {}


def test_block_spans_and_parse_blocks_agree_on_every_block():
    # Two readers of one input drift, and this module has two: parse_blocks
    # returns the bodies, block_spans the bounds. Pin them to each other.
    manifest = Manifest(rfc="spec", title="T", claims=(), structures=(_wire(), _enum()))
    text = render_all(manifest)
    lines = text.splitlines()
    bodies, _ = parse_blocks(text)
    spans = block_spans(text)
    assert len(spans) == len(bodies)
    bounded = []
    for first, last in spans:
        # A span bounds the delimiters too: three lines open it, three close it.
        start, stop = first + 3, last - 2
        bounded.append("\n".join(lines[start:stop]) + "\n")
    assert sorted(bounded) == sorted(bodies.values())


GOLDENS = Path(__file__).parent / "goldens"


def _message():
    return Structure(
        id="hello",
        kind=StructureKind.MESSAGE,
        title="Hello",
        section="3.1",
        fields=(
            Field(
                name="token",
                claim="spec:3.1",
                type="opaque",
                width=64,
                description="Session token.",
            ),
        ),
    )


def _record():
    return Structure(
        id="entry",
        kind=StructureKind.RECORD,
        title="Log entry",
        section="7.2",
        fields=(
            Field(
                name="stamp",
                claim="spec:7.1",
                type="uint64",
                description="Milliseconds.",
            ),
        ),
    )


@pytest.mark.parametrize(
    "name, build",
    [
        ("wire-format", _wire),
        ("message", _message),
        ("record", _record),
        ("enum", _enum),
        ("state-machine", _machine),
    ],
)
def test_each_kind_matches_its_golden(name, build, request):
    produced = render(build())
    path = GOLDENS / f"{name}.md"
    if request.config.getoption("--update-goldens"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(produced)
    assert path.read_text() == produced, (
        f"the {name} rendering changed, so every structures.md frozen in every "
        f"checkpoint is now stale and every gate over them will fail. If the "
        f"change is intended, re-run with --update-goldens and say so in the "
        f"commit message."
    )
