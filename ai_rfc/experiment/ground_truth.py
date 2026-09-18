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

from . import ExperimentError

DATASET = "aioquic-w02-11.yaml"
PACKAGE = "ai_rfc.experiment.groundtruth"

#: How far from a symbol a value-shaped token still counts as said about it,
#: in characters of the normalised text, measured from both ends of the
#: symbol. Not a guess, though the measurement under it moved when the
#: stream-type entries left the scored set: across the dataset's own
#: one-sentence statements the widest gap between a symbol and its value is 56
#: characters, and both statements that reach it are stream types this axis
#: now excludes (see :func:`score_draft`). Among the entries still scored the
#: widest gap is 20. 120 is kept rather than narrowed onto that smaller
#: figure, because those statements are the most compressed prose form each
#: claim has and a draft carries the subordinate clause they do not. It is
#: part of what a score *means* -- two drafts scored at different widths are
#: not comparable -- so the verb writes it into every report.
NEARBY_CHARS = 120

#: A hexadecimal token as a draft writes one. Keeping ``0x10`` out of ``0x10a``
#: is the greedy ``+`` and not the guards: with either guard removed, ``0x10a``
#: still yields ``0x10a`` whole. What the guards do is keep a hex-looking run
#: from being read out of an identifier -- ``frame0x10`` before it, ``0x10_bad``
#: after. The comparison is numeric on top of all that, so ``0x10a`` and
#: ``0x10A`` agree and ``0x10`` and ``0x10a`` do not.
_HEX_TOKEN = re.compile(r"(?<![0-9a-z_])0x[0-9a-f]+(?![0-9a-z_])")

#: A value spelled as a bare decimal numeral. A test on an entry's *value* and
#: never a search of a draft: a decimal value is excluded from scoring, so
#: nothing ever looks for one in prose. See :func:`score_draft` for why, and
#: :data:`EXCLUDED_DECIMAL` for what the report says about it.
_DECIMAL_VALUE = re.compile(r"\d+")

#: Why an entry could not be scored, as the report states it per entry. Both
#: are read off how the value is spelled, never off the entry's ``kind``.
EXCLUDED_NOT_A_NUMBER = "its value is not a number, and no prose draft spells one"
EXCLUDED_DECIMAL = (
    "its value is a bare decimal numeral, which prose does not distinguish "
    "from any other number that falls near the symbol"
)

#: What a coercion out of text costs, per field. The harm differs and the
#: message has to say which one it is: a coerced *value* goes on being scored,
#: against the wrong number in the wrong base; a coerced *symbol* is what the
#: draft is searched for, so the entry quietly stops matching any draft and
#: reads as a miss. Consulted by :func:`_text`.
_COERCION_HARM = {
    "value": (
        "a value coerced that way would be scored as a decimal claim rather "
        "than refused"
    ),
    "symbol": (
        "a symbol coerced that way would be searched for as that integer's "
        "digits, and the entry would read as a miss against every draft"
    ),
}


