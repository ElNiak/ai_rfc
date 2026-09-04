"""Deterministic quality metrics and findings over one draft revision.

Everything here is a pure function of the draft text (plus the manifest for
citation coverage): no model, no network, no clock. The report separates the
*numbers* — what an instrument aggregates across runs — from the *findings* —
what an author fixes before tagging. The gate decides what a citation is; the
lint only measures, so the legacy ``a_rfc:`` spelling is counted but never
credited against the manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml

from ..models import Manifest
from .gate import CITATION

REPORT_FILE = "lint-report.json"
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Introduction",
    "Security Considerations",
    "IANA Considerations",
)
#: The skeleton's abstract sentence; a draft still carrying it was never written.
STUB_ABSTRACT_MARKER = (
    "Each revision reflects the implementation as it stood at one cluster"
)
BCP14_TERMS: tuple[str, ...] = (
    "MUST NOT",
    "SHALL NOT",
    "SHOULD NOT",
    "NOT RECOMMENDED",
    "MUST",
    "REQUIRED",
    "SHALL",
    "SHOULD",
    "RECOMMENDED",
    "MAY",
    "OPTIONAL",
)
MUST_FRACTION_CEILING = 0.8
MUST_FRACTION_FLOOR_COUNT = 20
FIGURE_CITATION_WINDOW = 3
LEGACY_CITATION = re.compile(r"`a_rfc:([^`\s]+)`")
_KEYWORD = re.compile(r"\b(" + "|".join(re.escape(t) for t in BCP14_TERMS) + r")\b")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_HEADING_ATTRS = re.compile(r"\s*\{[#:][^}]*\}\s*$")
_FENCE = re.compile(r"^(~~~+|```+)")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_ORDINAL = (
    r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|"
    r"eighteenth|nineteenth|twentieth|thirtieth|fortieth|fiftieth|sixtieth|"
    r"seventieth|eightieth|ninetieth|hundredth|[a-z]+-[a-z]+th|"
    r"[a-z]+-(first|second|third))"
)
NARRATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ordinal cluster",
        re.compile(r"\b" + _ORDINAL + r"\b[^.]{0,40}\bcluster\b", re.I),
    ),
    (
        "added/withdrawn count",
        re.compile(
            r"\b(statements?|claims?) (are|were|is|was) (added|withdrawn)\b", re.I
        ),
    ),
    ("cluster", re.compile(r"\bclusters?\b", re.I)),
    ("this revision", re.compile(r"\bthis revision\b", re.I)),
)


@dataclass(frozen=True)
class LintReport:
    """Numbers and findings for one draft text."""

    source: dict[str, str]
    sections: dict[str, list[str]]
    abstract: dict[str, Any]
    references: dict[str, int]
    keywords: dict[str, Any]
    blocks: dict[str, Any]
    citations: dict[str, Any]
    narration: list[dict[str, Any]]
    manifest_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def findings(self) -> tuple[str, ...]:
        """What an author must fix, one line each."""
        found: list[str] = []
        if self.manifest_error:
            found.append(f"manifest: unloadable ({self.manifest_error})")
        if self.abstract["is_stub"]:
            found.append("abstract: still the skeleton stub")
        for name in self.sections["missing"]:
            found.append(f"section missing: {name}")
        if self.references["normative"] + self.references["informative"] == 0:
            found.append(
                "references: none declared (normative and informative are both empty)"
            )
        for figure in self.blocks["figures_without_caption_citation"]:
            found.append(
                f"figure at line {figure['line']}: no citation within "
                f"{FIGURE_CITATION_WINDOW} lines of its closing fence"
            )
        for claim_id in self.citations["cited_unknown"]:
            found.append(f"citation {claim_id}: not in the manifest")
        if self.narration:
            lines = {entry["line"] for entry in self.narration}
            found.append(
                f"introduction: narrates the reconstruction ({len(lines)} "
                f"line(s), e.g. line {self.narration[0]['line']})"
            )
        total = self.keywords["total"]
        fraction = self.keywords["must_fraction"]
        if total >= MUST_FRACTION_FLOOR_COUNT and fraction > MUST_FRACTION_CEILING:
            found.append(
                f"keywords: MUST fraction {fraction:.2f} exceeds "
                f"{MUST_FRACTION_CEILING} over {total} keywords"
            )
        return tuple(found)

    def to_json(self) -> str:
        """Serialise deterministically, derived ``findings`` included."""
        payload = asdict(self)
        payload["findings"] = list(self.findings)
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


_SECTION_MARKERS = ("--- abstract", "--- middle", "--- back")


def _parts(text: str) -> dict[str, str]:
    """Split kramdown-rfc source into front matter, abstract, middle and back."""
    parts = {"front": "", "abstract": "", "middle": "", "back": ""}
    lines = text.splitlines()
    front_end = 0
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            marker = lines[index].strip()
            if marker == "---":
                front_end, body_start = index, index + 1
                break
            # kramdown-rfc also lets the first section marker close the front
            # matter, and that is what every draft this tool produces does: the
            # skeleton and the MARK draft both go straight from `---` to
            # `--- abstract`. Treating only a bare `---` as the terminator left
            # `front` empty and every reference count permanently zero.
            if marker in _SECTION_MARKERS:
                front_end, body_start = index, index
                break
        parts["front"] = "\n".join(lines[1:front_end]) + "\n"
    current = "preamble"
    buckets: dict[str, list[str]] = {
        "preamble": [],
        "abstract": [],
        "middle": [],
        "back": [],
    }
    for line in lines[body_start:]:
        marker = line.strip()
        if marker in _SECTION_MARKERS:
            current = marker.split()[1]
            continue
        buckets[current].append(line)
    for name in ("abstract", "middle", "back"):
        parts[name] = "\n".join(buckets[name]) + "\n"
    return parts


def _heading_title(match: re.Match[str]) -> str:
    """A heading match's title, with any kramdown attribute list stripped.

    ``# Introduction {#intro}`` must still read as ``"Introduction"``, or
    every anchored heading in a real xml2rfc-built draft looks like a missing
    section and the Introduction is never entered.
    """
    return _HEADING_ATTRS.sub("", match.group(2))


def _sections(body: str) -> dict[str, list[str]]:
    present = [
        _heading_title(match)
        for match in (_HEADING.match(line) for line in body.splitlines())
        if match and len(match.group(1)) == 1
    ]
    return {
        "present": present,
        "missing": [name for name in REQUIRED_SECTIONS if name not in present],
    }


def _references(front: str) -> dict[str, int]:
    try:
        loaded = yaml.safe_load(front) or {}
    except yaml.YAMLError:
        return {"normative": 0, "informative": 0, "inline": 0}
    counts = {"normative": 0, "informative": 0, "inline": 0}
    for kind in ("normative", "informative"):
        entries = loaded.get(kind) if isinstance(loaded, dict) else None
        if not isinstance(entries, dict):
            continue
        counts[kind] = len(entries)
        counts["inline"] += sum(
            1 for value in entries.values() if isinstance(value, dict)
        )
    return counts


def _prose_lines(body: str) -> list[tuple[int, str]]:
    """Body lines outside fences, comments and directives, with 1-based numbers."""
    kept: list[tuple[int, str]] = []
    fenced = False
    commented = False
    for number, line in enumerate(body.splitlines(), start=1):
        stripped = line.lstrip()
        if _FENCE.match(line):
            fenced = not fenced
            continue
        # kramdown-rfc drops comment blocks, so their bodies never reach the
        # built draft and must not be linted. Skipping only lines starting
        # `{::` left both the body and the `{:/comment}` closer in the prose.
        if stripped.startswith("{::comment}"):
            commented = True
            continue
        if stripped.startswith("{:/comment}"):
            commented = False
            continue
        if fenced or commented or stripped.startswith("{::"):
            continue
        kept.append((number, line))
    return kept


def _keywords(body: str) -> dict[str, Any]:
    histogram: dict[str, int] = {}
    for _, line in _prose_lines(body):
        for match in _KEYWORD.finditer(line):
            histogram[match.group(1)] = histogram.get(match.group(1), 0) + 1
    total = sum(histogram.values())
    must = histogram.get("MUST", 0) + histogram.get("MUST NOT", 0)
    return {
        "histogram": dict(sorted(histogram.items())),
        "total": total,
        "must_fraction": round(must / total, 4) if total else 0.0,
    }


def _blocks(body: str, offset: int) -> dict[str, Any]:
    lines = body.splitlines()
    figures = 0
    uncited: list[dict[str, Any]] = []
    tables = 0
    opened: int | None = None
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            if opened is None:
                opened = index
                continue
            figures += 1
            start = index + 1
            stop = start + FIGURE_CITATION_WINDOW
            following = lines[start:stop]
            window = "\n".join(following)
            if not CITATION.search(window):
                uncited.append({"line": offset + opened + 1})
            opened = None
            continue
        if (
            opened is None
            and index > 0
            and _TABLE_RULE.match(line)
            and lines[index - 1].lstrip().startswith("|")
        ):
            tables += 1
    return {
        "figures": figures,
        "tables": tables,
        "figures_without_caption_citation": uncited,
    }


def _citations(body: str, manifest: Manifest | None) -> dict[str, Any]:
    tokens = CITATION.findall(body)
    legacy = LEGACY_CITATION.findall(body)
    distinct = sorted(set(tokens))
    result: dict[str, Any] = {
        "tokens": len(tokens),
        "distinct": len(distinct),
        "legacy_tokens": len(legacy),
        "cited_unknown": [],
        "uncited": [],
        "cited_fraction": None,
    }
    if manifest is not None:
        known = {claim.id for claim in manifest.claims}
        cited = set(distinct)
        result["cited_unknown"] = sorted(cited - known)
        result["uncited"] = sorted(known - cited)
        result["cited_fraction"] = (
            round(len(cited & known) / len(known), 4) if known else None
        )
    return result


def _introduction(body: str) -> list[tuple[int, str]]:
    """The Introduction section's prose lines, with numbers relative to the body."""
    inside = False
    kept: list[tuple[int, str]] = []
    for number, line in _prose_lines(body):
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 1:
            inside = _heading_title(match) == "Introduction"
            continue
        if inside:
            kept.append((number, line))
    return kept


