"""Source hygiene this repository holds to, checked rather than remembered.

``tests/substrate/test_cli_conventions.py`` asserts the conventions the *entry
points* hold to, over a file list that deliberately excludes ``server`` and
``experiment`` and never looks at ``tests/`` at all. This asks a different
question — is the source spelled safely — over everything, so it is a module
of its own rather than a third scope smuggled into that one.
"""

import pathlib
import unicodedata

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Both trees. The hazard is a Python string literal's *spelling*, so this is
#: every ``.py`` the repository owns and nothing else: an invisible character
#: inside a JSON fixture is data, and data is what these tests are for.
ROOTS = ("ai_rfc", "tests")

#: The one character :meth:`str.isprintable` refuses that source legitimately
#: contains. Black emits none, but a hand-edited file may, and a tab is
#: neither invisible nor reordering.
ALLOWED = "\t"


def _sources() -> list[pathlib.Path]:
    """Every Python file in the repository's own trees."""
    return [
        path
        for root in ROOTS
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def test_no_source_file_carries_an_unprintable_character():
    """Invisible characters belong in source as escapes, never as themselves.

    A raw ``U+2028`` or ``U+202E`` in a source file cannot be seen in an
    editor, in a diff or in a review, and it is not merely ugly:
    :meth:`str.splitlines` treats the separators as line breaks and
    RIGHT-TO-LEFT OVERRIDE reorders everything after it on the rendered line.
    A test that *carries* one where it meant to *name* one therefore stops
    testing what it says it tests, with nothing to see. Nineteen of them were
    found across five files and three separate rows, every file a test of
    exactly this escaping.

    **The predicate, not a list.** :meth:`str.isprintable` is False for every
    Cc, Cf, Cs, Co and Cn and for every separator but the plain space — the
    same test :func:`ai_rfc.driver.printable` applies to values at runtime,
    applied here to the source that names them. The first draft of this check
    enumerated ``U+2028`` and ``U+2029``; it passed while ``U+0085``,
    ``U+200B`` and ``U+202E`` sat in the very files it had just cleaned. That
    is this row's most repeated lesson arriving one more time, and it is the
    argument for the predicate written as code rather than as a comment.

    **The cause is a tool, not carelessness**, which is why this is a check
    and not a note: writing a unicode escape through an assistant's editing
    tool normalises it back into the one character it names. Anyone fixing a
    hit here must write the replacement from a script that builds the escape
    by concatenation, or watch the correct spelling silently become the wrong
    one again and conclude that the escape "does not work".

    The scan splits on ``\n`` rather than calling
    :meth:`str.splitlines`, because ``splitlines`` breaks on several of the
    characters being looked for and would report the wrong line for each.
    """
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.split("\n"), 1):
            for character in line:
                if character.isprintable() or character in ALLOWED:
                    continue
                name = unicodedata.name(character, "unnamed")
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{number}: raw "
                    f"U+{ord(character):04X} {name} — write the escape "
                    f"\\u{ord(character):04x} instead"
                )
    assert not offenders, "\n".join(["unprintable characters in source:", *offenders])
