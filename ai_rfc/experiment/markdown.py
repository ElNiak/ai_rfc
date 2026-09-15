"""Markdown table primitives, shared by every renderer in this package.

A cell and the separator under it have to agree about what a column is, so
they live together rather than being reached into from a sibling: the campaign
report wrote them first, and the draft-quality comparison was the first module
to import them across a module boundary.

They agree here because :func:`separator` counts only the pipes :func:`cell`
did *not* escape. Before that they disagreed by construction — ``cell``
escapes a pipe in a value rather than removing it, so a header carrying one
bought the table a column the header did not have, and the only defence was
every author remembering to size the separator from something else.

Nothing here imports beyond :func:`ai_rfc.driver.printable`, for the reason
that function's own docstring gives for sitting at a package root: a leaf
several callers need should not make any of them load machinery to reach it.
"""

from __future__ import annotations

from typing import Any

from ai_rfc.driver import printable


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
