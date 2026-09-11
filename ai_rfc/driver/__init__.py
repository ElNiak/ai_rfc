"""Everything that launches and reads one ``claude -p`` session.

This package is the lower layer: :mod:`ai_rfc.experiment` imports from here,
and nothing here may import from ``ai_rfc.experiment``. The instrument was
split out over this package precisely so production and the three-arm
experiment share one spawn path rather than two that drift.
"""

from __future__ import annotations


class DriverError(RuntimeError):
    """A session could not be launched, read, or accounted for."""


def printable(text: str) -> str:
    """One line's worth of text, with anything that is not printable escaped.

    :meth:`str.isprintable` is the whole class in one test — False for every
    Cc, Cf, Cs, Co and Cn and for every separator but the plain space — and it
    is a predicate over the *category* rather than a list of characters
    somebody thought of. That distinction is this row's most repeated lesson:
    :func:`ai_rfc.driver.stop._quoted` shipped ``shlex.quote`` first and then
    C0+DEL, and both were necessary and insufficient.

    Escaped rather than refused. This runs on the stop path, where the line
    being printed *is* the diagnosis; raising here would replace the answer
    with a second failure. Each offending character becomes its own escape, so
    the damage is visible in the line instead of acting on it.

    It lives at the package root rather than in :mod:`~ai_rfc.driver.sweep`,
    where it was written, because :func:`ai_rfc.lifecycle.common.report` is
    the second stderr boundary that needs it and this module imports nothing:
    reaching it through ``sweep`` would have made every lifecycle verb —
    ``doctor``, ``config``, ``status`` — load the session-driving machinery to
    print one line (measured: 223 modules to 238).

    Args:
        text: The line.

    Returns:
        ``text`` when it is already printable, else the same line with every
        unprintable character escaped. A legitimate accented path is
        printable and is returned untouched.
    """
    if text.isprintable():
        return text
    return "".join(
        character if character.isprintable() else repr(character)[1:-1]
        for character in text
    )
