"""The structure renderer and the delimited-block reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_rfc.draft.structures import parse_blocks, render, render_all
from ai_rfc.models import (
    Field,
    Manifest,
    Structure,
    StructureKind,
    Transition,
    Value,
)

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
