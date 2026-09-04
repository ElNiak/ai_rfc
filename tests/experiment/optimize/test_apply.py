"""Writing a candidate back into the plugin, and showing what it changed.

Every test here writes into a copy of the plugin and a copy of the loop
template. Pointing one at the packaged paths would rewrite the repository's
own skills, which is the thing the verb exists to let a person review first.
"""

import dataclasses
import shutil

import pytest

from ai_rfc.experiment.optimize.apply import apply, diff_stat
from ai_rfc.experiment.optimize.codec import (
    SKILL_DIRS,
    CodecError,
    encode,
    frontmatters_from_plugin,
    seed_from_plugin,
)
from ai_rfc.experiment.render import SKILL_FRONTMATTER, TEMPLATE, render_loop
from ai_rfc.server.testing import git

LOOP_SKILL = "skills/ai-rfc-reconstruction-loop/SKILL.md"


def _snapshot(*roots):
    """Every file under each root, by path, as bytes."""
    files = {}
    for root in roots:
        paths = sorted(root.rglob("*")) if root.is_dir() else [root]
        for path in paths:
            if path.is_file():
                files[path] = path.read_bytes()
    return files


def _skill(plugin_root, section):
    return plugin_root / "skills" / SKILL_DIRS[section] / "SKILL.md"


@pytest.fixture
def plugin_copy(tmp_path, plugin_root):
    """The real plugin, copied so a test may write into it."""
    copy = tmp_path / "plugin"
    shutil.copytree(plugin_root, copy)
    return copy


@pytest.fixture
def template_copy(tmp_path):
    """The packaged loop template, copied so no test writes the real one."""
    path = tmp_path / "loop.tmpl.md"
    path.write_text(TEMPLATE.read_text())
    return path


def test_applying_the_seed_encoding_changes_not_one_byte(plugin_copy, template_copy):
    """The packaged texts are already the shape the codec writes back.

    If they were not, applying the seed would open a diff nobody proposed,
    and every candidate's diff would carry that noise on top of its own
    change.
    """
    before = _snapshot(plugin_copy, template_copy)

    applied = apply(
        encode(seed_from_plugin(plugin_copy)), plugin_copy, template_path=template_copy
    )

    assert _snapshot(plugin_copy, template_copy) == before
    assert applied.rendered_skill == plugin_copy / LOOP_SKILL
    assert set(applied.written) == {
        _skill(plugin_copy, "evidence-hygiene"),
        _skill(plugin_copy, "interviewing"),
        _skill(plugin_copy, "rfc-style"),
        template_copy,
        plugin_copy / LOOP_SKILL,
    }


def test_a_changed_body_and_loop_land_and_the_skill_is_re_rendered(
    plugin_copy, template_copy
):
    """The two files the candidate changed, and nothing else.

    The loop skill is generated rather than written, so what proves the
    template landed is the rendering: the committed file is pinned to
    exactly this expression by ``tests/experiment/test_render.py``.
    """
    seed = seed_from_plugin(plugin_copy)
    loop = seed.loop + "\nHold the window open until the cluster is tagged.\n"
    rfc_style = seed.rfc_style + "\nSpell every keyword in full capitals.\n"
    candidate = encode(dataclasses.replace(seed, loop=loop, rfc_style=rfc_style))
    frontmatters = frontmatters_from_plugin(plugin_copy)
    untouched = _snapshot(
        _skill(plugin_copy, "evidence-hygiene"), _skill(plugin_copy, "interviewing")
    )

    applied = apply(candidate, plugin_copy, template_path=template_copy)

    assert template_copy.read_text() == loop
    assert (
        _skill(plugin_copy, "rfc-style").read_text()
        == frontmatters["rfc-style"] + rfc_style
    )
    assert applied.rendered_skill.read_text() == SKILL_FRONTMATTER + render_loop(
        "interactive", template=loop
    )
    assert (
        _snapshot(
            _skill(plugin_copy, "evidence-hygiene"), _skill(plugin_copy, "interviewing")
        )
        == untouched
    )


def test_the_frontmatter_a_candidate_proposes_no_name_for_survives(
    plugin_copy, template_copy
):
    """A candidate carries bodies only, so every skill keeps its own header."""
    seed = seed_from_plugin(plugin_copy)
    before = frontmatters_from_plugin(plugin_copy)
    candidate = encode(
        dataclasses.replace(seed, interviewing=seed.interviewing + "\nAsk once.\n")
    )

    apply(candidate, plugin_copy, template_path=template_copy)

    assert frontmatters_from_plugin(plugin_copy) == before
    assert all(text.startswith("---\n") for text in before.values())


def test_a_rejected_candidate_raises_and_writes_nothing(plugin_copy, template_copy):
    """Guarding before the first write is what makes the verb safe to try."""
    before = _snapshot(plugin_copy, template_copy)
    candidate = encode(seed_from_plugin(plugin_copy)).replace(
        "<<<AI_RFC_SECTION rfc-style>>>", "## Notes"
    )

    with pytest.raises(CodecError) as error:
        apply(candidate, plugin_copy, template_path=template_copy)

    assert "rfc-style: missing section header" in error.value.reasons
    assert _snapshot(plugin_copy, template_copy) == before


def test_diff_stat_names_the_files_a_candidate_changed(tmp_path, plugin_root):
    repo = tmp_path / "repo"
    shutil.copytree(plugin_root, repo / "plugins" / "ai-rfc")
    template = repo / "prompts" / "loop.tmpl.md"
    template.parent.mkdir(parents=True)
    template.write_text(TEMPLATE.read_text())
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "plugin")
    plugin_copy = repo / "plugins" / "ai-rfc"
    seed = seed_from_plugin(plugin_copy)
    candidate = encode(
        dataclasses.replace(seed, rfc_style=seed.rfc_style + "\nBe terse.\n")
    )

    applied = apply(candidate, plugin_copy, template_path=template)
    stat = diff_stat(plugin_copy, applied.written)

    assert "skills/ai-rfc-rfc-style/SKILL.md" in stat
    assert "1 file changed" in stat


def test_diff_stat_reports_git_refusing_rather_than_raising(tmp_path):
    """A written candidate must still be reported when there is no repository.

    ``apply`` has already changed the working tree by the time the diff is
    asked for, so a failure here is something to print, not something to
    raise over a change that did land.
    """
    outside = tmp_path / "loose"
    outside.mkdir()
    (outside / "SKILL.md").write_text("body\n")

    stat = diff_stat(outside, [outside / "SKILL.md"])

    assert "not a git repository" in stat.lower()
