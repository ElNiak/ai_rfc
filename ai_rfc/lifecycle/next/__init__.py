"""``ai-rfc next``: perform exactly one action, then report what it did.

The package shares its name with the ``next`` builtin, and that is harmless:
nothing imports it bare. CLI-1's D1 requires a registered verb to be
``<package>/cli.py``, and :data:`ai_rfc.entrypoints.ENTRY_POINTS` names this
one as the dotted string ``ai_rfc.lifecycle.next.cli``, which
:func:`importlib.import_module` resolves without ever binding ``next`` in a
namespace where the builtin is wanted. Renaming the package to dodge the
collision would put the directory out of step with the verb an operator
types, which is the one thing the layout exists to keep true.
"""
