"""Markdown primitives, shared by every renderer this package ships.

**At the package root, not under** ``experiment/``, **because the production
renderer needs them too.** :mod:`ai_rfc.report` — the manifest report the
substrate CLI writes — interpolates a value into a code span on two of its
lines, and a substrate module may not import :mod:`ai_rfc.experiment`: that
edge is the one :mod:`ai_rfc.driver` states in the other direction, and a
shared leaf reached across it would invert the package's layering to save a
move. So the escapers live above both renderers and each imports them.

A cell and the separator under it have to agree about what a column is, so
they live together rather than being reached into from a sibling: the campaign
report wrote them first, and the draft-quality comparison was the first module
to import them across a module boundary.

They agree here because :func:`separator` counts only the pipes :func:`cell`
did *not* escape. Before that they disagreed by construction — ``cell``
escapes a pipe in a value rather than removing it, so a header carrying one
bought the table a column the header did not have, and the only defence was
every author remembering to size the separator from something else.

:func:`code` and :func:`cell` are the two grammars, not one: a span is
delimited by backticks and a cell by pipes, and each escapes what ends *its*
enclosure. Both run :func:`~ai_rfc.driver.printable` first, because a line
ending ends either one.

Nothing here imports beyond :func:`ai_rfc.driver.printable`, for the reason
that function's own docstring gives for sitting at a package root: a leaf
several callers need should not make any of them load machinery to reach it.
"""

from __future__ import annotations

import re
from typing import Any

from .driver import printable


def fmt(value: Any, digits: int = 3) -> str:
    """One value as the text a report shows for it.

    Args:
        value: Any value. ``None`` renders as the em dash: a metric nothing
            measured must not read as a zero.
        digits: Decimal places for a float.

    Returns:
        The rendered text.
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def code(value: Any) -> str:
    """One value as a Markdown code span that its own content cannot break.

    A run of backticks inside the value is fenced by a longer run outside it,
    per CommonMark; a leading or trailing backtick needs the padding space.

    :func:`~ai_rfc.driver.printable` runs first, so a character that would end
    the line is already a visible escape by the time the fence is measured. It
    is the predicate over the whole unprintable category, which is what this
    needs: neutralising ``\\n`` alone left CR — CommonMark's other line ending
    — and five further characters :meth:`str.splitlines` breaks on. Nothing
    else is escaped, because a code span's content is literal.

    Deliberately not routed through :func:`fmt`. The em dash for ``None`` is
    the one rendering the two share; the rest of ``fmt`` — ``yes``/``no`` for
    a bool, a fixed number of decimals for a float — is how a *table* presents
    a measurement, and a span shows what the value is.

    Args:
        value: Any value; ``None`` renders as the em dash, never as a span.

    Returns:
        The fenced span, or ``"—"`` for ``None``.
    """
    if value is None:
        return "—"
    text = printable(str(value))
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def cell(value: Any) -> str:
    """One value as a table cell that cannot add a column or a row.

    :func:`~ai_rfc.driver.printable` runs first, so every character that ends a
    line is already a visible escape before the backslashes it wrote are
    doubled; a pipe is escaped after that doubling, so its own backslash stays
    single.

    Args:
        value: Any value.

    Returns:
        The cell text with pipes escaped and every line ending made visible.
    """
    return printable(str(fmt(value))).replace("\\", "\\\\").replace("|", "\\|")


def separator(header: str) -> str:
    """The ``---`` row for a markdown table, sized from its own header.

    Counting the header's columns keeps the two in step; a hand-written width
    silently misrenders the table when a column is added. Only the pipes
    :func:`cell` left structural are counted: an escaped one is content, and
    counting it would widen the separator past the header it was sized from.

    Args:
        header: The rendered header row, outer rails included.

    Returns:
        The separator row, one ``---`` per column of ``header``.
    """
    return "|" + "---|" * (header.count("|") - header.count("\\|") - 1)
