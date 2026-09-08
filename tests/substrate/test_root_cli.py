"""The one door: ``ai-rfc <verb>`` reaches the registered sub-CLI's ``run``."""

import re

import pytest

from ai_rfc import __version__, cli
from ai_rfc.entrypoints import ENTRY_POINTS

pytestmark = pytest.mark.unit


def test_help_lists_every_verb_in_registration_order(capsys):
    """Registration order is the workflow order; the listing must keep it.

    The needle is "exactly two spaces then a word", read over the verb table
    alone: argparse indents its own option rows the same way, so scanning the
    whole help would capture ``-h``, ``--version`` and the ``<verb>`` metavar
    as if they were commands. The table starts at the first section heading.
    """
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    first_heading = out.index(f"{ENTRY_POINTS[0].section}:")
    table = out[first_heading:]
    rendered = [
        line.split()[0] for line in table.splitlines() if re.match(r"^ {2}\S", line)
    ]
    assert rendered == [entry.verb for entry in ENTRY_POINTS]


def test_a_bare_invocation_is_a_usage_error(capsys):
    """Like ``panther``: usage printed, exit 2, because nothing was asked."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2
    assert "usage: ai-rfc" in capsys.readouterr().err


def test_an_unknown_verb_exits_two(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["frobnicate"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_version_names_the_door(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"ai-rfc {__version__}\n"


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[e.verb for e in ENTRY_POINTS])
def test_a_verb_forwards_its_arguments_untouched(entry):
    """argparse owns 2 for a malformed invocation, and the door must not relabel it."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main([entry.verb, "--no-such-flag"])
    assert exit_info.value.code == 2


def test_a_sub_cli_return_code_passes_through(tmp_path):
    """1 is *returned* by ``check`` for an unreadable manifest, never raised, so
    a door that dropped the return value would report success here."""
    missing = tmp_path / "missing.yaml"
    assert cli.main(["check", str(missing), "--out", str(tmp_path / "out")]) == 1
