"""What the root door writes to stderr when argparse refuses the invocation.

``tests/cli/test_root.py`` asserts what the door *lists*; this asks what it
*says* when it says no. The two are separate files because the hazard here is
not the table's contents but the grammar of one stderr line: argparse composes
its refusals by interpolating the operator's own tokens, and a line an
operator may read as the tool's verdict — or copy and run — is the worst place
for a value to arrive unescaped.
"""

from __future__ import annotations

import pytest

from ai_rfc import cli

pytestmark = pytest.mark.unit

#: A second line that reads as a diagnostic of the tool's own. ``error: `` is
#: the prefix every composed refusal in this package opens with, so a forged
#: copy is indistinguishable from a real one by anything but its provenance.
FORGED = "error: workspace destroyed"


def test_an_unrecognised_argument_cannot_forge_a_stderr_line(capsys):
    """argparse interpolates the leftover tokens with ``%s``, not ``%r``.

    Reproduced before this was written, through a mounted verb: ``ai-rfc
    doctor --config x 'evil<break>error: workspace destroyed'`` printed four
    stderr lines, the fourth being the forgery standing alone underneath a
    real diagnostic. The token is the operator's, and after Task 6 fifteen
    more verbs reach this branch.

    The escape is asserted as well as the forgery's absence: without it a
    refusal that merely *dropped* the token would pass, and dropping it would
    lose the operator the one thing the message is for.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["doctor", "--config", "x", f"evil\n{FORGED}"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    # The usage line is genuinely two lines, so a count proves nothing: no
    # line may *begin* with the forgery.
    assert not any(line.startswith(FORGED) for line in err.splitlines())
    assert "unrecognized arguments" in err
    assert "\\n" in err


def test_the_refusal_still_names_the_program_and_prints_the_usage(capsys):
    """The escaping must not cost the message its shape.

    ``print_usage`` runs before the diagnostic and the diagnostic still opens
    with the parser's ``prog``, so an operator sees what to type and which
    command refused them — the two halves argparse's own ``error()`` writes.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["doctor", "--config", "x", "spare"])
    assert excinfo.value.code == 2
    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "usage: ai-rfc <verb> [args]"
    assert lines[-1].startswith("ai-rfc: error: unrecognized arguments: spare")
