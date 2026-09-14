"""The one door.

Every verb mounts under ``ai-rfc``, forwards untouched, and shares one help.
"""

import argparse
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ai_rfc import cli
from ai_rfc.entrypoints import ENTRY_POINTS
from ai_rfc.pipeline.stages import STAGES

pytestmark = pytest.mark.unit


#: The six verbs §6 retires from the operator's help. Hidden, not deleted:
#: ``ai_rfc/driver/render.py``'s arm-C table types ``python -m ai_rfc check``
#: through the **root** door, so removing the rows would break that arm
#: outright. ``draft`` is not among them — it is a spec-named agent group whose
#: verbs a later task renders into the experiment's prompts.
HIDDEN = ("check", "coverage", "history", "forge", "timeline", "views")


def test_help_lists_every_registered_verb_once_under_its_section(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for entry in ENTRY_POINTS:
        if entry.hidden:
            continue
        assert (
            out.count(f"  {entry.verb} ") == 1 or out.count(f"  {entry.verb}\n") == 1
        ), entry.verb
        assert entry.section in out


def test_a_hidden_verb_is_absent_from_the_help_and_present_in_the_parser(capsys):
    """Retired from the listing, still dispatchable — and the registry agrees.

    The three assertions are one property each and none of them substitutes
    for another: that the registry's ``hidden`` flags are exactly
    :data:`HIDDEN`, that no hidden verb reaches the rendered table, and that
    every one of them still parses. Asserting only the help would pass for a
    verb that had been deleted, which is the change this must not become.

    The table is read as a verb column rather than searched as text: ``check``
    and ``forge`` both appear inside other rows' summaries, so a substring
    test over the whole help could never go green.
    """
    assert {entry.verb for entry in ENTRY_POINTS if entry.hidden} == set(HIDDEN)

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    first_heading = out.index(f"{ENTRY_POINTS[0].section}:")
    table = out[first_heading:]
    rendered = {
        line.split()[0] for line in table.splitlines() if re.match(r"^ {2}\S", line)
    }
    assert rendered.isdisjoint(HIDDEN)

    mounted = cli.build_parser()._subparsers._group_actions[0].choices
    assert set(HIDDEN) <= set(mounted)


def test_the_hidden_verbs_section_still_renders_when_one_row_survives(capsys):
    """An emptied section drops its heading; a thinned one keeps it.

    ``PERFORMED`` loses all four of its rows and vanishes entirely, which is
    the intended retirement. ``BY_HAND`` loses two of three and must not, or
    ``draft`` would print under the previous section's heading.
    """
    from ai_rfc.entrypoints import BY_HAND, PERFORMED

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert f"{BY_HAND}:" in out
    assert f"{PERFORMED}:" not in out


#: Every argv arm C types through the root door, and the namespace each one
#: parsed to before the ``draft`` group was folded in. Written out rather than
#: derived: the point is that these exact dicts survive, so a table computed
#: from the parser would agree with whatever the parser became.
#:
#: The sources are ``ai_rfc/driver/render.py``'s ``_RAW`` table — ``lint`` and
#: ``gate`` at ``:137,:146``, ``checkpoint`` at ``:151``, ``citation_gate`` at
#: ``:169`` — which spells them ``python -m ai_rfc …``, the **root** door, not
#: ``python -m ai_rfc.<sub>``.
ARM_C_ARGV = [
    (
        ["check", "/w/manifest.yaml", "--out", "/w/out", "--repo", "/w/clone"],
        {
            "manifest": "/w/manifest.yaml",
            "out": "/w/out",
            "repo": "/w/clone",
            "strict": False,
            "verb": "check",
        },
    ),
    (
        [
            "check",
            "/w/manifest.yaml",
            "--out",
            "/w/out",
            "--repo",
            "/w/clone",
            "--strict",
        ],
        {
            "manifest": "/w/manifest.yaml",
            "out": "/w/out",
            "repo": "/w/clone",
            "strict": True,
            "verb": "check",
        },
    ),
    (
        [
            "draft",
            "checkpoint",
            "/w/manifest.yaml",
            "--timeline",
            "/w/timeline",
            "--cluster",
            "c0001-x",
            "--out",
            "/w/checkpoints",
        ],
        {
            "base": None,
            "cluster": "c0001-x",
            "consolidation": None,
            "manifest": "/w/manifest.yaml",
            "out": "/w/checkpoints",
            "timeline": "/w/timeline",
            "verb": "checkpoint",
        },
    ),
    (
        [
            "draft",
            "gate",
            "/w/draft",
            "--timeline",
            "/w/timeline",
            "--checkpoints",
            "/w/checkpoints",
            "--questions",
            "/w/questions.yaml",
            "--revisions",
            "/w/revisions.yaml",
            "--out",
            "/w/out",
        ],
        {
            "checkpoints": "/w/checkpoints",
            "consolidations": None,
            "draftrepo": "/w/draft",
            "out": "/w/out",
            "questions": "/w/questions.yaml",
            "revisions": "/w/revisions.yaml",
            "strict": False,
            "timeline": "/w/timeline",
            "verb": "gate",
        },
    ),
]


@pytest.mark.parametrize(
    ("argv", "expected"), ARM_C_ARGV, ids=[" ".join(a[:2]) for a, _ in ARM_C_ARGV]
)
def test_arm_c_argv_parses_to_the_same_namespace(argv, expected):
    """A pin, not a RED: green before the fold and green after it.

    Compared whole rather than by the fields that were touched. A merge that
    made a positional optional could satisfy every ``assert x == y`` over the
    arguments arm C passes and still have added a default that changes what
    the verb does with the ones it did not pass.
    """
    parsed = cli.build_parser().parse_args(argv)
    public = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(parsed).items()
        if not key.startswith("_")
    }
    assert public == expected