@dataclass(frozen=True)
class GroundTruthReport:
    """One draft's standing against the entries a dataset could score.

    Two partitions, one nested in the other. ``attempted`` and ``missed``
    partition the scored entries -- the ones the draft said something numeric
    about and the ones it never went near -- and ``matched`` and
    :attr:`mismatched` partition ``attempted`` in turn. ``excluded_because``
    is outside both: entries nothing could score, carried with the reason each
    one could not be, so that a reader never has to infer which denominator a
    ratio was taken over or why an entry is missing from it.

    Attributes:
        matched: Ids stated with the value the pinned source spells.
        attempted: Ids whose symbol appeared beside some value of the right
            shape, whether or not it was the right value.
        missed: Ids the draft did not attempt.
        excluded_because: One ``(id, reason)`` pair per entry no draft could
            be scored on, in dataset order. The reasons are not
            interchangeable: see :data:`EXCLUDED_NOT_A_NUMBER` and
            :data:`EXCLUDED_DECIMAL`.
    """

    matched: tuple[str, ...]
    attempted: tuple[str, ...]
    missed: tuple[str, ...]
    excluded_because: tuple[tuple[str, str], ...]

    @property
    def excluded(self) -> tuple[str, ...]:
        """The ids of the excluded entries alone, in dataset order.

        Derived rather than stored beside ``excluded_because``: two fields
        would be two things to keep in step, and an id present in one and
        absent from the other is exactly the drift this axis reports on.
        """
        return tuple(name for name, _ in self.excluded_because)

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

    A path that leaves the checkout is reported unresolved without being read.
    ``clone / "/etc/hosts"`` resolves to ``/etc/hosts``, which exists and is a
    file, and reading it to discover that its first line does not spell the
    assignment would make this a reader of arbitrary files on the strength of
    a dataset field. The packaged dataset's own suite already forbids an
    absolute path and a ``..`` segment; this is the second boundary, for the
    entries a caller assembles itself.

    Args:
        entries: The dataset's ``entries`` list, not the document around it.
        clone: The root of the checkout the paths are relative to.

    Returns:
        The ids that did not resolve, in the order the entries were given.
        Empty means every triple was found, which is the only claim this
        function makes: it says nothing about whether the RFC citation beside
        each entry is apt.

        A value YAML coerced out of text needs no guard here, unlike in
        :func:`score_draft`: ``256`` does not spell the line the source
        carries, so the entry is reported unresolved, which is true of it.
    """
    unresolved: list[str] = []
    root = clone.resolve()
    for entry in entries:
        path = (clone / entry["source"]["path"]).resolve()
        if not path.is_relative_to(root):
            unresolved.append(entry["id"])
            continue
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

    A symbol the draft wrote only inside a longer symbol *the dataset also
    carries* is not a naming of it: the ``SETTINGS`` of
    ``H3_MISSING_SETTINGS`` belongs to the error code, not to the SETTINGS
    frame. The disambiguation is read off the entries rather than off a list
    of prefixes kept here, because a list would be a second dataset to keep in
    step with the first and would go stale the first time an entry was added.

    What the predicate can discriminate: a different number. Tokens are
    compared as numbers, so ``0x10a`` and ``0x10A`` agree while ``0x100`` and
    ``0x1000`` do not -- the substring reading that a containing number
    satisfies a claim is exactly the defect the dataset's anchor test was
    written against.

    What it cannot discriminate, and no matcher over prose could:

    * **A longer name the dataset does not carry.** The disambiguation can
      only recognise a longer name some entry spells, so what it covers grows
      and shrinks with the dataset's own scope: a draft writing
      ``RECEIVED_SETTINGS``, or simply the words "received settings", still
      credits the ``SETTINGS`` frame type. A compound the dataset does not
      name is indistinguishable from prose that happens to run two words
      together.
    * **A claim the draft did not intend.** Proximity is a character window,
      not syntax: a symbol and a nearby number are scored as a statement
      about each other even when the sentence relates neither. This is the
      limitation the exclusion rule below is drawn against.
    * **A value spelled in the other base.** The shape comes from the entry's
      own value, which is the source's spelling, so a draft writing ``256``
      where the source spells ``0x100`` is read as having attempted nothing,
      not as having got it right.

    Which entries can be scored at all is read off how the value is spelled,
    never off the entry's ``kind``. A hexadecimal literal is scored: ``0x`` is
    the draft's own mark that the number is a protocol constant rather than a
    count, a date or a section number. Everything else is excluded, for one of
    two reasons the report keeps apart:

    * **Not a number**, a Python literal like ``["h3"]``. No prose draft
      spells one, so scoring it would bake a permanent miss into the metric,
      and giving it a hand-written "prose form" would put a claim where a
      measurement belongs.
    * **A bare decimal numeral.** Prose is full of digits and none of them
      carry a mark saying they are a constant, so the window cannot tell a
      claim from a coincidence. Before these were excluded, "The flow control
      window starts at 0." was scored as a correct statement of the control
      stream type and "The QPACK decoder stream was written on 2026-09-03."
      as one of the decoder's: recall was being paid for non-claims and the
      accuracy's denominator counted claims no draft had made.

    The cost of the second rule is stated rather than hidden. A draft that
    *does* state a decimal-valued entry correctly now earns nothing for it,
    and the four stream types the dataset carries today leave the denominator
    with it. That is the trade taken: a denominator of 39 with four entries
    scored by prose that makes no claim measures a draft less honestly than a
    denominator of 35 with none.

    Args:
        text: The draft's text, as written.
        entries: The dataset's ``entries`` list, not the document around it.

    Returns:
        The report, whose id tuples follow the order of ``entries``.

    Raises:
        ExperimentError: An entry's symbol or value is not text. Refused here
            rather than coerced, because a coercion is silent where it is most
            dangerous: YAML reads a bare ``0x100`` as the integer 256, and
            ``str(256)`` is a perfectly good decimal token, so the entry would
            go on being *scored* -- against the wrong number, in the wrong
            base, with nothing to read that says so.
    """
    body = _normalised(text)
    # Every entry's symbol, and deliberately not only the ones that will be
    # scored: an excluded entry still names a token a draft may write, whether
    # it was excluded for a value that is no number or for a decimal one, and
    # RESERVED_SETTINGS is precisely what says that the SETTINGS inside it is
    # not the SETTINGS frame. Compiled once, because the question each match
    # asks -- did the draft write a longer symbol here? -- is about the text,
    # not about the entry being scored.
    symbols = [_symbol_pattern(_text(entry, "symbol")) for entry in entries]
    named = _symbol_spans(body, symbols)
    matched: list[str] = []
    attempted: list[str] = []
    missed: list[str] = []
    excluded: list[tuple[str, str]] = []
    for entry, symbol in zip(entries, symbols):
        value = _text(entry, "value")
        shape = _value_shape(value)
        if shape is None:
            excluded.append((entry["id"], _why_not_scored(value)))
            continue
        token, base = shape
        tokens = _tokens_near(body, symbol, token, named)
        if not tokens:
            missed.append(entry["id"])
            continue
        attempted.append(entry["id"])
        wanted = int(value, base)
        if any(int(found, base) == wanted for found in tokens):
            matched.append(entry["id"])
    return GroundTruthReport(
        matched=tuple(matched),
        attempted=tuple(attempted),
        missed=tuple(missed),
        excluded_because=tuple(excluded),
    )


