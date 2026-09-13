"""The one property a diagnostic's **raise site** can assert about its own text.

:func:`ai_rfc.lifecycle.common.report` and
:func:`~ai_rfc.lifecycle.common.report_structured` differ in exactly one
character: whether ``\\n`` survives. Deciding which to call inside an ``except``
clause is the wrong granularity, and this row proved it in both directions.
Routing a whole family through the structured verb let an operator-controlled
``recon.yaml`` path forge a second stderr line — a fabricated ``resume:``
instruction. Routing it through the collapsing verb instead pointed a YAML
parser's ``^`` at nothing. A clause catches a *family*; "these line breaks are
mine" is true of one raise site at a time.

So the raise site declares it and the handler tests for it. The test is one
``isinstance`` over this category, which is the shape
:func:`ai_rfc.driver.printable` gives for :meth:`str.isprintable`: a predicate
over a category, not an enumeration of the cases somebody thought of. Which is
why all **three** producers :func:`~ai_rfc.lifecycle.common.report_structured`
names join it — :class:`ai_rfc.config.ConfigParseError`,
:class:`ai_rfc.ledger.LedgerParseError` and
:class:`ai_rfc.toolchain.ToolchainBuildError`. A predicate that omitted a known
member of its own category would be an enumeration of the two that a review
happened to surface.

This module imports nothing, so the leaves that raise these errors —
:mod:`ai_rfc.config`, :mod:`ai_rfc.ledger` and :mod:`ai_rfc.toolchain` — can
reach it.
"""

from __future__ import annotations


class StructuredDiagnostic:
    """Mixin for an exception whose message is a heading over a block.

    The two halves are kept apart because they are escaped differently:

    * ``structured_context`` — one line the raising module **composed**, with
      a value interpolated into it (a path, a key) or none at all. A handler
      escapes it: a config path carrying a newline would otherwise forge a
      second diagnostic beneath the first.
    * ``structured_block`` — what a **parser or tool emitted**, breaks and
      all. A handler prints it with the breaks intact, because a
      :class:`yaml.YAMLError` ends in a ``^`` under the offending column and
      position is the whole of its meaning, and because a build tool's stderr
      tail is the only account of why a build failed.

    Splitting at construction rather than at the handler is the point: a
    handler that re-split ``str(error)`` would be guessing where the value
    ended, and the guess is what the two halves exist to remove.

    **The join is a newline**, so ``str()`` reads as the two lines a handler
    prints. That is byte-for-byte what the three ``f"…:\\n{stderr[-2000:]}"``
    sites of :mod:`ai_rfc.toolchain` already carried; the two parse errors
    gain a break where they had ``": "``, which is the same split their
    handler was already making visible. Nothing reads these messages back —
    the four sites that re-wrap one into another exception type
    (:mod:`ai_rfc.experiment.cli`, :mod:`ai_rfc.pipeline.state`,
    :mod:`ai_rfc.draft.completeness`) interpolate a block that was multi-line
    already.

    Mix it in **first**, before the exception base, so this ``__init__`` is
    the one that runs: ``class ConfigParseError(StructuredDiagnostic,
    ConfigError)``. It is deliberately not an :class:`Exception` subclass, so
    that ``except StructuredDiagnostic`` is a ``TypeError`` rather than a
    quiet second way to do what only :func:`isinstance` should.
    """

    structured_context: str
    structured_block: str

    def __init__(self, context: str, block: str) -> None:
        """Join the two halves for ``str()`` and keep each for a handler.

        Args:
            context: The composed half, with values interpolated. One line,
                and the heading the block sits under.
            block: The producer's own text, whose line breaks are its meaning.
        """
        # ``super()`` is the concrete exception base at runtime, because every
        # subclass mixes this in first — but read alone this class has only
        # ``object``, whose ``__init__`` takes no message, and that is what
        # mypy sees. The ignore records the invariant rather than weakening
        # it: giving this class an ``Exception`` base to satisfy the checker
        # would also make ``except StructuredDiagnostic`` legal, which is the
        # one thing a marker orthogonal to the exception *families* must not
        # be — a clause spelled that way would catch a ``LedgerParseError``
        # in a handler opened for ``recon.yaml``.
        super().__init__(f"{context}\n{block}")  # type: ignore[call-arg]
        self.structured_context = context
        self.structured_block = block
