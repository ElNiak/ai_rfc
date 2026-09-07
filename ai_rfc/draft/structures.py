"""Render a manifest structure as one delimited kramdown block.

Pure: a :class:`~ai_rfc.models.Structure` in, text out. The checkpoint freezes
what this produces, the gate re-derives it, and the lint counts it, so every
caller sees the same bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

from ai_rfc.models import (
    FIELD_KINDS,
    VARIABLE_WIDTH,
    Manifest,
    Structure,
    StructureKind,
)

#: The frozen rendering beside a checkpoint's ``manifest.yaml``.
STRUCTURES_FILE = "structures.md"

#: Bits per row of a wire-format diagram; 32 keeps a row inside 72 columns.
BITS_PER_ROW = 32

_BEGIN = re.compile(r"^ai_rfc:struct:(?P<id>\S+)\s+begin$")
_END = re.compile(r"^ai_rfc:struct:(?P<id>\S+)\s+end$")
_OPEN = "{::comment}"
_CLOSE = "{:/comment}"


def _cell(text: object) -> str:
    """Collapse author text onto one line, then escape its pipes.

    A block's bytes are what the checkpoint freezes and the gate compares, and
    a delimiter marker owns a whole line of its own. So no author text may
    carry a line break: one lets a member spell an end marker and a following
    begin marker, closing and reopening its own block, and the gate then reads
    back only the fragment after the forged reopen. Every interpolation of
    author text into a block goes through here.
    """
    return " ".join(str(text).split()).replace("|", r"\|")


def _rule(bits: int) -> str:
    return "+" + "-+" * bits


def _ruler(bits: int) -> list[str]:
    tens = " " + " ".join(str(i // 10) if i % 10 == 0 else " " for i in range(bits))
    ones = " " + " ".join(str(i % 10) for i in range(bits))
    return [tens.rstrip(), ones.rstrip()]


def _rows(structure: Structure) -> list[list[tuple[str, int]]]:
    """Lay the fields out over 32-bit rows, splitting any that straddles."""
    rows: list[list[tuple[str, int]]] = []
    row: list[tuple[str, int]] = []
    used = 0
    for field in structure.fields:
        name = _cell(field.name)
        if field.width == VARIABLE_WIDTH:
            if row:
                rows.append(row)
                row, used = [], 0
            rows.append([(f"{name} (variable)", BITS_PER_ROW)])
            continue
        if field.width is None:
            raise ValueError(
                f"{structure.id}: wire-format field {field.name} has no width"
            )
        remaining = int(field.width)
        first = True
        while remaining > 0:
            take = min(remaining, BITS_PER_ROW - used)
            row.append((name if first else f"{name} (cont.)", take))
            used += take
            remaining -= take
            first = False
            if used == BITS_PER_ROW:
                rows.append(row)
                row, used = [], 0
    if row:
        rows.append(row)
    return rows


def _diagram(structure: Structure) -> list[str]:
    rows = _rows(structure)
    if not rows:
        return []
    widths = [sum(width for _, width in row) for row in rows]
    lines = _ruler(BITS_PER_ROW)
    for index, row in enumerate(rows):
        # A border belongs to the wider of the two rows it separates, or the
        # full row above is left open on the right where the partial row ends.
        above = widths[index] if index == 0 else max(widths[index - 1], widths[index])
        lines.append(_rule(above))
        cells = []
        for label, width in row:
            inner = width * 2 - 1
            cells.append(label[:inner].center(inner))
        lines.append(("|" + "|".join(cells) + "|").rstrip())
    lines.append(_rule(widths[-1]))
    return lines


def _ladder(structure: Structure) -> list[str]:
    """Draw each state as a box, in declaration order, then its edges."""
    lines: list[str] = []
    for state in structure.states:
        box = f"| {_cell(state)} |"
        lines.extend(
            ["+" + "-" * (len(box) - 2) + "+", box, "+" + "-" * (len(box) - 2) + "+"]
        )
    for transition in structure.transitions:
        arrow = (
            f"{_cell(transition.source)} --{_cell(transition.event)}--> "
            f"{_cell(transition.target)}"
        )
        lines.append(arrow)
    return [line.rstrip() for line in lines]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _body(structure: Structure) -> list[str]:
    lines = [f"**{_cell(structure.title)}**", ""]
    if structure.kind is StructureKind.WIRE_FORMAT:
        lines += ["~~~"] + _diagram(structure) + ["~~~", ""]
        lines += _table(
            ["Field", "Bits", "Description", "Claim"],
            [
                [
                    _cell(field.name),
                    _cell(field.width if field.width is not None else "-"),
                    _cell(field.description or "-"),
                    f"`ai_rfc:{_cell(field.claim)}`",
                ]
                for field in structure.fields
            ],
        )
    elif structure.kind in FIELD_KINDS:
        lines += _table(
            ["Field", "Type", "Size", "Description", "Claim"],
            [
                [
                    _cell(field.name),
                    _cell(field.type or "-"),
                    _cell(field.width if field.width is not None else "-"),
                    _cell(field.description or "-"),
                    f"`ai_rfc:{_cell(field.claim)}`",
                ]
                for field in structure.fields
            ],
        )
    elif structure.kind is StructureKind.ENUM:
        lines += _table(
            ["Value", "Name", "Description", "Claim"],
            [
                [
                    _cell(value.value),
                    _cell(value.name),
                    _cell(value.description or "-"),
                    f"`ai_rfc:{_cell(value.claim)}`",
                ]
                for value in structure.values
            ],
        )
    else:
        lines += ["~~~"] + _ladder(structure) + ["~~~", ""]
        lines += _table(
            ["From", "Event", "Guard", "To", "Claim"],
            [
                [
                    _cell(transition.source),
                    _cell(transition.event),
                    _cell(transition.guard or "-"),
                    _cell(transition.target),
                    f"`ai_rfc:{_cell(transition.claim)}`",
                ]
                for transition in structure.transitions
            ],
        )
    return lines


def render(structure: Structure) -> str:
    """Render one structure as a delimited kramdown block.

    Args:
        structure: The structure to render.

    Returns:
        The block, delimiters included, ending in a newline. Every line is
        stripped of trailing whitespace so the template's ``lint-whitespace``
        target accepts it.

    Raises:
        ValueError: If a wire-format field carries no width. The schema already
            refuses that in a loaded manifest, so this catches a programmatic
            structure that would otherwise render a silently wrong figure.
    """
    lines = [
        _OPEN,
        f"ai_rfc:struct:{structure.id} begin",
        _CLOSE,
        *_body(structure),
        _OPEN,
        f"ai_rfc:struct:{structure.id} end",
        _CLOSE,
    ]
    return "\n".join(line.rstrip() for line in lines) + "\n"


def render_all(manifest: Manifest) -> str:
    """Render every structure, ordered by id, as the frozen ``structures.md``."""
    blocks = [render(s) for s in sorted(manifest.structures, key=lambda s: s.id)]
    return "\n".join(blocks)


class _Open(NamedTuple):
    """The block a begin marker left open: where it started, and its id."""

    at: int
    id: str


class _Block(NamedTuple):
    """One closed block: its id, its inclusive bounds, and its interior."""

    id: str
    first: int
    last: int
    lines: list[str]


class _Malformed(NamedTuple):
    """A delimiter sequence that closed no block, and the finding it earns."""

    finding: str


def _scan(lines: list[str]) -> Iterator[_Block | _Malformed]:
    """Walk a draft's delimiter lines once, in document order.

    :func:`parse_blocks` and :func:`block_spans` are both projections of this
    rather than two walks of the same grammar, for the reason
    :func:`parse_blocks` gives its own callers: two readers of one input drift,
    and a drifted reader reports a finding that is not there.

    Args:
        lines: The draft source, split into lines.

    Yields:
        A :class:`_Block` for each block closed by its own id, and a
        :class:`_Malformed` for each delimiter sequence that closes none,
        interleaved in the order the draft states them.
    """
    closed: set[str] = set()
    opened: _Open | None = None
    collected: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == _OPEN and index + 2 < len(lines):
            marker = lines[index + 1].strip()
            begin, end = _BEGIN.match(marker), _END.match(marker)
            if (begin or end) and lines[index + 2].strip() == _CLOSE:
                if begin:
                    opening = begin.group("id")
                    if opened is not None:
                        yield _Malformed(
                            f"structure block {opened.id} was never closed before "
                            f"{opening} opened"
                        )
                    if opening in closed:
                        yield _Malformed(
                            f"structure block {opening} appears more than once"
                        )
                    opened, collected = _Open(index, opening), []
                elif end is not None:
                    closing = end.group("id")
                    if opened is None:
                        yield _Malformed(
                            f"structure block {closing} was closed but never opened"
                        )
                    elif closing != opened.id:
                        yield _Malformed(
                            f"structure block {opened.id} was closed by {closing}"
                        )
                        opened = None
                    else:
                        yield _Block(opened.id, opened.at, index + 2, collected)
                        closed.add(opened.id)
                        opened, collected = None, []
                index += 3
                continue
        if opened is not None:
            collected.append(lines[index])
        index += 1
    if opened is not None:
        yield _Malformed(f"structure block {opened.id} was never closed")


def parse_blocks(text: str) -> tuple[dict[str, str], tuple[str, ...]]:
    """Read every delimited structure block out of a draft.

    The gate and the lint both call this rather than each matching their own
    pattern: two readers of one input drift, and a drifted reader reports a
    finding that is not there.

    Args:
        text: The draft source.

    Returns:
        A pair of the bodies by structure id, and findings describing every
        malformed delimiter. A malformed block contributes a finding and no
        body; a block id that opens a second time contributes a finding and
        keeps the body it had, so a later block can never silently replace the
        one the gate compares. It never raises.
    """
    bodies: dict[str, str] = {}
    findings: list[str] = []
    for event in _scan(text.splitlines()):
        if isinstance(event, _Malformed):
            findings.append(event.finding)
        elif event.id not in bodies:
            bodies[event.id] = "\n".join(event.lines) + "\n"
    return bodies, tuple(findings)


def block_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Locate every closed structure block by its delimiter lines.

    The lint needs the bounds rather than the bodies :func:`parse_blocks`
    returns, to tell a figure the renderer produced from one the author wrote.

    Args:
        text: The draft source.

    Returns:
        One ``(first, last)`` pair per closed block, in document order. Both are
        0-based line indices into ``text`` and both are inclusive: ``first`` is
        the ``{::comment}`` opening the begin marker, ``last`` the
        ``{:/comment}`` closing the matching end marker. A block left unclosed,
        closed by another id, or closed without opening contributes no span. A
        repeated id contributes one span per closed block, where
        :func:`parse_blocks` keeps only the first body: both are blocks the
        author wrote, and the lint counts them.
    """
    return tuple(
        (event.first, event.last)
        for event in _scan(text.splitlines())
        if isinstance(event, _Block)
    )
