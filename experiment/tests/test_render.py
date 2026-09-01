import pytest

from experiment import ExperimentError
from experiment.render import (
    SLOT_TABLES,
    SKILL_FRONTMATTER,
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
    skill = plugin_root / "skills" / "arfc-reconstruction-loop" / "SKILL.md"
    assert skill.read_text() == SKILL_FRONTMATTER + render_loop("interactive")


def test_write_plugin_skill_round_trips(tmp_path):
    root = tmp_path / "plugin"
    (root / "skills" / "arfc-reconstruction-loop").mkdir(parents=True)
    written = write_plugin_skill(root)
    assert written.read_text() == SKILL_FRONTMATTER + render_loop("interactive")


def test_arm_renderings_name_only_their_surface():
    a, b, c = (render_loop(arm) for arm in "ABC")
    assert "arfc_cluster_next" in a
    assert "arfc cluster-next" not in a and "python -m panther" not in a
    assert "arfc cluster-next" in b
    assert "arfc_cluster_next" not in b and "python -m panther" not in b
    assert "python -m panther.plugins.services.testers.ai_rfc" in c
    assert "arfc_" not in c and "arfc cluster" not in c


def test_arm_prompt_bundles_the_neutral_texts(plugin_root):
    prompt = arm_prompt("A", plugin_root)
    assert "# RFC prose for a reconstructed specification" in prompt
    assert "# The claim-citation convention" in prompt
    assert "# Evidence hygiene for reconstruction manifests" in prompt
    assert "\nname: arfc-" not in prompt and not prompt.startswith("---")


def test_arm_prompts_differ_only_where_slots_differ(plugin_root):
    a, b = arm_prompt("A", plugin_root), arm_prompt("B", plugin_root)
    diff = unified_diff(a, b, "arm-A", "arm-B")
    assert diff.startswith("--- arm-A") and "+++ arm-B" in diff
    changed = [
        line
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert changed and all("arfc" in line for line in changed)
