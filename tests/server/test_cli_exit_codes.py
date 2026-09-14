import pytest

from ai_rfc.server import cli
from ai_rfc.server.core import gates


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_no_verb_reinterprets_the_core_s_exit_code(code, workspace, monkeypatch):
    """server/cli.py:4-5 promises exit codes "pass through untouched"; the
    checkpoint branch alone collapsed every non-zero to 1. The `workspace`
    fixture is required: :254-259 resolves the context before dispatch and
    returns 1 on EnvError, so without it every parametrisation returns 1.
    """
    monkeypatch.setattr(
        gates, "write_checkpoint", lambda *a, **k: {"exit_code": code, "stderr": []}
    )
    assert cli.main(["checkpoint", "c0001-x"]) == code
