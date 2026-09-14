"""The marker's own two properties: the join it makes, and surviving a copy.

``ai_rfc/diagnostics.py`` carries no logic beyond a constructor, which is why
what it can get wrong is not what it computes but what it leaves behind on the
instance. Both properties here are about that: ``str()`` is the two lines a
handler prints, and ``args`` is what every generic reconstruction of an
exception — :mod:`pickle`, :func:`copy.copy`, a process pool handing a worker's
failure back to its parent — is handed to rebuild from.
"""

from __future__ import annotations

import copy
import pickle

import pytest

from ai_rfc.config import ConfigParseError
from ai_rfc.ledger import LedgerParseError
from ai_rfc.toolchain import ToolchainBuildError

#: Every producer :func:`ai_rfc.lifecycle.common.report_structured` names.
STRUCTURED = (ConfigParseError, LedgerParseError, ToolchainBuildError)


@pytest.mark.parametrize("kind", STRUCTURED, ids=lambda kind: kind.__name__)
def test_the_join_is_one_newline(kind):
    """``str()`` reads as the two lines a handler prints, byte for byte.

    Load-bearing rather than incidental: the four sites that re-wrap one of
    these into another exception type interpolate ``str(error)``, so the
    message an operator sees there is this join and nothing else.
    """
    assert str(kind("recon.yaml is not valid:", "line 2\n     ^")) == (
        "recon.yaml is not valid:\nline 2\n     ^"
    )


@pytest.mark.parametrize("kind", STRUCTURED, ids=lambda kind: kind.__name__)
def test_a_structured_diagnostic_survives_pickling_and_copying(kind):
    """Generic reconstruction goes through ``args``, which is the joined half.

    :meth:`BaseException.__reduce__` returns ``(cls, self.args)``, and ``args``
    holds one joined string while this constructor takes two halves — so the
    default reduction rebuilds with an argument too few. Nothing in ``ai_rfc``
    crosses a process boundary today; the first campaign to run its arms in a
    pool is where an unpicklable failure stops being latent.
    """
    error = kind("recon.yaml is not valid:", "line 2\n     ^")

    for revived in (pickle.loads(pickle.dumps(error)), copy.copy(error)):
        assert type(revived) is kind
        assert str(revived) == str(error)
        assert revived.structured_context == error.structured_context
        assert revived.structured_block == error.structured_block