def test_every_registered_module_exposes_the_configure_run_contract():
    for entry in ENTRY_POINTS:
        module = importlib.import_module(entry.module)
        assert callable(module.configure) and callable(module.run), entry.module
        assert callable(module.main), entry.module


def test_the_config_verb_refuses_a_verb_it_does_not_implement():
    """A third verb must not silently render whichever branch was the fallback.

    The same hazard ``draft``'s explicit ``gate`` branch removed: a dispatcher
    ending in an unguarded expression runs its last arm for anything it does
    not recognise, with arguments meant for something else.
    """
    from ai_rfc.lifecycle.config import cli as config_cli

    with pytest.raises(AssertionError):
        config_cli.run(argparse.Namespace(verb="frobnicate"))


def test_a_verb_forwards_to_its_module_run(tmp_path, capsys):
    """`ai-rfc pipeline status WS` and `python -m ai_rfc.pipeline status WS` agree."""
    from ai_rfc.pipeline import cli as pipeline_cli

    empty = tmp_path / "ws"
    empty.mkdir()
    via_root = cli.main(["pipeline", "status", str(empty)])
    root_out = capsys.readouterr().out
    via_module = pipeline_cli.main(["status", str(empty)])
    assert via_root == via_module and capsys.readouterr().out == root_out


def test_the_instrument_help_reaches_a_nested_verb(capsys):
    """``ai-rfc experiment preflight --help`` is the mount's own smoke test.

    The instrument nests a second level of subparsers under every command, so
    reaching one of them is what proves ``configure`` was handed the door's
    subparser rather than building a parser of its own: a leaf that named
    ``experiment`` alone, or ``usage: preflight``, would mean the tree below
    the verb never inherited the door's ``prog``.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["experiment", "preflight", "--help"])
    assert excinfo.value.code == 0
    assert "usage: ai-rfc experiment preflight" in capsys.readouterr().out


def test_the_experiment_verb_forwards_to_the_instruments_run(tmp_path, capsys):
    """``ai-rfc experiment profile init`` and the module door agree.

    Namespaces are the wrong thing to compare: the door injects ``_run`` and
    sets ``verb="experiment"``, which the instrument's own ``dest="verb"``
    then overwrites for its nested commands. What must agree is what the
    operator sees — the exit code and the output — which is also what spec §3
    promises the module door keeps until CLI-3 retires it.
    """
    from ai_rfc.experiment import cli as experiment_cli

    argv = ["profile", "init", "--root", str(tmp_path)]
    via_root = cli.main(["experiment", *argv])
    root_out = capsys.readouterr().out
    via_module = experiment_cli.main(argv)
    assert via_root == via_module and capsys.readouterr().out == root_out


def test_the_module_door_onto_the_instrument_still_runs(tmp_path):
    """Spec §3: the server core and the raw arm invoke ``python -m`` until CLI-3.

    A subprocess rather than ``cli.main``, because what those callers depend on
    is ``__main__.py`` over an importable package, and an in-process call
    exercises neither. It runs a verb rather than ``--help`` deliberately:
    ``--help`` exits inside argparse before dispatch, so it cannot tell a
    wired ``main`` from a wired ``main`` over a dispatch that reaches nothing.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_rfc.experiment",
            "profile",
            "init",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "claude auth login" in result.stdout


def test_pipeline_from_choices_follow_pipeline_order(capsys):
    with pytest.raises(SystemExit):
        cli.main(["pipeline", "run", "--help"])
    out = capsys.readouterr().out
    names = [stage.name for stage in STAGES]
    positions = [out.find(name) for name in names]
    assert positions == sorted(positions) and -1 not in positions


def test_config_example_prints_a_loadable_starter(tmp_path, capsys):
    from ai_rfc.config import load_config

    assert cli.main(["config", "example"]) == 0
    text = capsys.readouterr().out
    (tmp_path / "recon.yaml").write_text(text)
    assert load_config(tmp_path / "recon.yaml").name == "example"
    assert cli.main(["config", "reference"]) == 0
    assert "| `source.pin` |" in capsys.readouterr().out
