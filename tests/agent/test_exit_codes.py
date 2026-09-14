"""A core's exit code reaches the caller as the number the core reported.

Moved here from ``tests/server/test_cli_exit_codes.py`` with the parser it
drove: ``checkpoint`` is one verb of the root tree now, not of a second one.
"""

from __future__ import annotations

import pytest

from ai_rfc import cli
from ai_rfc.server.core import gates

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_no_verb_reinterprets_the_cores_exit_code(code, workspace, monkeypatch):
    """The checkpoint branch alone collapsed every non-zero code to 1.

    The ``workspace`` fixture is required rather than incidental:
    :func:`ai_rfc.agent.perform` resolves the context before it dispatches and
    returns 1 on an ``EnvError``, so without a resolvable workspace every
    parametrisation would return 1 and agree with itself for the wrong reason.
    """
    monkeypatch.setattr(
        gates, "write_checkpoint", lambda *a, **k: {"exit_code": code, "stderr": []}
    )
    assert cli.main(["checkpoint", "c0001-x"]) == code
