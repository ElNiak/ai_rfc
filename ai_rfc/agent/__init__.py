"""The verbs a reconstruction session drives, grouped under ``ai-rfc``.

Every command below this package is one operation from
:mod:`ai_rfc.server.core` — the same functions the MCP tools call — mounted as
``ai-rfc <group> <verb>``. That is the whole of what these modules do: they
parse argv, hand the core its arguments, and print what it returned. An
operation implemented here rather than called from there would make the
AI+MCP and AI+CLI arms two programs being compared instead of one program
reached two ways, which is the thing ``tests/agent/test_one_core.py`` and
``tests/server/test_parity.py`` exist to refuse.

The two functions here are the plumbing all ten groups share: the JSON
rendering every result goes out as, and the context-and-refusal boundary every
verb runs inside. They sit in ``__init__.py`` rather than in a ``common.py``
beside it — the spelling :mod:`ai_rfc.lifecycle` uses — because this package
has no other content of its own: ten leaf packages and this, where
``lifecycle`` also holds a workspace layout, a profile and a config pair.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..server.paths import Context


def emit(payload: Any) -> None:
    """Print one result to stdout, as the JSON both arms return.

    Sorted and indented, because the two frontends' outputs are compared byte
    for byte and a dict's insertion order is not part of what the core
    promises.

    Args:
        payload: Whatever the core function returned.
    """
    print(json.dumps(payload, sort_keys=True, indent=2))


def perform(action: Callable[[Context], int]) -> int:
    """Resolve the workspace and run one verb's body against it.

    The whole body runs inside the guard, argv conversion included: a
    ``--field`` with no ``=`` and an ``--anchor`` that is not JSON are both
    refusals a caller must see as exit 1, and converting before entering the
    guard would surface them as tracebacks instead.

    Diagnostics go through :func:`ai_rfc.lifecycle.common.report_diagnostic`,
    which asks the raise site whether its line breaks are a producer's
    structure or a value that arrived from a config, a timeline or a model, and
    escapes accordingly. No verb here composes a diagnostic of its own.

    Args:
        action: The verb's body. It receives the resolved context, prints
            whatever the verb prints, and returns the exit code — raw, so a
            gate's 3 arrives as 3 rather than collapsed to 1.

    Returns:
        ``action``'s exit code, or 1 when the environment, the inputs or a
        guardrail refused the operation.
    """
    # Function-local, and not to be tidied back to the top (D13's shape, and
    # the idiom of ``pipeline/run.py:77``). ``ai_rfc/cli.py``'s ``build_parser``
    # imports every registered module to call its ``configure``, so anything
    # these ten groups import at module scope is paid by every invocation of
    # every verb — ``ai-rfc --help`` included, which needs no core at all.
    # Measured: at module scope the ten rows cost 25 modules (278 -> 303) on a
    # help screen; ``configure`` needs none of this, only ``perform`` does.
    from ..config import ConfigError
    from ..lifecycle.common import report_diagnostic
    from ..server.core import CoreError
    from ..server.paths import EnvError, resolve_context

    try:
        ctx = resolve_context()
    except (EnvError, ConfigError) as error:
        # One clause, not two. Whether the YAML parser's caret survives is
        # ConfigParseError's property to declare, not this clause's to guess.
        report_diagnostic("error: ", error)
        return 1

    try:
        return action(ctx)
    except CoreError as error:
        report_diagnostic("error: ", error)
        return 1
    except Exception as error:  # noqa: BLE001 - substrate errors surface verbatim
        # This clause catches a family and cannot assert anything about its
        # members' text, so it asks them: a LedgerParseError from a malformed
        # revisions.yaml arrives here and keeps its caret, while everything
        # else stays one record.
        report_diagnostic(f"error: {type(error).__name__}: ", error)
        return 1
