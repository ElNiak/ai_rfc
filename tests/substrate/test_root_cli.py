"""The one door: ``ai-rfc <verb>`` forwards to the registered sub-CLI."""

import re

import pytest

from ai_rfc import __version__, cli
from ai_rfc.entrypoints import ENTRY_POINTS

pytestmark = pytest.mark.unit


def test_help_lists_every_verb_in_registration_order(capsys):
    """Registration order is the workflow order; the listing must keep it.

    The needle is "exactly two spaces then a word": the second usage line is
    indented deeper, and a bare ``startswith("  ")`` would capture it.
    """
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    rendered = [
        line.split()[0] for line in out.splitlines() if re.match(r"^ {2}\S", line)
    ]
    assert rendered == [entry.verb for entry in ENTRY_POINTS]


def test_a_bare_invocation_is_a_usage_error(capsys):
    """Like ``panther``: usage printed, exit 2, because nothing was asked."""
    assert cli.main([]) == 2
    assert "usage: ai-rfc" in capsys.readouterr().err


def test_an_unknown_verb_exits_two(capsys):
    assert cli.main(["frobnicate"]) == 2
    assert "unknown verb" in capsys.readouterr().err


def test_version_names_the_door(capsys):
    assert cli.main(["--version"]) == 0
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
