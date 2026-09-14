"""D56's verb tree: every agent verb reachable as ``ai-rfc <group> <verb>``.

The parity CLI's twenty verbs were a second front door. Nineteen of them mount
here as grouped subcommands of the root, and ``status`` retires without one
(U3). ``draft``'s four came last because ``draft`` was already a leaf verb and
two ``add_parser("draft")`` calls cannot coexist.
"""

import pytest

from ai_rfc import cli

pytestmark = pytest.mark.unit

#: The mount table, spelled as an operator types it.
GROUPED = [
    "corpus query",
    "cluster get",
    "cluster next",
    "claim upsert",
    "claim check",
    "claim record-status",
    "question draft",
    "question export",
    "answer record",
    "revision record",
    "revision tag",
    "checkpoint",
    "gate",
    "citation-gate",
    "structure upsert",
    "draft commit",
    "draft build",
    "draft lint",
    "draft render",
]


@pytest.mark.parametrize("verb", GROUPED)
def test_the_grouped_verb_parses(verb):
    """D56's verb tree, one row at a time.

    ``--help`` exits 0 once mounted and 2 while unmounted, so the code must be
    asserted or the test can never go green: ``pytest.raises(SystemExit)``
    alone is satisfied by argparse refusing the verb outright.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(verb.split() + ["--help"])
    assert excinfo.value.code == 0


@pytest.mark.parametrize("verb", GROUPED)
def test_the_grouped_verbs_usage_names_the_whole_path(verb, capsys):
    """A leaf's usage line must name what the operator typed, not its group.

    The mount hands each ``configure`` the root's own subparser, so the nested
    tree inherits the door's ``prog``. A group that built a parser of its own
    would still parse — the test above would pass — and would print
    ``usage: ai-rfc`` or a bare ``usage: query`` here.
    """
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(verb.split() + ["--help"])
    assert f"usage: ai-rfc {verb}" in capsys.readouterr().out
