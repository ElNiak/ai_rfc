"""Operator lifecycle verbs.

One config, one workspace, one command that does what is next.
"""

from __future__ import annotations


class LifecycleError(RuntimeError):
    """A lifecycle verb could not do what it was asked."""
