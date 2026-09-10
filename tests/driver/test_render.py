import dataclasses
import re
import shutil

import pytest

from ai_rfc.driver import DriverError
from ai_rfc.driver.render import (
    INTERVIEW_PREAMBLE,
    SKILL_FRONTMATTER,
    SLOT_TABLES,
    TEMPLATE,
    arm_prompt,
    render_loop,
    render_task,
    strip_frontmatter,
    unified_diff,
    write_plugin_skill,
)


def test_every_table_fills_every_slot():
    for arm in SLOT_TABLES:
        text = render_loop(arm)
        assert "{{" not in text and "}}" not in text, arm


def test_unknown_arm_is_refused():
    with pytest.raises(DriverError):
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
    lines = unified_diff(a, b, "arm-A", "arm-B").splitlines()
    assert lines[0].startswith("--- arm-A") and lines[1].startswith("+++ arm-B")
    # Only lines 0 and 1 are file headers, and a hunk header starts with `@`.
    # Filtering the body on the `---`/`+++` prefixes instead would silently
    # drop a *changed* line whose own text begins `--` or `++` — a bare CLI
    # flag at line start — from the set this asserts on.
    changed = [line for line in lines[2:] if line[:1] in "+-"]
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
    with pytest.raises(DriverError) as excinfo:
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
    with pytest.raises(DriverError):
        arm_prompt("B", plugin_root, profile="interview")
    with pytest.raises(DriverError):
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


def test_the_structures_skill_names_every_kind_and_the_tool():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "plugins/ai-rfc/skills/ai-rfc-structures/SKILL.md").read_text()
    for kind in ("wire-format", "message", "record", "enum", "state-machine"):
        assert kind in text
    assert "ai_rfc_structure_upsert" in text
    assert "ai_rfc_draft_render" in text
    # Naming the kinds is not enough: an agent that does not know which key
    # each kind takes writes a body the schema refuses.
    assert "values:" in text
    assert "transitions:" in text


def test_the_editorial_skill_states_the_move_never_drop_rule():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    raw = (root / "plugins/ai-rfc/skills/ai-rfc-editorial/SKILL.md").read_text()
    # The bundle carries the body only, so a phrase that lived in the
    # frontmatter would pin nothing the model ever reads.
    text = strip_frontmatter(raw)
    assert "Move, never drop" in text
    assert "Change Log" in text and "Implementation Notes" in text
    assert "normative" in text.lower()
    # The superset rule, not the equality one a 2026-09-03 draft stated: a
    # consolidation may not lose a citation, and may add one.
    assert "keep every citation" in text and "adding one" in text


def test_the_consolidation_template_renders_for_every_arm():
    from ai_rfc.driver.render import render_consolidation

    for arm in SLOT_TABLES:
        text = render_consolidation(arm)
        assert "{{" not in text and "}}" not in text, f"{arm} left a slot unrendered"


def test_arm_c_is_told_the_new_commands_are_unavailable():
    from ai_rfc.driver.render import render_consolidation

    assert "not available in arm C" in render_consolidation("C")


def test_the_consolidation_prompt_bundles_the_editorial_skills(plugin_root):
    from ai_rfc.driver.render import consolidation_prompt

    prompt = consolidation_prompt("A", plugin_root)
    assert "# Editorial" in prompt
    assert "# Structures" in prompt
    assert "# Figures in a reconstructed specification" in prompt
    # This round adjudicates nothing, so the loop's hygiene text is absent.
    assert "# Evidence hygiene for reconstruction manifests" not in prompt
    assert "\nname: ai-rfc-" not in prompt and not prompt.startswith("---")


def test_the_consolidation_prompt_never_states_the_equality_rule(plugin_root):
    # Step 4 of this very prompt orders the agent to add references and a
    # figure caption. A bundled skill saying the citation set may not change
    # would refuse the step above it, inside one prompt.
    from ai_rfc.driver.render import consolidation_prompt

    prompt = consolidation_prompt("A", plugin_root)
    assert "must equal" not in prompt
    assert "same citation set" not in prompt
    assert "keeps every citation" in prompt


def test_the_loop_and_the_consolidation_share_one_slot_validator():
    from ai_rfc.driver.render import render_consolidation

    for render in (render_loop, render_consolidation):
        with pytest.raises(DriverError):
            render("no-such-arm")
        with pytest.raises(DriverError) as excinfo:
            render("A", template="{{nonesuch}}\n")
        assert "nonesuch" in str(excinfo.value)


