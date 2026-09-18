"""The ground-truth axis: claims checkable against pinned source.

The judge scores a draft by asking a model what it thinks. This axis does not
ask anything: each dataset entry names a file, a line and a value in a pinned
checkout, so a claim either matches the source or it does not.
"""

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Pattern

import yaml

DATASET = "aioquic-w02-11.yaml"
PACKAGE = "ai_rfc.experiment.groundtruth"

#: How far from a symbol a value-shaped token still counts as said about it,
#: in characters of the normalised text, measured from both ends of the
#: symbol. Not a guess: across the 39 scored entries the widest gap between a
#: symbol and its value in the dataset's own statement is 56 characters
#: (``h3-stream-control``, "a unidirectional stream type of 0"), and those
#: statements are the most compressed prose form each claim has. 120 is a
#: little over twice that, which leaves a draft room for the subordinate
#: clause the dataset's one-sentence form does not carry. It is part of what
#: a score *means* -- two drafts scored at different widths are not
#: comparable -- so the verb writes it into every report.
NEARBY_CHARS = 120

#: A hexadecimal token as a draft writes one. The surrounding guards keep
#: ``0x10`` out of ``0x10a`` and out of an identifier that merely ends in a
#: hex-looking run; the comparison is numeric on top of that, so ``0x10a`` and
#: ``0x10A`` agree and ``0x10`` and ``0x10a`` do not.
_HEX_TOKEN = re.compile(r"(?<![0-9a-z_])0x[0-9a-f]+(?![0-9a-z_])")

#: A decimal token as a draft writes one. ``/`` is excluded before it so that
#: the 3 of "HTTP/3" is a protocol's name and not a stream type, and a
#: following ``.<digit>`` is excluded so that the 8 of "Section 8.1" is not a
#: value either. A trailing ``.`` that no digit follows is kept, because three
#: of the four stream-type claims end their sentence on the value.
_INT_TOKEN = re.compile(r"(?<![0-9a-z_./])\d+(?!\.?\d)(?![0-9a-z_])")


@dataclass(frozen=True)
class GroundTruthReport:
    """One draft's standing against the entries a dataset could score.

    The three id tuples partition the scored entries: ``missed`` are the ones
    the draft never went near, ``attempted`` the ones it said something
    numeric about, and ``matched`` the subset of those it got right.
    ``excluded`` is outside that partition -- entries nothing could score --
    and it is carried so that a reader never has to infer which denominator a
    ratio was taken over.

    Attributes:
        matched: Ids stated with the value the pinned source spells.
        attempted: Ids whose symbol appeared beside some value of the right
            shape, whether or not it was the right value.
        missed: Ids the draft did not attempt.
        excluded: Ids no draft could be scored on, in dataset order.
    """

    matched: tuple[str, ...]
    attempted: tuple[str, ...]
    missed: tuple[str, ...]
    excluded: tuple[str, ...]

    @property
    def scored(self) -> int:
        """How many entries the draft was scored against."""
        return len(self.attempted) + len(self.missed)

    @property
    def mismatched(self) -> tuple[str, ...]:
        """Attempted ids whose stated value is not the one the source spells."""
        matched = set(self.matched)
        return tuple(name for name in self.attempted if name not in matched)

    @property
    def recall(self) -> float | None:
        """Matched over scored, or ``None`` when there was nothing to score.

        A zero here is a measurement: entries were looked for and not found.
        ``None`` is the other thing -- an empty dataset scores no draft, and
        reporting that as 0.0 would say the draft failed a test nobody set.
        """
        return len(self.matched) / self.scored if self.scored else None

    @property
    def claim_accuracy(self) -> float | None:
        """Matched over attempted, or ``None`` when nothing was attempted.

        Never 0.0 and never 1.0 for a draft that made no checkable claim: its
        accuracy is *unmeasured*, and both of those numbers are verdicts on
        claims that were never made.

        Not called precision. ``JUDGE_DIMENSIONS`` already grades a 1-to-5
        dimension by that name, meaning "does this prose specify
        unambiguously", and two different scales under one word in one
        experiment's output is a confusion this axis is meant to avoid.
        """
        if not self.attempted:
            return None
        return len(self.matched) / len(self.attempted)


