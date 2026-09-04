"""Writing a candidate back into the plugin, and showing what it changed.

Every test here writes into a copy of the plugin and a copy of the loop
template. Pointing one at the packaged paths would rewrite the repository's
own skills, which is the thing the verb exists to let a person review first.
"""

import dataclasses
import shutil

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.optimize.apply import (
    apply,
    by_repository,
    diff_stat,
    dirty_paths,
    repo_root,
    targets,
    uncommitted_work,
)
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


def _commit(repo):
    """Initialize ``repo`` and commit everything already in it."""
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "committed")
    return repo


@pytest.fixture
def plugin_repo(tmp_path, plugin_root):
    """A committed repository holding a plugin copy and a loop template.

    Returns:
        The repository, the plugin inside it, and the template beside it.
    """
    repo = tmp_path / "repo"
    shutil.copytree(plugin_root, repo / "plugins" / "ai-rfc")
    template = repo / "prompts" / "loop.tmpl.md"
    template.parent.mkdir(parents=True)
    template.write_text(TEMPLATE.read_text())
    _commit(repo)
    return repo, repo / "plugins" / "ai-rfc", template


@pytest.fixture
def split_repos(tmp_path, plugin_root):
    """A plugin and its loop template committed in two separate repositories.

    This is the ordinary shape, not a corner case: the template ships with
    the harness's own source, and a deployed plugin is checked out on its
    own. One ``git status`` cannot span the two.

    Returns:
        The plugin, and the template in the other repository.
    """
    plugin_side = tmp_path / "plugin-side"
    shutil.copytree(plugin_root, plugin_side / "plugins" / "ai-rfc")
    _commit(plugin_side)
    harness_side = tmp_path / "harness-side"
    template = harness_side / "prompts" / "loop.tmpl.md"
    template.parent.mkdir(parents=True)
    template.write_text(TEMPLATE.read_text())
    _commit(harness_side)
    return plugin_side / "plugins" / "ai-rfc", template


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


def test_targets_names_exactly_what_apply_writes(plugin_copy, template_copy):
    """The guard must know the paths before ``apply`` exists to report them.

    ``targets`` spells the generated loop skill out rather than learning it
    from the renderer, so this equality is the only thing keeping the two
    from drifting apart.
    """
    named = targets(plugin_copy, template_path=template_copy)

    applied = apply(
        encode(seed_from_plugin(plugin_copy)), plugin_copy, template_path=template_copy
    )

    assert named == applied.written
    assert named[-1] == applied.rendered_skill


def test_repo_root_finds_the_repository_and_reports_none_outside_one(
    plugin_repo, tmp_path
):
    repo, plugin_copy, _ = plugin_repo
    outside = tmp_path / "loose"
    outside.mkdir()

    assert repo_root(plugin_copy) == repo.resolve()
    assert repo_root(outside) is None


def test_dirty_paths_names_a_modified_target_and_a_staged_one(plugin_repo):
    repo, plugin_copy, template = plugin_repo
    watched = targets(plugin_copy, template_path=template)
    assert dirty_paths(repo, watched) == ()

    _skill(plugin_copy, "rfc-style").write_text("a hand edit nobody committed\n")
    template.write_text(TEMPLATE.read_text() + "\nA staged line.\n")
    git(repo, "add", str(template))

    assert set(dirty_paths(repo, watched)) == {
        "plugins/ai-rfc/skills/ai-rfc-rfc-style/SKILL.md",
        "prompts/loop.tmpl.md",
    }


def test_dirty_paths_counts_a_file_git_keeps_no_copy_of(plugin_repo):
    """Untracked is the worst case: overwriting loses the content outright."""
    repo, _, _ = plugin_repo
    fresh = repo / "prompts" / "proposed.tmpl.md"
    fresh.write_text(TEMPLATE.read_text())

    assert dirty_paths(repo, [fresh]) == ("prompts/proposed.tmpl.md",)


def test_dirty_paths_refuses_when_git_cannot_answer(plugin_repo, split_repos):
    """A check that could not run must never read as a check that passed.

    Asked about a path in another repository, git exits 128 and prints
    nothing to stdout, so returning what it printed would report every file
    clean and wave the write through.
    """
    repo, _, _ = plugin_repo
    _, elsewhere = split_repos

    with pytest.raises(ExperimentError) as error:
        dirty_paths(repo, [elsewhere])

    assert "status" in str(error.value)
    assert "outside repository" in str(error.value)


def test_by_repository_groups_each_path_under_its_own_top_level(split_repos, tmp_path):
    plugin_copy, template = split_repos
    loose = tmp_path / "loose" / "SKILL.md"
    loose.parent.mkdir()
    loose.write_text("body\n")
    skill = _skill(plugin_copy, "rfc-style")

    grouped, unchecked = by_repository([skill, template, loose])

    assert grouped == {
        repo_root(skill.parent): (skill,),
        repo_root(template.parent): (template,),
    }
    assert unchecked == (loose,)


def test_uncommitted_work_sees_a_dirty_target_beside_one_in_another_repository(
    split_repos,
):
    """The defect this exists to stop: two repositories, one silent pass.

    A single status over both exits 128 and prints nothing, so the modified
    skill went unreported and was overwritten with a clean exit.
    """
    plugin_copy, template = split_repos
    watched = targets(plugin_copy, template_path=template)
    assert uncommitted_work(watched).dirty == ()

    _skill(plugin_copy, "rfc-style").write_text("a hand edit nobody committed\n")

    work = uncommitted_work(watched)

    assert work.dirty == ("plugins/ai-rfc/skills/ai-rfc-rfc-style/SKILL.md",)
    assert work.unchecked == ()


def test_uncommitted_work_sees_a_dirty_template_in_the_other_repository(split_repos):
    plugin_copy, template = split_repos
    template.write_text(TEMPLATE.read_text() + "\nAn uncommitted line.\n")

    work = uncommitted_work(targets(plugin_copy, template_path=template))

    assert work.dirty == ("prompts/loop.tmpl.md",)


def test_uncommitted_work_reports_what_it_could_not_check(plugin_copy, template_copy):
    """Nothing vouched for these; the caller has to be able to say so."""
    watched = targets(plugin_copy, template_path=template_copy)

    work = uncommitted_work(watched)

    assert work.dirty == ()
    assert set(work.unchecked) == set(watched)


def test_diff_stat_names_the_files_a_candidate_changed(plugin_repo):
    _, plugin_copy, template = plugin_repo
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