def _text(entry: dict, field: str) -> str:
    """One field of an entry, refused unless it is text.

    The dataset's own suite holds ``symbol`` and ``value`` to ``str`` and says
    why: an unquoted ``0x100`` loads as 256. That guard covers the packaged
    dataset, and this covers the boundary -- :func:`score_draft` takes a list
    of entries, which a caller may assemble itself.

    Args:
        entry: The entry to read.
        field: Which field to read.

    Returns:
        The field's text.

    Raises:
        ExperimentError: The field is not text, named with the entry, the
            field that was coerced and the harm that coercion does to *that*
            field rather than to whichever one the message happened to carry.
    """
    found = entry[field]
    if not isinstance(found, str):
        raise ExperimentError(
            f"{entry['id']}: {field} is of type {type(found).__name__}, not "
            "text; YAML reads a bare 0x100 as the integer 256, and "
            f"{_COERCION_HARM.get(field, 'the entry would be read wrong')}"
        )
    return found


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
    may *follow* the symbol, which is what keeps ``DATA`` out of
    ``H3_DATAGRAM`` and ``H3_DATAGRAM`` out of ``H3_DATAGRAM_ERROR``. But a
    separator may *precede* it, because the RFCs name every setting
    ``SETTINGS_X`` where aioquic's symbol is the bare ``X``: with a symmetric
    word boundary the five setting entries never match their own statements,
    which is five permanent misses against any draft that uses the RFC's
    names.

    The loose side is also what lets ``SETTINGS`` match inside
    ``H3_MISSING_SETTINGS``, and that is not repaired here -- tightening this
    boundary is the same edit as losing the five settings. It is repaired by
    :func:`_inside_a_longer_symbol`, which asks the dataset which symbol the
    token belongs to.

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

    Only a hexadecimal literal has a shape here. A bare decimal numeral is a
    number and still has none, because the shape is what the matcher searches
    prose with and prose does not distinguish a decimal constant from any
    other number; :func:`score_draft` records why at length.

    Args:
        value: The entry's value, as the source spells it.

    Returns:
        The pattern that finds tokens of that shape and the base to compare
        them in, or ``None`` when nothing in a draft's prose can be scored
        against the value. :func:`_why_not_scored` says which kind of
        ``None`` it is.
    """
    if _HEX_TOKEN.fullmatch(value.casefold()):
        return _HEX_TOKEN, 16
    return None


def _why_not_scored(value: str) -> str:
    """Which exclusion reason a value without a shape falls under.

    Only meaningful for a value :func:`_value_shape` returned ``None`` for.
    The two reasons are kept apart in the report because they are different
    facts about a dataset: one entry could never be scored by any matcher over
    prose, and the other could be scored by a matcher that read syntax rather
    than a character window.

    Args:
        value: The entry's value, as the source spells it.

    Returns:
        :data:`EXCLUDED_DECIMAL` or :data:`EXCLUDED_NOT_A_NUMBER`.
    """
    if _DECIMAL_VALUE.fullmatch(value.casefold()):
        return EXCLUDED_DECIMAL
    return EXCLUDED_NOT_A_NUMBER


def _symbol_spans(
    body: str, symbols: list[Pattern[str]]
) -> tuple[tuple[int, int], ...]:
    """Where every symbol a dataset names occurs in one draft.

    Located with the same patterns the scoring uses, so the separator
    tolerance that lets a draft write "missing settings" for
    ``H3_MISSING_SETTINGS`` also lets "missing settings" say which symbol its
    ``settings`` belongs to. A set, because two entries sharing a symbol would
    otherwise contribute the same span twice and neither would be a longer
    name than the other anyway.

    Args:
        body: The normalised draft.
        symbols: One pattern per entry, in any order.

    Returns:
        The spans found, without repeats and in no particular order.
    """
    spans: set[tuple[int, int]] = set()
    for pattern in symbols:
        for match in pattern.finditer(body):
            spans.add(match.span())
    return tuple(spans)


def _inside_a_longer_symbol(
    span: tuple[int, int], named: tuple[tuple[int, int], ...]
) -> bool:
    """Whether the draft wrote this match only as part of a longer symbol.

    The prefix side of :func:`_symbol_pattern` is loose on purpose and cannot
    be tightened without losing the five settings entries, so ``SETTINGS``
    matches inside ``H3_MISSING_SETTINGS``, inside ``H3_SETTINGS_ERROR`` and
    inside ``RESERVED_SETTINGS``. The token in those drafts belongs to the
    longer symbol, and crediting the shorter entry too inflates both ratios
    with a claim the draft never made.

    The rule is drawn from the dataset rather than from a list of prefixes
    written here: an identifier the draft wrote that is *itself* an entry's
    symbol names that entry. ``SETTINGS_MAX_FIELD_SECTION_SIZE`` is no entry's
    symbol, so it goes on crediting ``MAX_FIELD_SECTION_SIZE``, which is the
    asymmetry's whole purpose; ``H3_MISSING_SETTINGS`` is one, so it stops
    crediting the SETTINGS frame. A list would be a second dataset to maintain
    and would be wrong the first time an entry was added.

    Containment is strict in length, so a symbol never suppresses itself, and
    it is not applied recursively: a span that is itself suppressed for
    scoring still disambiguates, which is what lets ``RESERVED_SETTINGS`` --
    an entry no draft can be scored on -- speak for the ``SETTINGS`` it holds.

    Args:
        span: The match under consideration, as ``(start, end)``.
        named: Every span at which the dataset's symbols occur in the draft.

    Returns:
        ``True`` when some longer symbol of the dataset covers this match.
    """
    start, end = span
    return any(
        other_start <= start
        and end <= other_end
        and other_end - other_start > end - start
        for other_start, other_end in named
    )


def _tokens_near(
    body: str,
    symbol: Pattern[str],
    token: Pattern[str],
    named: tuple[tuple[int, int], ...],
) -> list[str]:
    """Every token of one shape lying near any mention of one symbol.

    The window reaches both ways, because a draft writes "0x100
    (H3_NO_ERROR)" as readily as the other order. Where a symbol is named
    several times the windows are unioned: one correct statement of a fact is
    a statement of it, however many times the draft names the symbol
    elsewhere.

    A mention the draft wrote only inside a longer symbol of the dataset is
    not a mention at all; see :func:`_inside_a_longer_symbol`.

    Args:
        body: The normalised draft.
        symbol: The symbol pattern to locate.
        token: The token pattern to collect.
        named: Every span at which the dataset's symbols occur in ``body``.

    Returns:
        The tokens found, with repeats, empty when the symbol is absent or
        nothing of that shape lies near it.
    """
    found: list[str] = []
    for match in symbol.finditer(body):
        if _inside_a_longer_symbol(match.span(), named):
            continue
        start = max(0, match.start() - NEARBY_CHARS)
        found += token.findall(body[start : match.end() + NEARBY_CHARS])
    return found
