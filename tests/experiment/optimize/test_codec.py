"""Tests for the candidate codec.

Every case runs against the real plugin under ``plugins/ai-rfc`` and the real
packaged loop template, because the codec's whole job is to survive a
round-trip through those exact files.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_rfc.experiment.optimize.codec import (
    SECTION_HEADER,
    SECTION_NAMES,
    SKILL_DIRS,
    Bundle,
    CodecError,
    decode,
    encode,
    frontmatters_from_plugin,
    materialize,
    seed_from_plugin,
    split_frontmatter,
)
from ai_rfc.experiment.render import TEMPLATE, arm_prompt


def _rejects(candidate: str, seed: Bundle) -> list[str]:
    with pytest.raises(CodecError) as caught:
        decode(candidate, seed=seed)
    return caught.value.reasons


def _files_under(root: Path, relative: str) -> set[Path]:
    return {
        path.relative_to(root)
        for path in (root / relative).rglob("*")
        if path.is_file()
    }


def test_split_frontmatter_is_lossless(plugin_root: Path) -> None:
    text = (plugin_root / "skills" / "ai-rfc-interviewing" / "SKILL.md").read_text()
    frontmatter, body = split_frontmatter(text)
    assert frontmatter + body == text
    assert frontmatter.startswith("---\n")
    assert frontmatter.rstrip("\n").endswith("---")
    assert body.startswith("# ")
    assert split_frontmatter(body) == ("", body)


def test_seed_reads_the_template_and_the_skill_bodies(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    assert seed.loop == TEMPLATE.read_text()
    for section, directory in SKILL_DIRS.items():
        text = (plugin_root / "skills" / directory / "SKILL.md").read_text()
        assert getattr(seed, section.replace("-", "_")) == split_frontmatter(text)[1]


def test_frontmatters_cover_the_three_prose_skills(plugin_root: Path) -> None:
    frontmatters = frontmatters_from_plugin(plugin_root)
    assert set(frontmatters) == set(SKILL_DIRS)
    for section, directory in SKILL_DIRS.items():
        assert f"name: {directory}\n" in frontmatters[section]


def test_encode_lays_the_sections_out_in_order(plugin_root: Path) -> None:
    text = encode(seed_from_plugin(plugin_root))
    headers = [line for line in text.splitlines() if "AI_RFC_SECTION" in line]
    assert headers == [SECTION_HEADER.format(name=name) for name in SECTION_NAMES]
    assert text.startswith(SECTION_HEADER.format(name="loop") + "\n\n# ")


def test_encode_is_byte_stable_and_decodes(plugin_root: Path) -> None:
    first = encode(seed_from_plugin(plugin_root))
    second = encode(seed_from_plugin(plugin_root))
    assert first == second
    assert decode(first, seed=seed_from_plugin(plugin_root)) == seed_from_plugin(
        plugin_root
    )


def test_round_trip_returns_the_seed(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    assert decode(encode(seed), seed=seed) == seed


def test_decode_rejects_a_missing_header(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    header = SECTION_HEADER.format(name="interviewing")
    candidate = encode(seed).replace(header + "\n", "", 1)
    assert "interviewing: missing section header" in _rejects(candidate, seed)


def test_decode_rejects_a_duplicate_header(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    header = SECTION_HEADER.format(name="loop")
    candidate = f"{encode(seed)}\n{header}\n\nfiller\n"
    assert "loop: section header repeated (2 times)" in _rejects(candidate, seed)


def test_decode_rejects_an_unknown_header(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    candidate = f"{encode(seed)}\n<<<AI_RFC_SECTION invented>>>\n\nfiller\n"
    reasons = _rejects(candidate, seed)
    assert any(
        reason.startswith("unknown section header on line ") for reason in reasons
    )


def test_decode_rejects_a_missing_slot(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    candidate = encode(replace(seed, loop=seed.loop.replace("{{gate}}", "", 1)))
    assert "loop: missing slot {{gate}}" in _rejects(candidate, seed)


def test_decode_rejects_an_invented_slot(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    loop = seed.loop.replace("{{gate}}", "{{frobnicate}}", 1)
    reasons = _rejects(encode(replace(seed, loop=loop)), seed)
    assert "loop: unknown slot {{frobnicate}}" in reasons
    assert "loop: missing slot {{gate}}" in reasons


def test_decode_rejects_a_duplicated_slot(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    loop = seed.loop.replace("{{gate}}", "{{gate}} {{gate}}", 1)
    reasons = _rejects(encode(replace(seed, loop=loop)), seed)
    assert "loop: slot {{gate}} appears 2 times, expected 1" in reasons


def test_decode_rejects_a_frontmatter_fence_in_a_body(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    body = f"---\nname: ai-rfc-interviewing\n---\n\n{seed.interviewing}"
    reasons = _rejects(encode(replace(seed, interviewing=body)), seed)
    assert "interviewing: body starts with a --- fence" in reasons
    assert "interviewing: frontmatter key name in the first three lines" in reasons


def test_decode_rejects_a_frontmatter_key_in_a_body(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    body = f"description: a skill\n\n{seed.rfc_style}"
    reasons = _rejects(encode(replace(seed, rfc_style=body)), seed)
    assert "rfc-style: frontmatter key description in the first three lines" in reasons


def test_decode_rejects_a_body_shorter_than_a_quarter_of_the_seed(
    plugin_root: Path,
) -> None:
    seed = seed_from_plugin(plugin_root)
    reasons = _rejects(encode(replace(seed, rfc_style="# Short\n")), seed)
    low = int(0.25 * len(seed.rfc_style))
    assert f"rfc-style: shorter than 0.25× seed (8 < {low} chars)" in reasons


def test_decode_rejects_a_body_longer_than_twice_the_seed(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    body = seed.evidence_hygiene * 3
    reasons = _rejects(encode(replace(seed, evidence_hygiene=body)), seed)
    high = 2 * len(seed.evidence_hygiene)
    assert (
        f"evidence-hygiene: longer than 2× seed ({len(body)} > {high} chars)" in reasons
    )


def test_decode_rejects_an_empty_body(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    reasons = _rejects(encode(replace(seed, interviewing="")), seed)
    assert "interviewing: empty body" in reasons


def test_decode_reports_every_failed_guard_at_once(plugin_root: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    candidate = encode(
        replace(seed, loop=seed.loop.replace("{{gate}}", "", 1), interviewing="")
    )
    reasons = _rejects(candidate, seed)
    assert "loop: missing slot {{gate}}" in reasons
    assert "interviewing: empty body" in reasons


def test_materialize_reproduces_the_plugin(plugin_root: Path, tmp_path: Path) -> None:
    seed = seed_from_plugin(plugin_root)
    dest = materialize(
        seed,
        frontmatters_from_plugin(plugin_root),
        tmp_path / "candidate",
        source_plugin_root=plugin_root,
    )
    assert dest == tmp_path / "candidate"
    assert _files_under(dest, "skills") == _files_under(plugin_root, "skills")
    for relative in _files_under(plugin_root, "skills"):
        assert (dest / relative).read_bytes() == (plugin_root / relative).read_bytes()
    assert (dest / ".claude-plugin" / "plugin.json").read_bytes() == (
        plugin_root / ".claude-plugin" / "plugin.json"
    ).read_bytes()
    assert sorted(path.name for path in dest.iterdir()) == [".claude-plugin", "skills"]


@pytest.mark.parametrize("profile", ["loop", "interview"])
def test_materialized_plugin_renders_the_same_arm_prompt(
    plugin_root: Path, tmp_path: Path, profile: str
) -> None:
    dest = materialize(
        seed_from_plugin(plugin_root),
        frontmatters_from_plugin(plugin_root),
        tmp_path / "candidate",
        source_plugin_root=plugin_root,
    )
    assert arm_prompt("A", dest, profile=profile) == arm_prompt(
        "A", plugin_root, profile=profile
    )


def test_materialize_writes_the_proposed_bodies(
    plugin_root: Path, tmp_path: Path
) -> None:
    seed = seed_from_plugin(plugin_root)
    proposed = replace(
        seed,
        rfc_style=seed.rfc_style.replace("# RFC prose", "# Proposed prose", 1),
        loop=seed.loop.replace("{{guidance}}", "{{guidance}} Proposed.", 1),
    )
    dest = materialize(
        proposed,
        frontmatters_from_plugin(plugin_root),
        tmp_path / "candidate",
        source_plugin_root=plugin_root,
    )
    style = (dest / "skills" / SKILL_DIRS["rfc-style"] / "SKILL.md").read_text()
    assert style.startswith("---\nname: ai-rfc-rfc-style\n")
    assert "# Proposed prose" in style
    loop = (dest / "skills" / "ai-rfc-reconstruction-loop" / "SKILL.md").read_text()
    assert "Proposed." in loop
    assert "{{" not in loop
    assert "# Proposed prose" in arm_prompt("A", dest)
