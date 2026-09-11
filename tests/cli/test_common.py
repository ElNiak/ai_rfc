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

from ai_rfc.lifecycle.common import report, report_structured

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


# --- report_structured: the opt-out, and how wide it is ---------------------


def test_an_opted_in_diagnostic_keeps_its_own_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The direction the boundary broke: a tool's block stays a block."""
    report_structured("error: make deps failed:\nline one\n  line two\n    ^")

    assert capsys.readouterr().err.splitlines() == [
        "error: make deps failed:",
        "line one",
        "  line two",
        "    ^",
    ]


@pytest.mark.parametrize("character", [c for c in UNPRINTABLE if c != "\n"])
def test_the_opt_out_is_exactly_one_character_wide(
    capsys: pytest.CaptureFixture[str], character: str
) -> None:
    """Opting in buys ``\\n`` and nothing else.

    Every other break and control character is still escaped, line by line —
    so a ``\\r``, a NEL, a LINE SEPARATOR or an ANSI introducer smuggled into
    a subprocess's stderr cannot rewrite what the terminal already showed,
    even inside a diagnostic whose lines the caller vouched for.
    """
    report_structured(f"error: a{character}b")

    printed = capsys.readouterr().err
    assert character not in printed[:-1]
    assert len(printed.splitlines()) == 1


def test_the_default_still_collapses_what_the_opt_out_would_keep(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other direction: adding the opt-out did not weaken the default.

    The same text through both functions — four lines when vouched for, one
    when not. A test that only exercised the opt-in would pass just as well if
    ``report`` had quietly started keeping breaks too.
    """
    text = "error: make deps failed:\nline one\n  line two\n    ^"

    report(text)
    collapsed = capsys.readouterr().err
    report_structured(text)
    kept = capsys.readouterr().err

    assert len(collapsed.splitlines()) == 1
    assert "\\n" in collapsed
    assert len(kept.splitlines()) == 4


def test_a_single_line_message_is_identical_through_both(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The opt-out only ever differs where a break already exists.

    This is what bounds the blast radius of routing a whole ``except`` clause
    through it: the toolchain verb also catches ``OSError``, whose message is
    one line.
    """
    report("error: /w/tools/toolchain.json: No such file or directory")
    first = capsys.readouterr().err
    report_structured("error: /w/tools/toolchain.json: No such file or directory")

    assert capsys.readouterr().err == first


@pytest.mark.parametrize("verb", ["run", "status", "verify", "doctor", "init"])
def test_every_verb_that_reads_a_config_keeps_the_parser_s_lines(
    tmp_path, capsys: pytest.CaptureFixture[str], verb: str
) -> None:
    """The opt-out is per-handler, so each of the five is its own way to miss it.

    ``ConfigParseError`` reaches every verb that loads a ``recon.yaml``, and
    each one names it in its own ``except`` clause — five independent edits.
    Testing only ``run`` proved insufficient in practice: ``doctor``'s clause
    was written against an import that was never added, and the whole CLI
    suite still passed because no test drove ``doctor`` at a malformed file.

    The caret is the assertion because it is the part that cannot survive a
    collapse: its meaning is the column it sits under.
    """
    from ai_rfc.cli import main

    config_path = tmp_path / "recon.yaml"
    config_path.write_text("name: fixture\n  bad: [unclosed\n")

    assert main([verb, "--config", str(config_path)]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) > 1
    assert any(line.strip() == "^" for line in lines)


def test_a_legitimate_accented_path_is_printed_untouched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The guard names a category, so it cannot be tightened into refusing all.

    A reconstruction lives wherever the operator put it, and a path is the
    value these lines interpolate most often.
    """
    report("error: /w/reconstruction-café/recon.yaml is not an initialised workspace")

    assert "café" in capsys.readouterr().err
