import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.render import (
    SKILL_FRONTMATTER,
    SLOT_TABLES,
    arm_prompt,
    render_loop,
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
