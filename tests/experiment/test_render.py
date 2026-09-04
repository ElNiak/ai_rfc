import re

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.render import (
    INTERVIEW_PREAMBLE,
    SKILL_FRONTMATTER,
    SLOT_TABLES,
    TEMPLATE,
    arm_prompt,
    render_loop,
    render_task,
    unified_diff,
    write_plugin_skill,
)


def test_every_table_fills_every_slot():
    for arm in SLOT_TABLES:
        text = render_loop(arm)
        assert "{{" not in text and "}}" not in text, arm


def test_unknown_arm_is_refused():
    with pytest.raises(ExperimentError):
        render_loop("Z")


def test_plugin_skill_is_the_interactive_rendering(plugin_root):
    skill = plugin_root / "skills" / "ai-rfc-reconstruction-loop" / "SKILL.md"
    assert skill.read_text() == SKILL_FRONTMATTER + render_loop("interactive")


def test_write_plugin_skill_round_trips(tmp_path):
    root = tmp_path / "plugin"
    (root / "skills" / "ai-rfc-reconstruction-loop").mkdir(parents=True)
    written = write_plugin_skill(root)
    assert written.read_text() == SKILL_FRONTMATTER + render_loop("interactive")


def test_arm_renderings_name_only_their_surface():
    a, b, c = (render_loop(arm) for arm in "ABC")
    assert "ai_rfc_cluster_next" in a
    assert "ai_rfc cluster-next" not in a and "python -m ai_rfc" not in a
    assert "ai_rfc cluster-next" in b
    assert "ai_rfc_cluster_next" not in b and "python -m ai_rfc" not in b
    assert "python -m ai_rfc" in c
    assert "arfc_" not in c and "ai_rfc cluster" not in c


def test_arm_prompt_bundles_the_neutral_texts(plugin_root):
    prompt = arm_prompt("A", plugin_root)
    assert "# RFC prose for a reconstructed specification" in prompt
    assert "# The claim-citation convention" in prompt
    assert "# Evidence hygiene for reconstruction manifests" in prompt
    assert "\nname: ai-rfc-" not in prompt and not prompt.startswith("---")


def test_arm_prompts_differ_only_where_slots_differ(plugin_root):
    a, b = arm_prompt("A", plugin_root), arm_prompt("B", plugin_root)
    diff = unified_diff(a, b, "arm-A", "arm-B")
    assert diff.startswith("--- arm-A") and "+++ arm-B" in diff
    changed = [
        line
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert changed and all("ai_rfc" in line for line in changed)


def test_the_raw_arm_uses_the_dispatcher():
    """Arm C's family is ``python -m ai_rfc``; every raw command must start there."""
    c = render_loop("C")
    assert "python -m ai_rfc check " in c
    assert "python -m ai_rfc draft checkpoint " in c
    assert "python -m ai_rfc draft gate " in c
    assert "python -m ai_rfc.draft" not in c and "python -m ai_rfc $" not in c


def test_every_arm_names_its_build_step():
    a, b, c, interactive = (render_loop(arm) for arm in ("A", "B", "C", "interactive"))
    assert "ai_rfc_draft_build" in a and "refuses on findings" in a
    assert "ai_rfc draft-build" in b
    assert "not available in this arm" in c and "draft-build" not in c
    assert "ai_rfc_draft_build" in interactive and "ai_rfc draft-build" in interactive


def test_arm_prompt_bundles_the_keyword_policy_and_the_figures_skill(plugin_root):
    prompt = arm_prompt("A", plugin_root)
    assert "# Keyword policy" in prompt
    assert "# Figures in a reconstructed specification" in prompt
    assert "CLAUDE.md" not in prompt


def test_arm_c_prompt_has_no_build_tool_or_verb_names(plugin_root):
    # Arm C has neither the MCP server nor the `ai_rfc` command, so its
    # bundled prompt (the loop rendering plus the arm-neutral skill texts)
    # must never leak a tool or CLI-verb name it cannot use.
    prompt = arm_prompt("C", plugin_root)
    assert "ai_rfc_draft_build" not in prompt
    assert "ai_rfc draft-build" not in prompt


def test_the_package_template_is_what_an_absent_override_renders():
    """The override seam must not move the default path by one byte."""
    for arm in SLOT_TABLES:
        assert render_loop(arm) == render_loop(arm, template=TEMPLATE.read_text())


def test_render_loop_renders_an_overriding_template():
    text = render_loop("A", template="{{preamble}}\n\n{{guidance}}\n")
    assert text == f"{SLOT_TABLES['A']['preamble']}\n\n{SLOT_TABLES['A']['guidance']}\n"
    assert "{{" not in text


def test_an_overriding_template_naming_an_unknown_slot_is_refused():
    with pytest.raises(ExperimentError) as excinfo:
        render_loop("A", template="{{nonesuch}}\n")
    assert "nonesuch" in str(excinfo.value)


def test_write_plugin_skill_writes_an_overriding_template(tmp_path):
    root = tmp_path / "plugin"
    (root / "skills" / "ai-rfc-reconstruction-loop").mkdir(parents=True)
    written = write_plugin_skill(root, template="{{guidance}}\n")
    body = f"{SLOT_TABLES['interactive']['guidance']}\n"
    assert written.read_text() == SKILL_FRONTMATTER + body


def test_the_interview_bundle_carries_only_its_own_skills(plugin_root):
    prompt = arm_prompt("A", plugin_root, profile="interview")
    assert prompt.startswith(INTERVIEW_PREAMBLE.rstrip("\n"))
    assert "# The author-feedback loop" in prompt
    assert "# Evidence hygiene for reconstruction manifests" in prompt
    assert "# The reconstruction loop" not in prompt
    assert "# RFC prose for a reconstructed specification" not in prompt
    assert "\nname: ai-rfc-" not in prompt


def test_the_interview_preamble_names_only_mcp_tools():
    """Arm A has no shell, so every ai_rfc token must be a real MCP tool."""
    from ai_rfc.server.tools import ALL_TOOLS

    named = set(re.findall(r"\bai_rfc[\w-]*", INTERVIEW_PREAMBLE))
    assert named
    assert named <= {tool.__name__ for tool in ALL_TOOLS}


def test_the_interview_profile_is_arm_A_only(plugin_root):
    with pytest.raises(ExperimentError):
        arm_prompt("B", plugin_root, profile="interview")
    with pytest.raises(ExperimentError):
        arm_prompt("A", plugin_root, profile="no-such-profile")


def test_render_task_renders_the_profile_it_is_given():
    task = render_task((2, 2), profile="interview")
    assert "$low" not in task and "$high" not in task
    assert "interviews/int-001.md" in task
    assert task != render_task((2, 2))


def test_render_task_leaves_a_placeholder_it_cannot_fill(tmp_path):
    """A proposed template may name anything; an unknown $name is not a crash."""
    template = tmp_path / "task.tmpl.md"
    template.write_text("Ordinals $low..$high under $AI_RFC_WORKSPACE.\n")
    assert (
        render_task((3, 3), template=template)
        == "Ordinals 3..3 under $AI_RFC_WORKSPACE.\n"
    )