def _narration(body: str, offset: int) -> list[dict[str, Any]]:
    """Every narration pattern each Introduction line matches.

    A single sentence can carry more than one tell (an ordinal-cluster
    reference and an added/withdrawn count both land on one unwrapped line),
    so each specific pattern is checked independently rather than stopping at
    the first hit. The generic ``cluster`` pattern is not independent,
    though: it is guaranteed to co-fire wherever ``ordinal cluster`` does
    (that pattern requires the word "cluster"), so counting it unconditionally
    would double-report the same tell under two names. It is recorded only as
    a fallback, when nothing more specific matched the line.
    """
    entries: list[dict[str, Any]] = []
    for number, line in _introduction(body):
        specific: list[dict[str, Any]] = []
        fallback: dict[str, Any] | None = None
        for name, pattern in NARRATION_PATTERNS:
            if not pattern.search(line):
                continue
            entry = {"line": offset + number, "pattern": name, "text": line.strip()}
            if name == "cluster":
                fallback = entry
            else:
                specific.append(entry)
        if specific:
            entries.extend(specific)
        elif fallback is not None:
            entries.append(fallback)
    return entries


def lint(
    text: str,
    *,
    manifest: Manifest | None = None,
    manifest_error: str | None = None,
    source: dict[str, str] | None = None,
) -> LintReport:
    """Measure one draft text.

    Args:
        text: The kramdown-rfc source of the draft file.
        manifest: The manifest to measure citation coverage against, if any.
        manifest_error: Why the manifest could not be loaded, when it could not;
            reported as a finding rather than raised, because the draft is
            still worth measuring.
        source: Provenance for the report (``path``, ``ref``); the text digest
            is added here.

    Returns:
        The report.
    """
    parts = _parts(text)
    middle_offset = (
        text.count("\n", 0, text.index("--- middle") + 1) + 1
        if "--- middle" in text
        else 0
    )
    # `_parts` drops the `--- back` marker line itself; re-inserting the one
    # line it took keeps every line after it aligned with its true physical
    # number in `text` (`parts["middle"]` always ends with exactly one
    # newline, so this adds exactly one blank line, not two).
    body = parts["middle"] + "\n" + parts["back"]
    # A `{::comment}` block that explains the stub marker by quoting it
    # verbatim must not itself keep the abstract flagged: kramdown-rfc drops
    # comments at render time, so the abstract must be measured the same way
    # the body already is.
    abstract_text = "\n".join(line for _, line in _prose_lines(parts["abstract"]))
    abstract_text = abstract_text.strip()
    provenance = dict(source or {})
    provenance["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return LintReport(
        source=provenance,
        sections=_sections(body),
        abstract={
            # A real draft is hard-wrapped by xml2rfc at RFC line width, so the
            # marker's own spaces can fall on a line break and become
            # newlines; joining on whitespace before the substring check is
            # what lets this survive wrapping instead of only ever matching
            # the unwrapped test fixture.
            "is_stub": STUB_ABSTRACT_MARKER in " ".join(abstract_text.split()),
            "word_count": len(abstract_text.split()),
        },
        references=_references(parts["front"]),
        keywords=_keywords(parts["middle"]),
        blocks=_blocks(body, middle_offset),
        citations=_citations(body, manifest),
        narration=_narration(parts["middle"], middle_offset),
        manifest_error=manifest_error,
    )
