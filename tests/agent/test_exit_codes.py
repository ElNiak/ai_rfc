"""Every verb that carries a core exit code carries it whole.

Moved here from ``tests/server/test_cli_exit_codes.py`` with the parser it
drove. It arrived guarding one branch of six: ``checkpoint`` was the branch
that had actually collapsed every non-zero code to 1, so it was the branch
that got a test, and the claim the row makes — that a substrate code reaches
the caller unchanged wherever a verb reports one — was guarded nowhere else.
An existing test asserts ``== 0`` for the two gate verbs, which a collapse to
1 passes as readily as the truth does.

Six verbs return a core's code rather than composing one, and all six are
here. The mutation that proves the parametrisation has teeth is the historical
defect itself — ``return 1 if result["exit_code"] else 0`` in place of
``return int(result["exit_code"])`` — applied to each verb in turn; see the
report for the runs.
"""

from __future__ import annotations

import pytest

from ai_rfc import cli

pytestmark = pytest.mark.unit

#: ``(argv, core module, core function)`` for every verb that surfaces a
#: core's exit code. The module is named by dotted path rather than imported
#: at the top, because each verb imports its core inside its own body (D13);
#: what makes the patch reach that import is that the verb then reads the
#: attribute off the module object, which is the same object ``import_module``
#: hands back from ``sys.modules``. Read before written: all six verbs spell
#: it ``gates.write_checkpoint(...)`` and not ``from ... import``, so all six
#: are patchable this way.
BRANCHES = [
    (["checkpoint", "c0001-x"], "ai_rfc.server.core.gates", "write_checkpoint"),
    (["gate", "--strict"], "ai_rfc.server.core.gates", "manifest_gate"),
    (["citation-gate", "--strict"], "ai_rfc.server.core.gates", "citation_gate"),
    (
        ["revision", "tag", "draft-test-spec-00", "-m", "rev"],
        "ai_rfc.server.core.draft",
        "tag_revision",
    ),
    (["draft", "build"], "ai_rfc.server.core.build", "draft_build"),
    (["draft", "lint"], "ai_rfc.server.core.build", "draft_lint"),
]


@pytest.mark.parametrize("code", [0, 1, 2, 3])
@pytest.mark.parametrize(
    ("argv", "module", "function"),
    BRANCHES,
    ids=[" ".join(a[:2]) for a, _, _ in BRANCHES],
)
def test_no_verb_reinterprets_the_cores_exit_code(
    code, argv, module, function, workspace, monkeypatch, capsys
):
    """The code the core reported is the code the door returns.

    The ``workspace`` fixture is required rather than incidental:
    :func:`ai_rfc.agent.perform` resolves the context before it dispatches and
    returns 1 on an ``EnvError``, so without a resolvable workspace every
    parametrisation would return 1 and the table would agree with itself for
    the wrong reason.

    2 is included although argparse owns it: what is asserted is that the door
    does not *reinterpret* a number, and a door that special-cased 2 would be
    lying about a code it did not produce.
    """
    from importlib import import_module

    core = import_module(module)
    monkeypatch.setattr(
        core, function, lambda *a, **k: {"exit_code": code, "stderr": []}
    )
    assert cli.main(argv) == code
    capsys.readouterr()
