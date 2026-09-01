"""Judge the guard against real traffic and against the shapes it must refuse.

The corpus is the 31 Bash commands run B1 actually issued before the guard
defect aborted it. Synthetic cases alone could not have caught that defect --
it took a real agent writing a backslash continuation and a paged redirection
-- so the traffic is frozen into a fixture and asserted here.

One edit has been made to that recording: the CLI was called ``arfc`` when B1
ran and is now ``ai_rfc``, so the program name was substituted throughout. What
the corpus is evidence *for* is the command shapes a real agent produced --
quoting, continuations, pipes -- and those are untouched. The commands are
therefore no longer verbatim; regenerating the corpus needs a fresh campaign.

A corpus of one arm's traffic can only show the guard admits what it should.
Because every fix so far has *loosened* the guard, the constructed cases below
carry the other direction: the shapes it must still refuse.
"""

import json
from pathlib import Path

import pytest

from experiment.arms import ARMS, arm_profile
from experiment.audit import bash_surface, in_arm
from experiment.enforcement import bash_prefixes, is_allowed

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "enforcement"
ARM_B = ("ai_rfc ",)
ARM_C = (
    "python -m panther.plugins.services.testers.ai_rfc",
    "git ",
    "sqlite3 ",
)


def _corpus():
    document = json.loads((FIXTURES / "run-b1-arm-b.json").read_text())
    # The fixture is captured evidence from run B1 and keeps the key it was
    # recorded under; only the code's word for it changed.
    prefixes = tuple(document["families"])
    return [
        pytest.param(case["command"], case["allowed"], case["why"], prefixes)
        for case in document["cases"]
    ]


@pytest.mark.parametrize("command,allowed,why,prefixes", _corpus())
def test_run_b1_traffic_is_judged_as_read(command, allowed, why, prefixes):
    assert is_allowed(command, prefixes) is allowed, why


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
        # Splitting on them refuses a command that runs one in-prefix program.
        'ai_rfc corpus-query "SELECT a || b FROM commits"',
        'ai_rfc corpus-query "SELECT sha FROM commits; SELECT 1"',
        "ai_rfc corpus-query \"SELECT 1 WHERE x='a;b'\"",
        # An in-prefix command may page its own output.
        "ai_rfc cluster-get c1 --patch 2>&1 | head -c 20000",
        "ai_rfc status | wc -l",
    ],
)
def test_quoted_operators_do_not_separate_commands(command):
    assert is_allowed(command, ARM_B) is True


@pytest.mark.parametrize(
    "command",
    [
        # A pipe target that is not a pager is a second program.
        "ai_rfc status | tee /tmp/x",
        "ai_rfc status | sh",
        'ai_rfc status | python -c "import os"',
        # A pager does not license what follows the group.
        "ai_rfc status | head; python evil.py",
        # Arm C's surfaces are not arm B's.
        "git log --oneline",
        "sqlite3 corpus.db .tables",
        # A prefix check cannot see through substitution, so it fails closed.
        "ai_rfc status $(whoami)",
        "ai_rfc status `whoami`",
        # Nor through a quote that never closes.
        'ai_rfc corpus-query "SELECT 1',
        "ai_rfc corpus-query 'SELECT 1",
    ],
)
def test_out_of_prefix_shapes_are_still_refused(command):
    assert is_allowed(command, ARM_B) is False


@pytest.mark.parametrize(
    "command,allowed",
    [
        ("git log --oneline", True),
        ('sqlite3 corpus.db "SELECT 1; SELECT 2"', True),
        ("python -m panther.plugins.services.testers.ai_rfc.cli status", True),
        ("ai_rfc status", False),
        ("git log | sh", False),
    ],
)
def test_arm_c_prefixes_are_judged_independently(command, allowed):
    assert is_allowed(command, ARM_C) is allowed


def test_arm_a_has_no_bash_surface_so_nothing_is_allowed():
    """Arm A declares no Bash prefix; an empty prefix list must refuse all."""
    assert is_allowed("ai_rfc status", ()) is False
    assert is_allowed("echo hello", ()) is False


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("command,allowed,why,prefixes", _corpus())
def test_the_audit_reads_a_command_the_way_the_guard_did(
    command, allowed, why, prefixes, arm
):
    """The enforcement and the measurement must not disagree about a command.

    They are separate readers of the same string, coupled only by convention,
    and they have drifted twice: the audit once split on operators wherever
    they appeared, and later judged arm membership from the collapsed surface
    label, which cannot represent a line reaching two prefixes the arm holds.

    Asserting agreement over every arm, rather than a fixed verdict for arm B,
    is what closes the second gap: arm B has one prefix, so no arm-B command
    can span two, and the earlier single-arm version of this test could never
    have failed on it.
    """
    guard = is_allowed(command, bash_prefixes(arm_profile(arm)))
    audit = in_arm("Bash", {"command": command}, bash_surface(command), arm)
    assert audit is guard, f"{arm}: {why}"
    if arm == "B":
        assert guard is allowed, why


@pytest.mark.parametrize(
    "command,allowed,why",
    [
        # The line aioquic pilot run C1 issued at index 23. Both groups are
        # surfaces arm C holds, so the guard admitted it -- and the audit,
        # judging from the collapsed `bash:mixed` label, reported the run as an
        # integrity violation for a call the arm was entitled to make.
        (
            "sqlite3 -version; git -C /w/clone rev-parse HEAD",
            True,
            "two groups, each a prefix arm C holds",
        ),
        (
            "git -C /w/clone rev-parse HEAD && sqlite3 corpus.db .tables",
            True,
            "the same, joined by &&",
        ),
        # One group outside the arm still takes the whole line out of it.
        ("sqlite3 -version; ai_rfc status", False, "ai_rfc is arm B's surface"),
        ("git log --oneline; echo hi", False, "echo is no arm's surface"),
    ],
)
def test_a_line_spanning_two_held_prefixes_stays_in_arm_c(command, allowed, why):
    """A multi-prefix line is in arm when every one of its prefixes is."""
    assert is_allowed(command, ARM_C) is allowed, why
    assert in_arm("Bash", {"command": command}, bash_surface(command), "C") is allowed
