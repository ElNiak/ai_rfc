"""The one door.

Every verb mounts under ``ai-rfc``, forwards untouched, and shares one help.
"""

import importlib

import pytest

from ai_rfc import __version__, cli
from ai_rfc.entrypoints import ENTRY_POINTS
from ai_rfc.pipeline.stages import STAGES

pytestmark = pytest.mark.unit


def test_help_lists_every_registered_verb_once_under_its_section(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for entry in ENTRY_POINTS:
        assert (
            out.count(f"  {entry.verb} ") == 1 or out.count(f"  {entry.verb}\n") == 1
        ), entry.verb
        assert entry.section in out


def test_every_registered_module_exposes_the_configure_run_contract():
    for entry in ENTRY_POINTS:
        module = importlib.import_module(entry.module)
        assert callable(module.configure) and callable(module.run), entry.module
        assert callable(module.main), entry.module


def test_version_names_the_one_program(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"ai-rfc {__version__}\n"


def test_an_unknown_verb_exits_two(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["frobnicate"])
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_a_verb_forwards_to_its_module_run(tmp_path, capsys):
    """`ai-rfc pipeline status WS` and `python -m ai_rfc.pipeline status WS` agree."""
    from ai_rfc.pipeline import cli as pipeline_cli

    empty = tmp_path / "ws"
    empty.mkdir()
    via_root = cli.main(["pipeline", "status", str(empty)])
    root_out = capsys.readouterr().out
    via_module = pipeline_cli.main(["status", str(empty)])
    assert via_root == via_module and capsys.readouterr().out == root_out


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