def load_dataset(name: str = DATASET) -> dict[str, Any]:
    """Read a ground-truth dataset out of the installed package.

    The dataset is read through :mod:`importlib.resources` rather than a path
    relative to this file, so it resolves the same way from a wheel as from a
    source checkout.

    Args:
        name: File name of the dataset within the ``groundtruth`` package.

    Returns:
        The dataset document: ``repository`` and ``commit`` identify the
        checkout the anchors were read from, and ``entries`` holds the claims.
    """
    text = resources.files(PACKAGE).joinpath(name).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def resolve_anchors(entries: list[dict], clone: Path) -> tuple[str, ...]:
    """The entries whose anchor the given checkout does not bear.

    An anchor resolves when the cited file exists, the cited line is within
    it, and that line spells exactly ``SYMBOL = VALUE``. Equality rather than
    two substring checks, for the reason the dataset's own anchor test
    records: ``0x10`` is contained in ``H3_MISSING_SETTINGS = 0x10A``, so a
    wrong value would pass a substring check.

    Args:
        entries: The dataset's ``entries`` list, not the document around it.
        clone: The root of the checkout the paths are relative to.

    Returns:
        The ids that did not resolve, in the order the entries were given.
        Empty means every triple was found, which is the only claim this
        function makes: it says nothing about whether the RFC citation beside
        each entry is apt.
    """
    unresolved: list[str] = []
    for entry in entries:
        path = clone / entry["source"]["path"]
        if not path.is_file():
            unresolved.append(entry["id"])
            continue
        lines = path.read_text().splitlines()
        line = entry["source"]["line"]
        if not 1 <= line <= len(lines):
            unresolved.append(entry["id"])
            continue
        if lines[line - 1].strip() != f"{entry['symbol']} = {entry['value']}":
            unresolved.append(entry["id"])
    return tuple(unresolved)


def score_draft(text: str, entries: list[dict]) -> GroundTruthReport:
    """Score a draft's prose against the entries a dataset can check.

    An entry is *attempted* when the draft names its symbol and, within
    :data:`NEARBY_CHARS` characters either side of that name, writes a token
    shaped like the entry's value. It is *matched* when one of those tokens
    equals the value numerically. Anything else is *missed*.

    What the predicate can discriminate: a different number. Tokens are
    compared as numbers, so ``0x10a`` and ``0x10A`` agree while ``0x100`` and
    ``0x1000`` do not -- the substring reading that a containing number
    satisfies a claim is exactly the defect the dataset's anchor test was
    written against.

    What it cannot discriminate, and no matcher over prose could:

    * **Two claims that share a value.** "H3 DATAGRAM ERROR is 0x33" names
      the ``H3_DATAGRAM`` setting as well, because the separator tolerance
      that reads "H3 SETTINGS ERROR" as ``H3_SETTINGS_ERROR`` also reads
      ``H3_DATAGRAM`` out of it, and both are 0x33. Spelled with underscores
      the two are distinct, because a symbol followed by more of an
      identifier does not match.
    * **A claim the draft did not intend.** Proximity is a character window,
      not syntax: a symbol and a nearby number are scored as a statement
      about each other even when the sentence relates neither.
    * **A value spelled in the other base.** The shape comes from the entry's
      own value, so a draft writing ``0x02`` for a stream type whose value is
      ``2`` is read as having attempted nothing, not as having got it wrong.

    An entry whose value is not a number -- a Python literal like ``["h3"]``
    -- is excluded rather than scored. No prose draft spells one, so scoring
    it would bake a permanent miss into the metric, and giving it a
    hand-written "prose form" would put a claim where a measurement belongs.
    The rule reads the value, not the entry's ``kind``: a constant whose value
    is a number is scored like any other claim.

    Args:
        text: The draft's text, as written.
        entries: The dataset's ``entries`` list, not the document around it.

    Returns:
        The report, whose id tuples follow the order of ``entries``.
    """
    body = _normalised(text)
    matched: list[str] = []
    attempted: list[str] = []
    missed: list[str] = []
    excluded: list[str] = []
    for entry in entries:
        shape = _value_shape(entry["value"])
        if shape is None:
            excluded.append(entry["id"])
            continue
        token, base = shape
        tokens = _tokens_near(body, _symbol_pattern(entry["symbol"]), token)
        if not tokens:
            missed.append(entry["id"])
            continue
        attempted.append(entry["id"])
        wanted = int(entry["value"], base)
        if any(int(found, base) == wanted for found in tokens):
            matched.append(entry["id"])
    return GroundTruthReport(
        matched=tuple(matched),
        attempted=tuple(attempted),
        missed=tuple(missed),
        excluded=tuple(excluded),
    )


