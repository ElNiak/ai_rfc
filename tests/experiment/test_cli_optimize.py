"""The ``optimize`` verbs: what they print, and what they refuse to start.

Nothing here imports ``gepa`` or reaches a network. A whole optimization is
covered by ``tests/experiment/optimize/test_run.py``, which needs the 3.11
environment; what is left to check is that a pilot cannot be started by
accident and that ``apply`` says the diff is uncommitted.
"""

import dataclasses
import importlib.util
import json
import shutil

import pytest

from ai_rfc.experiment import cli
from ai_rfc.experiment.optimize.codec import encode, seed_from_plugin
from ai_rfc.experiment.render import TEMPLATE

without_gepa = pytest.mark.skipif(
    importlib.util.find_spec("gepa") is not None,
    reason="the refusal only fires where the backend is not installed",
)


@pytest.fixture
def plugin_copy(tmp_path, plugin_root):
    copy = tmp_path / "plugin"
    shutil.copytree(plugin_root, copy)
    return copy


@pytest.fixture
def template_copy(tmp_path):
    path = tmp_path / "loop.tmpl.md"
    path.write_text(TEMPLATE.read_text())
    return path


@pytest.fixture
def plugin_repo(tmp_path, plugin_root):
    """A committed repository holding a plugin copy and a loop template.

    Returns:
        The plugin inside the repository, and the template beside it.
    """
    from ai_rfc.server.testing import git

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
    return repo / "plugins" / "ai-rfc", template


def _apply(candidate, plugin_root, template, *extra):
    return [
        "optimize",
        "apply",
        str(candidate),
        "--plugin-root",
        str(plugin_root),
        "--template",
        str(template),
        *extra,
    ]


def _candidate(tmp_path, plugin_root, name="best.txt"):
    seed = seed_from_plugin(plugin_root)
    path = tmp_path / name
    path.write_text(
        encode(dataclasses.replace(seed, rfc_style=seed.rfc_style + "\nBe terse.\n"))
    )
    return path


@pytest.fixture
def examples_file(tmp_path):
    path = tmp_path / "examples.json"
    path.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "kind": "loop",
                        "id": "loop-1",
                        "pristine_dir": str(tmp_path / "pristine"),
                        "cluster_id": "c0002-pr-abcdef",
                        "budget_usd": 4.0,
                    }
                ]
            }
        )
    )
    return path


def _pilot(tmp_path, examples_file, toolchain_record, *extra):
    return [
        "optimize",
        "run",
        "--root",
        str(tmp_path / "root"),
        "--name",
        "pilot-1",
        "--stage",
        "pilot",
        "--examples",
        str(examples_file),
        "--toolchain",
        str(toolchain_record),
        *extra,
    ]


def _priced(*extra):
    return [
        "--max-evals",
        "30",
        "--max-token-cost",
        "5",
        "--model",
        "some-agent-model",
        "--reflection-lm",
        "some/proposer-model",
        "--judge-model",
        "some-judge-model",
        *extra,
    ]


def test_seed_prints_the_bundle_the_plugin_carries(plugin_root, capsys, tmp_path):
    out_file = tmp_path / "seed.txt"

    assert cli.main(["optimize", "seed", "--plugin-root", str(plugin_root)]) == 0
    printed = capsys.readouterr().out
    assert (
        cli.main(
            [
                "optimize",
                "seed",
                "--plugin-root",
                str(plugin_root),
                "--out",
                str(out_file),
            ]
        )
        == 0
    )

    assert printed == encode(seed_from_plugin(plugin_root))
    assert out_file.read_text() == printed
    assert str(out_file) in capsys.readouterr().out


