"""Everything that launches and reads one ``claude -p`` session.

This package is the lower layer: :mod:`ai_rfc.experiment` imports from here,
and nothing here may import from ``ai_rfc.experiment``. The instrument was
split out over this package precisely so production and the three-arm
experiment share one spawn path rather than two that drift.
"""

from __future__ import annotations


class DriverError(RuntimeError):
    """A session could not be launched, read, or accounted for."""