def _normalised(text: str) -> str:
    """Case folded, with runs of whitespace collapsed to one space.

    Deliberately not :func:`~.judge._normalised`, which collapses whitespace
    and stops there. That one serves quote verification, where the case a
    judge quoted is the thing being checked and folding it would let a
    misquotation verify. Here the draft is prose to search rather than text to
    hold to, and a draft writing ``h3_no_error`` states the same fact as one
    writing ``H3_NO_ERROR``. Importing it would also tie this verb to the
    module that owns the model transport, and this verb calls no model.

    Args:
        text: The draft's text, as written.

    Returns:
        The text to search, whose character offsets the window is measured in.
    """
    return " ".join(text.casefold().split())


def _symbol_pattern(symbol: str) -> Pattern[str]:
    """A symbol as a draft may spell it: ``_``, ``-`` or a space between words.

    The boundaries are asymmetric, and measurably so. Nothing identifier-like
    may *follow* the symbol, which is what keeps ``PUSH`` out of
    ``PUSH_PROMISE`` and ``H3_DATAGRAM`` out of ``H3_DATAGRAM_ERROR``. But a
    separator may *precede* it, because the RFCs name every setting
    ``SETTINGS_X`` where aioquic's symbol is the bare ``X``: with a symmetric
    word boundary the five setting entries never match their own statements,
    which is five permanent misses against any draft that uses the RFC's
    names.

    Args:
        symbol: The entry's symbol, as the source spells it.

    Returns:
        A compiled pattern to search the normalised text with.
    """
    words = [re.escape(word.casefold()) for word in re.split(r"[_\-\s]+", symbol)]
    return re.compile(
        r"(?<![0-9a-z])"
        + r"[\s_\-]+".join(word for word in words if word)
        + r"(?![0-9a-z_])"
    )


def _value_shape(value: str) -> tuple[Pattern[str], int] | None:
    """The token shape and base a value is written in, or ``None``.

    Read off the value itself rather than off the entry's ``kind``: ``kind``
    is an enumeration, and a shape derived from one is a second list to keep
    in step with the first.

    Args:
        value: The entry's value, as the source spells it.

    Returns:
        The pattern that finds tokens of that shape and the base to compare
        them in, or ``None`` when the value is not a number and so nothing a
        draft's prose can be scored against.
    """
    folded = value.casefold()
    if _HEX_TOKEN.fullmatch(folded):
        return _HEX_TOKEN, 16
    if _INT_TOKEN.fullmatch(folded):
        return _INT_TOKEN, 10
    return None


def _tokens_near(body: str, symbol: Pattern[str], token: Pattern[str]) -> list[str]:
    """Every token of one shape lying near any mention of one symbol.

    The window reaches both ways, because a draft writes "0x100
    (H3_NO_ERROR)" as readily as the other order. Where a symbol is named
    several times the windows are unioned: one correct statement of a fact is
    a statement of it, however many times the draft names the symbol
    elsewhere.

    Args:
        body: The normalised draft.
        symbol: The symbol pattern to locate.
        token: The token pattern to collect.

    Returns:
        The tokens found, with repeats, empty when the symbol is absent or
        nothing of that shape lies near it.
    """
    found: list[str] = []
    for match in symbol.finditer(body):
        start = max(0, match.start() - NEARBY_CHARS)
        found += token.findall(body[start : match.end() + NEARBY_CHARS])
    return found