def test_apply_writes_the_candidate_and_says_nothing_was_committed(
    plugin_copy, template_copy, tmp_path, capsys
):
    seed = seed_from_plugin(plugin_copy)
    candidate = tmp_path / "best.txt"
    candidate.write_text(
        encode(dataclasses.replace(seed, rfc_style=seed.rfc_style + "\nBe terse.\n"))
    )

    code = cli.main(
        [
            "optimize",
            "apply",
            str(candidate),
            "--plugin-root",
            str(plugin_copy),
            "--template",
            str(template_copy),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert cli.NOT_COMMITTED in out
    assert (
        "Be terse."
        in (plugin_copy / "skills" / "ai-rfc-rfc-style" / "SKILL.md").read_text()
    )


def test_apply_refuses_to_overwrite_work_nobody_committed(
    plugin_repo, tmp_path, capsys
):
    """A dirty target is indistinguishable from the candidate in the diff.

    The verb's whole contract is that a person reads the diff afterwards. An
    uncommitted edit written over would show up as part of the candidate's
    own change, with no way to tell the two apart and no copy to recover.
    """
    plugin_copy, template = plugin_repo
    skill = plugin_copy / "skills" / "ai-rfc-rfc-style" / "SKILL.md"
    skill.write_text("half-finished rewrite\n")
    candidate = _candidate(tmp_path, plugin_copy)

    code = cli.main(_apply(candidate, plugin_copy, template))

    err = capsys.readouterr().err
    assert code == 1
    assert "skills/ai-rfc-rfc-style/SKILL.md" in err and "--force" in err
    assert skill.read_text() == "half-finished rewrite\n"


def test_force_applies_over_uncommitted_work(plugin_repo, tmp_path, capsys):
    plugin_copy, template = plugin_repo
    skill = plugin_copy / "skills" / "ai-rfc-rfc-style" / "SKILL.md"
    skill.write_text("half-finished rewrite\n")
    candidate = _candidate(tmp_path, plugin_copy)

    code = cli.main(_apply(candidate, plugin_copy, template, "--force"))

    assert code == 0
    assert cli.NOT_COMMITTED in capsys.readouterr().out
    assert "Be terse." in skill.read_text()


def test_apply_says_so_when_the_plugin_is_in_no_repository(
    plugin_copy, template_copy, tmp_path, capsys
):
    """Nothing was checked, and the reader is told rather than reassured."""
    candidate = _candidate(tmp_path, plugin_copy)

    code = cli.main(_apply(candidate, plugin_copy, template_copy))

    out = capsys.readouterr().out
    assert code == 0
    assert "no git repository" in out
    assert cli.NOT_COMMITTED in out


def test_apply_reports_a_rejected_candidate_and_leaves_the_plugin_alone(
    plugin_copy, template_copy, tmp_path, capsys
):
    before = (plugin_copy / "skills" / "ai-rfc-rfc-style" / "SKILL.md").read_bytes()
    candidate = tmp_path / "broken.txt"
    candidate.write_text("nothing the codec recognises\n")

    code = cli.main(
        [
            "optimize",
            "apply",
            str(candidate),
            "--plugin-root",
            str(plugin_copy),
            "--template",
            str(template_copy),
        ]
    )

    assert code == 1
    assert "missing section header" in capsys.readouterr().err
    assert (
        plugin_copy / "skills" / "ai-rfc-rfc-style" / "SKILL.md"
    ).read_bytes() == before


def test_a_pilot_without_a_credential_stops_before_anything_is_created(
    tmp_path, examples_file, toolchain_record, monkeypatch, capsys
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    code = cli.main(
        _pilot(tmp_path, examples_file, toolchain_record, *_priced("--yes"))
    )

    assert code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "root").exists()


def test_a_pilot_that_names_no_ceiling_names_the_flags_it_wants(
    tmp_path, examples_file, toolchain_record, monkeypatch, capsys
):
    """Every model id and both ceilings, or the run does not start.

    A missing ``--max-evals`` is not a default to be guessed: the flag is the
    only thing bounding how many agent sessions a pilot pays for.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used-by-this-test")

    code = cli.main(_pilot(tmp_path, examples_file, toolchain_record, "--yes"))

    err = capsys.readouterr().err
    assert code == 1
    for flag in (
        "--max-evals",
        "--max-token-cost",
        "--model",
        "--reflection-lm",
        "--judge-model",
    ):
        assert flag in err
    assert not (tmp_path / "root").exists()


def test_a_pilot_prints_its_worst_case_spend_and_waits_for_yes(
    tmp_path, examples_file, toolchain_record, monkeypatch, capsys
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used-by-this-test")

    code = cli.main(_pilot(tmp_path, examples_file, toolchain_record, *_priced()))

    captured = capsys.readouterr()
    assert code == 1
    assert "125.00" in captured.out
    assert "--yes" in captured.err
    assert not (tmp_path / "root").exists()


def test_the_fake_stage_refuses_an_agent_binary_that_is_not_there(
    tmp_path, examples_file, toolchain_record, capsys
):
    code = cli.main(
        [
            "optimize",
            "run",
            "--root",
            str(tmp_path / "root"),
            "--name",
            "rehearsal",
            "--stage",
            "fake",
            "--examples",
            str(examples_file),
            "--toolchain",
            str(toolchain_record),
            "--claude-bin",
            str(tmp_path / "no-such-claude"),
        ]
    )

    assert code == 1
    assert "no-such-claude" in capsys.readouterr().err
    assert not (tmp_path / "root").exists()


@without_gepa
def test_the_harness_interpreter_is_told_it_has_no_backend(
    tmp_path, examples_file, toolchain_record, capsys
):
    """The likeliest mistake is running this verb on the wrong python.

    ``gepa`` installs only under the ``optimize`` extra, on 3.11, so on the
    interpreter the rest of the harness runs on the search cannot start. It
    says so rather than raising ``ModuleNotFoundError`` out of the backend.
    """
    code = cli.main(
        [
            "optimize",
            "run",
            "--root",
            str(tmp_path / "root"),
            "--name",
            "rehearsal",
            "--stage",
            "fake",
            "--examples",
            str(examples_file),
            "--toolchain",
            str(toolchain_record),
        ]
    )

    assert code == 1
    assert "optimize extra" in capsys.readouterr().err
    assert not (tmp_path / "root").exists()
