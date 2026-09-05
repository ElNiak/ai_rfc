"""Render a manifest structure as one delimited kramdown block.

Pure: a :class:`~ai_rfc.models.Structure` in, text out. The checkpoint freezes
what this produces, the gate re-derives it, and the lint counts it, so every
caller sees the same bytes.
"""

from __future__ import annotations

import re

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
    """Escape a table cell so a literal pipe cannot add a column."""
    return str(text).replace("|", r"\|")


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
        if field.width == VARIABLE_WIDTH:
            if row:
                rows.append(row)
                row, used = [], 0
            rows.append([(f"{field.name} (variable)", BITS_PER_ROW)])
            continue
        remaining = int(field.width or 0)
        first = True
        while remaining > 0:
            take = min(remaining, BITS_PER_ROW - used)
            row.append((field.name if first else f"{field.name} (cont.)", take))
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
    lines = _ruler(BITS_PER_ROW)
    for row in rows:
        bits = sum(width for _, width in row)
        lines.append(_rule(bits))
        cells = []
        for label, width in row:
            inner = width * 2 - 1
            cells.append(label[:inner].center(inner))
        lines.append(("|" + "|".join(cells) + "|").rstrip())
    lines.append(_rule(sum(width for _, width in rows[-1])))
    return lines


def _ladder(structure: Structure) -> list[str]:
    """Draw each state as a box, in declaration order, then its edges."""
    lines: list[str] = []
    for state in structure.states:
        box = f"| {state} |"
        lines.extend(
            ["+" + "-" * (len(box) - 2) + "+", box, "+" + "-" * (len(box) - 2) + "+"]
        )
    for transition in structure.transitions:
        arrow = f"{transition.source} --{transition.event}--> {transition.target}"
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
                    f"`ai_rfc:{field.claim}`",
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
                    f"`ai_rfc:{field.claim}`",
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
                    f"`ai_rfc:{value.claim}`",
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
                    f"`ai_rfc:{transition.claim}`",
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
        body; it never raises.
    """
    lines = text.splitlines()
    bodies: dict[str, str] = {}
    findings: list[str] = []
    open_id: str | None = None
    collected: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == _OPEN and index + 2 < len(lines):
            marker = lines[index + 1].strip()
            begin, end = _BEGIN.match(marker), _END.match(marker)
            if (begin or end) and lines[index + 2].strip() == _CLOSE:
                if begin:
                    if open_id is not None:
                        findings.append(
                            f"structure block {open_id} was never closed before "
                            f"{begin.group('id')} opened"
                        )
                    open_id, collected = begin.group("id"), []
                elif end:
                    closing = end.group("id")
                    if open_id is None:
                        findings.append(
                            f"structure block {closing} was closed but never opened"
                        )
                    elif closing != open_id:
                        findings.append(
                            f"structure block {open_id} was closed by {closing}"
                        )
                        open_id = None
                    else:
                        bodies[open_id] = "\n".join(collected) + "\n"
                        open_id = None
                index += 3
                continue
        if open_id is not None:
            collected.append(lines[index])
        index += 1
    if open_id is not None:
        findings.append(f"structure block {open_id} was never closed")
    return bodies, tuple(findings)
