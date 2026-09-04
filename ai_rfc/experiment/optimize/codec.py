"""One string in, one plugin root out.

A search backend proposes text, not a directory tree, so the four skill
bodies under optimization travel as a single delimited string. Decoding it
is where the campaign is protected: a proposal that dropped a slot, grew a
frontmatter block or collapsed a skill to a sentence would still render, and
would quietly measure something other than the plugin. Every such proposal
is rejected here with the full list of what it broke, so the backend gets a
usable signal instead of one failure at a time.

The frontmatter of each skill stays outside the candidate. It names the
skill and states when to load it, which is harness wiring rather than the
prose being optimized.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .. import ExperimentError
from ..render import SLOT_RE, SLOT_TABLES, TEMPLATE, write_plugin_skill

SECTION_NAMES = ("loop", "evidence-hygiene", "interviewing", "rfc-style")
SECTION_HEADER = "<<<AI_RFC_SECTION {name}>>>"
SKILL_DIRS = {
    "evidence-hygiene": "ai-rfc-evidence-hygiene",
    "interviewing": "ai-rfc-interviewing",
    "rfc-style": "ai-rfc-rfc-style",
}
_LOOP_SKILL_DIR = "ai-rfc-reconstruction-loop"
_PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"

_HEADERS = {SECTION_HEADER.format(name=name): name for name in SECTION_NAMES}
_SECTION_MARK = SECTION_HEADER.split("{name}")[0].strip()
_FRONTMATTER_KEY = re.compile(r"^(name|description):")
_MIN_RATIO = 0.25
_MAX_RATIO = 2


@dataclass(frozen=True)
class Bundle:
    """The four texts a candidate carries, bodies only.

    Attributes:
        loop: The loop template, slots unrendered.
        evidence_hygiene: The evidence-hygiene skill's body.
        interviewing: The interviewing skill's body.
        rfc_style: The RFC-style skill's body.
    """

    loop: str
    evidence_hygiene: str
    interviewing: str
    rfc_style: str


class CodecError(ExperimentError):
    """Raised when a candidate fails one or more decode guards.

    Attributes:
        reasons: One stable string per failed guard, in guard order.
    """

    reasons: list[str]

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("candidate rejected: " + "; ".join(reasons))
        self.reasons = list(reasons)


def _body(bundle: Bundle, name: str) -> str:
    return getattr(bundle, name.replace("-", "_"))


def split_frontmatter(text: str) -> tuple[str, str]:
    """Cut a leading YAML frontmatter block off a skill file.

    Args:
        text: The file's contents.

    Returns:
        The frontmatter, fences and trailing blank lines included, and the
        body. Concatenating them returns ``text`` unchanged; a file without
        frontmatter yields an empty first element.
    """
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    body = text[end + len("\n---\n") :].lstrip("\n")
    return text[: len(text) - len(body)], body


def seed_from_plugin(plugin_root: Path) -> Bundle:
    """Read the bundle the optimizer starts from.

    Args:
        plugin_root: The plugin directory holding ``skills/``.

    Returns:
        The packaged loop template and the three prose skill bodies.
    """
    bodies = {
        section: split_frontmatter(
            (plugin_root / "skills" / directory / "SKILL.md").read_text()
        )[1]
        for section, directory in SKILL_DIRS.items()
    }
    return Bundle(
        loop=TEMPLATE.read_text(),
        evidence_hygiene=bodies["evidence-hygiene"],
        interviewing=bodies["interviewing"],
        rfc_style=bodies["rfc-style"],
    )


def frontmatters_from_plugin(plugin_root: Path) -> dict[str, str]:
    """Read the frontmatter each prose skill keeps across candidates.

    The loop skill is absent: its frontmatter is generated with the file
    itself, so no caller can supply one.

    Args:
        plugin_root: The plugin directory holding ``skills/``.

    Returns:
        Section name to frontmatter text, one entry per key of
        :data:`SKILL_DIRS`.
    """
    return {
        section: split_frontmatter(
            (plugin_root / "skills" / directory / "SKILL.md").read_text()
        )[0]
        for section, directory in SKILL_DIRS.items()
    }


def encode(bundle: Bundle) -> str:
    """Render a bundle as the one string a search backend proposes on.

    Args:
        bundle: The four bodies.

    Returns:
        Each section as its header line, a blank line and the body, in
        :data:`SECTION_NAMES` order.
    """
    sections = []
    for name in SECTION_NAMES:
        header = SECTION_HEADER.format(name=name)
        sections.append(header + "\n\n" + _body(bundle, name).strip("\n") + "\n")
    return "\n".join(sections)


def _split_sections(candidate: str, reasons: list[str]) -> dict[str, str]:
    lines = candidate.replace("\r\n", "\n").split("\n")
    marks: list[tuple[int, str | None]] = []
    for number, line in enumerate(lines, start=1):
        if not line.startswith(_SECTION_MARK):
            continue
        name = _HEADERS.get(line)
        if name is None:
            reasons.append(f"unknown section header on line {number}: {line}")
        marks.append((number - 1, name))
    counts = Counter(name for _, name in marks if name is not None)
    for name in SECTION_NAMES:
        if not counts[name]:
            reasons.append(f"{name}: missing section header")
        elif counts[name] > 1:
            reasons.append(f"{name}: section header repeated ({counts[name]} times)")
    bodies: dict[str, str] = {}
    for position, (index, name) in enumerate(marks):
        if name is None or counts[name] > 1:
            continue
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        bodies[name] = "\n".join(lines[index + 1 : end]).strip("\n") + "\n"
    return bodies


def _slot_reasons(loop: str) -> list[str]:
    found = SLOT_RE.findall(loop)
    reasons = [
        f"loop: unknown slot {{{{{name}}}}}"
        for name in sorted(set(found) - set(SLOT_TABLES["A"]))
    ]
    expected = Counter(SLOT_RE.findall(TEMPLATE.read_text()))
    counts = Counter(found)
    for name in sorted(expected - counts):
        reasons.append(f"loop: missing slot {{{{{name}}}}}")
    for name in sorted(counts - expected):
        if name in SLOT_TABLES["A"]:
            reasons.append(
                f"loop: slot {{{{{name}}}}} appears {counts[name]} times, "
                f"expected {expected[name]}"
            )
    return reasons


def _body_reasons(name: str, body: str, seed_body: str) -> list[str]:
    reasons = []
    if body.startswith("---"):
        reasons.append(f"{name}: body starts with a --- fence")
    for line in body.split("\n", 3)[:3]:
        key = _FRONTMATTER_KEY.match(line)
        if key is not None:
            reasons.append(
                f"{name}: frontmatter key {key.group(1)} in the first three lines"
            )
    low = int(_MIN_RATIO * len(seed_body))
    high = _MAX_RATIO * len(seed_body)
    if len(body) < low:
        reasons.append(
            f"{name}: shorter than {_MIN_RATIO}× seed ({len(body)} < {low} chars)"
        )
    elif len(body) > high:
        reasons.append(
            f"{name}: longer than {_MAX_RATIO}× seed ({len(body)} > {high} chars)"
        )
    if not body.strip():
        reasons.append(f"{name}: empty body")
    return reasons


def decode(candidate: str, *, seed: Bundle) -> Bundle:
    """Parse a proposed candidate back into a bundle, or reject it.

    Bodies come back with their surrounding blank lines dropped and exactly
    one trailing newline, which is the shape :func:`encode` writes and the
    shape the plugin's own files carry, so decoding an encoded bundle
    returns it unchanged.

    ``\\r\\n`` is normalized to ``\\n`` first and is the only rewriting done:
    every other byte survives, including a lone ``\\r`` and the control and
    Unicode characters that :meth:`str.splitlines` would treat as line
    breaks. A proposal is never silently reflowed, so a header line broken by
    one of those is reported rather than repaired.

    Args:
        candidate: The proposed string.
        seed: The bundle the campaign started from, whose body lengths bound
            how far a proposal may drift.

    Returns:
        The four bodies.

    Raises:
        CodecError: If any guard fails, naming every one that did.
    """
    reasons: list[str] = []
    bodies = _split_sections(candidate, reasons)
    if "loop" in bodies:
        reasons.extend(_slot_reasons(bodies["loop"]))
    for name in SECTION_NAMES:
        if name in bodies:
            reasons.extend(_body_reasons(name, bodies[name], _body(seed, name)))
    if reasons:
        raise CodecError(reasons)
    return Bundle(
        loop=bodies["loop"],
        evidence_hygiene=bodies["evidence-hygiene"],
        interviewing=bodies["interviewing"],
        rfc_style=bodies["rfc-style"],
    )


def materialize(
    bundle: Bundle,
    frontmatters: dict[str, str],
    dest: Path,
    *,
    source_plugin_root: Path,
) -> Path:
    """Write a candidate out as a plugin root a campaign can be pointed at.

    Everything under ``skills/`` that the candidate does not carry — the
    reference files, whole skills the candidate leaves alone — is copied
    byte for byte, so a materialized seed is indistinguishable from the
    source plugin.

    Args:
        bundle: The four bodies to write.
        frontmatters: Section name to frontmatter text, as returned by
            :func:`frontmatters_from_plugin`.
        dest: The plugin root to write; created if absent.
        source_plugin_root: The plugin the untouched files are copied from.

    Returns:
        ``dest``.
    """
    generated = {Path("skills") / _LOOP_SKILL_DIR / "SKILL.md"} | {
        Path("skills") / directory / "SKILL.md" for directory in SKILL_DIRS.values()
    }
    source_skills = source_plugin_root / "skills"
    for path in sorted(source_skills.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_plugin_root)
        if relative in generated:
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for section, directory in SKILL_DIRS.items():
        target = dest / "skills" / directory / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(frontmatters[section] + _body(bundle, section))
    (dest / "skills" / _LOOP_SKILL_DIR).mkdir(parents=True, exist_ok=True)
    write_plugin_skill(dest, template=bundle.loop)
    manifest = source_plugin_root / _PLUGIN_MANIFEST
    if manifest.is_file():
        (dest / _PLUGIN_MANIFEST).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest, dest / _PLUGIN_MANIFEST)
    return dest
