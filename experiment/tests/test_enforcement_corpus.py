"""Judge the guard against real traffic and against the shapes it must refuse.

The corpus is the 31 Bash commands run B1 actually issued before the guard
defect aborted it. Synthetic cases alone could not have caught that defect --
it took a real agent writing a backslash continuation and a paged redirection
-- so the traffic is frozen into a fixture and asserted here.

A corpus of one arm's traffic can only show the guard admits what it should.
Because every fix so far has *loosened* the guard, the constructed cases below
carry the other direction: the shapes it must still refuse.
"""

import json
from pathlib import Path

import pytest

from experiment.audit import ALLOWED, bash_family
from experiment.enforcement import is_allowed

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "enforcement"
ARM_B = ("arfc ",)
ARM_C = (
    "python -m panther.plugins.services.testers.a_rfc",
    "git ",
    "sqlite3 ",
)


def _corpus():
    document = json.loads((FIXTURES / "run-b1-arm-b.json").read_text())
    families = tuple(document["families"])
    return [
        pytest.param(case["command"], case["allowed"], case["why"], families)
        for case in document["cases"]
    ]


@pytest.mark.parametrize("command,allowed,why,families", _corpus())
def test_run_b1_traffic_is_judged_as_read(command, allowed, why, families):
    assert is_allowed(command, families) is allowed, why


def test_the_corpus_covers_both_verdicts():
    """A corpus that only ever expects True would pass against a broken guard."""
    document = json.loads((FIXTURES / "run-b1-arm-b.json").read_text())
    verdicts = {case["allowed"] for case in document["cases"]}
    assert verdicts == {True, False}
    assert len(document["cases"]) == 31


@pytest.mark.parametrize(
    "command",
    [
        # SQL writes both of these routinely: || concatenates, ; terminates.
        # Splitting on them refuses a command that runs one in-family program.
        'arfc corpus-query "SELECT a || b FROM commits"',
        'arfc corpus-query "SELECT sha FROM commits; SELECT 1"',
        "arfc corpus-query \"SELECT 1 WHERE x='a;b'\"",
        # An in-family command may page its own output.
        "arfc cluster-get c1 --patch 2>&1 | head -c 20000",
        "arfc status | wc -l",
    ],
)
def test_quoted_operators_do_not_separate_commands(command):
    assert is_allowed(command, ARM_B) is True


@pytest.mark.parametrize(
    "command",
    [
        # A pipe target that is not a pager is a second program.
        "arfc status | tee /tmp/x",
        "arfc status | sh",
        'arfc status | python -c "import os"',
        # A pager does not license what follows the group.
        "arfc status | head; python evil.py",
        # Arm C's surfaces are not arm B's.
        "git log --oneline",
        "sqlite3 corpus.db .tables",
        # A prefix check cannot see through substitution, so it fails closed.
        "arfc status $(whoami)",
        "arfc status `whoami`",
        # Nor through a quote that never closes.
        'arfc corpus-query "SELECT 1',
        "arfc corpus-query 'SELECT 1",
    ],
)
def test_out_of_family_shapes_are_still_refused(command):
    assert is_allowed(command, ARM_B) is False


@pytest.mark.parametrize(
    "command,allowed",
    [
        ("git log --oneline", True),
        ('sqlite3 corpus.db "SELECT 1; SELECT 2"', True),
        ("python -m panther.plugins.services.testers.a_rfc.cli status", True),
        ("arfc status", False),
        ("git log | sh", False),
    ],
)
def test_arm_c_families_are_judged_independently(command, allowed):
    assert is_allowed(command, ARM_C) is allowed


def test_arm_a_has_no_bash_surface_so_nothing_is_allowed():
    """Arm A declares no Bash family; an empty family list must refuse all."""
    assert is_allowed("arfc status", ()) is False
    assert is_allowed("echo hello", ()) is False


@pytest.mark.parametrize("command,allowed,why,families", _corpus())
def test_the_audit_reads_a_command_the_way_the_guard_did(
    command, allowed, why, families
):
    """The enforcement and the measurement must not disagree about a command.

    They are separate readers of the same string, coupled only by convention,
    and they have drifted before: the audit once split on operators wherever
    they appeared, so a command the guard permitted could be classified out of
    its own arm and reported as an integrity violation. Whatever the guard
    lets through must land inside the arm's allowed surfaces, and whatever it
    refuses must not.
    """
    surface = bash_family(command)
    assert (surface in ALLOWED["B"]) is allowed, why