def _plugin_copy(plugin_root, tmp_path):
    root = tmp_path / "plugin"
    shutil.copytree(plugin_root, root)
    return root


def test_a_slot_in_a_bundled_skill_body_is_refused(plugin_root, tmp_path):
    # The template's slots are filled before the skill texts are appended, so
    # nothing rescanned the result: a slot in a bundled body reached the frozen
    # prompt verbatim.
    root = _plugin_copy(plugin_root, tmp_path)
    body = root / "skills" / "ai-rfc-figures" / "SKILL.md"
    body.write_text(body.read_text() + "\nSee {{cluster_next}} for the rule.\n")
    with pytest.raises(DriverError) as excinfo:
        arm_prompt("A", root)
    assert "cluster_next" in str(excinfo.value)
    assert "ai-rfc-figures" in str(excinfo.value)


def test_a_slot_in_a_bundle_that_opens_on_a_preamble_is_refused(plugin_root, tmp_path):
    # A profile with a fixed preamble renders no template at all, so before the
    # bundle was scanned no slot check ran on any part of it.
    root = _plugin_copy(plugin_root, tmp_path)
    body = root / "skills" / "ai-rfc-interviewing" / "SKILL.md"
    body.write_text(body.read_text() + "\nSee {{cluster_next}} for the rule.\n")
    with pytest.raises(DriverError) as excinfo:
        arm_prompt("A", root, profile="interview")
    assert "cluster_next" in str(excinfo.value)


def test_a_slot_text_that_names_a_slot_is_refused(plugin_root, monkeypatch):
    # Substitution is single pass, so a slot's own text naming a slot reaches
    # the rendered opening unfilled.
    monkeypatch.setitem(
        SLOT_TABLES, "A", dict(SLOT_TABLES["A"], guidance="See {{gate}}.")
    )
    with pytest.raises(DriverError) as excinfo:
        arm_prompt("A", plugin_root)
    assert "gate" in str(excinfo.value)


def test_every_arm_states_the_strict_done_rule_for_the_next_cluster():
    # A lax rule in one arm and a strict one in another means the arms work
    # different clusters. The ledger is the only source of truth.
    for arm in SLOT_TABLES:
        text = render_loop(arm)
        assert "neither" not in text.lower(), f"{arm} still states the lax rule"
        assert "pre-seeded" in text.lower(), f"{arm} omits the pre-seeded clause"


def test_a_slot_in_a_fixed_preamble_is_refused(plugin_root, monkeypatch):
    # The preamble path renders no template, so its own text is the only part
    # of that bundle nothing else would ever look at.
    from ai_rfc.driver.render import TASK_PROFILES

    spec = TASK_PROFILES["interview"]
    monkeypatch.setitem(
        TASK_PROFILES,
        "interview",
        dataclasses.replace(spec, preamble=f"{spec.preamble}\nSee {{{{gate}}}}.\n"),
    )
    with pytest.raises(DriverError) as excinfo:
        arm_prompt("A", plugin_root, profile="interview")
    assert "gate" in str(excinfo.value) and "preamble" in str(excinfo.value)


def test_the_shipped_skill_cannot_carry_a_slot_a_slot_text_named(tmp_path, monkeypatch):
    # write_plugin_skill renders without bundling, so the scan has to live in
    # the renderer: the GEPA apply path writes this file too.
    monkeypatch.setitem(
        SLOT_TABLES,
        "interactive",
        dict(SLOT_TABLES["interactive"], guidance="See {{gate}}."),
    )
    root = tmp_path / "plugin"
    (root / "skills" / "ai-rfc-reconstruction-loop").mkdir(parents=True)
    with pytest.raises(DriverError) as excinfo:
        write_plugin_skill(root)
    assert "gate" in str(excinfo.value)


def test_the_loop_tells_a_cluster_to_register_a_structure_it_defines():
    # The slots are asserted on the template, not on a rendering: arm C fills
    # both with a plain "not available" sentence, and every other check in the
    # repository re-derives what it expects from the template itself, so a
    # rendering-based needle would survive the slots being dropped.
    template = TEMPLATE.read_text()
    assert "{{structure_upsert}}" in template
    assert "{{draft_render}}" in template

    for arm in SLOT_TABLES:
        text = render_loop(arm)
        assert "3b" in text
        assert "structure" in text.lower()
