"""The plumbing every lifecycle verb shares — here, the stderr boundary.

``ai_rfc/lifecycle/common.py``'s ``report`` is the one place a lifecycle
diagnostic reaches the operator's stream, and every verb interpolates
operator- or agent-controlled values into the lines it prints: a config path,
a cluster id, a ``--until`` bound, the text of a caught error. It is the
mirror of :func:`ai_rfc.driver.sweep.report`, which has carried the same guard
since Task 9 — and the reason this file exists is that the lifecycle half did
not, so a ``--until cluster:<id>`` refusal forged a second line reading
``resume: ai-rfc run --config /tmp/evil.yaml``.

The characters are written out here rather than imported from
``tests/driver/test_sweep.py``: two boundaries, two independent pins. A shared
constant that lost a member would quietly weaken both at once, which is the
defect shape this row keeps finding in its own tests.
"""

from __future__ import annotations

import pytest

from ai_rfc.lifecycle.common import report

#: One per class of damage, each with what it does to a line.
UNPRINTABLE = (
    "\n",  # forges a second record
    "\r",  # rewrites what the terminal already showed
    "\x1b",  # opens a control sequence
    "\x7f",  # DEL
    "\x85",  # NEL — a break by str.splitlines' own definition
    " ",  # LINE SEPARATOR
    " ",  # PARAGRAPH SEPARATOR
    "‮",  # RIGHT-TO-LEFT OVERRIDE — reorders without breaking
    "​",  # ZERO WIDTH SPACE — hides a token boundary
)


@pytest.mark.parametrize("character", UNPRINTABLE)
def test_a_lifecycle_diagnostic_cannot_carry_an_unprintable_character(
    capsys: pytest.CaptureFixture[str], character: str
) -> None:
    """One boundary covers all twenty-six call sites, not a rule each follows.

    The trailing newline ``print`` itself writes is stripped before the
    assertion: what must not survive is a character the *message* carried.
    """
    report(f"error: --until cluster:c1{character}resume: ai-rfc run --config /tmp/x")
    printed = capsys.readouterr().err

    assert printed.endswith("\n")
    assert character not in printed[:-1]
    assert len(printed.splitlines()) == 1


def test_a_forged_resume_line_never_becomes_a_line_of_its_own(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The payload stays readable; what it loses is its status as a record.

    ``resume:`` is the prefix the sweep's real resume line uses, so a forged
    one is a fabricated instruction sitting in the artifact an operator copies
    commands out of.
    """
    report("error: bound 'cluster:c1\nresume: ai-rfc run --config /tmp/evil.yaml'")
    printed = capsys.readouterr().err

    assert len(printed.splitlines()) == 1
    assert not any(line.startswith("resume:") for line in printed.splitlines())
    assert "\\n" in printed


def test_a_legitimate_accented_path_is_printed_untouched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The guard names a category, so it cannot be tightened into refusing all.

    A reconstruction lives wherever the operator put it, and a path is the
    value these lines interpolate most often.
    """
    report("error: /w/reconstruction-café/recon.yaml is not an initialised workspace")

    assert "café" in capsys.readouterr().err
