"""Resolve the environment handles everything here depends on: a required
workspace and an optional toolchain, both derived from one config.

``AI_RFC_CONFIG`` names the ``recon.yaml`` this process operates under, and is
the single handle every front door already passes (D57). It is required;
nothing guesses, because a tool quietly operating on the wrong workspace is the
kind of failure that looks like success. The substrate is an installed package,
so no checkout has to be located or placed on ``sys.path``.

The workspace is the config's **own directory** when that directory is a sealed
workspace — the config is named ``recon.yaml`` and an ``init.json`` sits beside
it, which is what ``init`` writes and nothing else does. Both halves are
required: a stray ``init.json`` beside a config under another name has not
initialised anything. Otherwise the config is an operator's, kept wherever they
like, and the workspace is the ``workspace:`` field it declares.

Deriving a sealed workspace from its own ``workspace:`` field would be wrong,
for the reason :func:`ai_rfc.lifecycle.common.load_pair` records: a campaign
pristine is sealed with its own root written into that field and then *copied*
per run, so every copy names the tree it was made from.

Which is why an **unsealed directory that is nonetheless a workspace is
refused, never resolved**. A run's copy that has lost its ``init.json`` would
otherwise fall through to that same ``workspace:`` field and send every tool to
the pristine — the shared baseline every other run is copied from, and the one
tree where a stray write contaminates a whole campaign. Falling back is
available only where the fallback is harmless: a config with no workspace
around it at all.

``AI_RFC_WORKSPACE`` is still exported — by :mod:`ai_rfc.driver.session`,
:mod:`ai_rfc.driver.arms` and :mod:`ai_rfc.experiment.preflight` — because arm
B's and arm C's rendered prompts spell paths as ``$AI_RFC_WORKSPACE/...`` at
some twenty sites. It is no longer what this module reads.

``AI_RFC_TOOLCHAIN`` is optional; when set it names the ``toolchain.json`` the
build gate uses. The config's own ``toolchain:`` outranks it, because that
field is defaulted rather than optional (``config.py:642``) — reading it
straight through would hand every context a toolchain and make
``core/draft.py``'s ``if ctx.toolchain is not None:`` always true, so the field
is tested for a *file* rather than against ``None``. Outranked is not
unchecked: a handle that names no file is still refused, whatever the config
declares, because it is an operator's typo either way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import load_config
from ..driver import DriverError
from ..driver.stop import _quoted
from ..ledger import PRISTINE_RECORD
from ..lifecycle.common import CONFIG_ENV
from ..lifecycle.workspace import CONFIG_FILE as CONFIG_FILE_NAME
from ..lifecycle.workspace import Layout, missing_context_handles

#: The optional handle naming a ``toolchain.json`` outright.
TOOLCHAIN_ENV = "AI_RFC_TOOLCHAIN"


class EnvError(RuntimeError):
    """Raised when the environment contract is not met."""


@dataclass(frozen=True)
class Context:
    """The resolved handle every core operation receives."""

    workspace: Path
    toolchain: Path | None = None

    @property
    def manifest(self) -> Path:
        """The workspace manifest."""
        return self.workspace / "manifest.yaml"

    @property
    def questions(self) -> Path:
        """The workspace question register."""
        return self.workspace / "questions.yaml"

    @property
    def revisions(self) -> Path:
        """The workspace revision map."""
        return self.workspace / "revisions.yaml"


def _workspace_markers(layout: Layout) -> list[str]:
    """Which artifacts of a built workspace ``layout.root`` already holds.

    A predicate over the category "is this a workspace", rather than a check
    for the one file that happens to be missing: every one of these is written
    by a stage or by ``init``, and none of them appears in a directory an
    operator merely keeps a ``recon.yaml`` in.

    Args:
        layout: The candidate workspace.

    Returns:
        The names present, in a stable order; empty when this is not a
        workspace.
    """
    candidates = (
        layout.manifest,
        layout.revisions,
        layout.questions,
        layout.clone,
        layout.corpus,
        layout.timeline,
        layout.clusters,
        layout.checkpoints,
        layout.draft,
        layout.out,
        layout.refcache,
        layout.root / PRISTINE_RECORD,
    )
    return [path.name for path in candidates if path.exists()]


def _looks_like_a_workspace(layout: Layout) -> bool:
    """Whether ``layout.root`` is a workspace that has lost its seal.

    The config itself is not evidence: this is asked only on the branch that
    is *defined* by a config being there, and an operator's desk holds one.
    Anything else on the list is a stage's or ``init``'s output, so one is
    enough.

    Erring towards refusal is deliberate. A desk that happens to hold a
    ``draft/`` beside its ``recon.yaml`` is refused with a message naming the
    fix; the alternative, silently resolving a run's workspace to the pristine
    it was copied from, is the failure that looks like success.

    Args:
        layout: The candidate workspace.

    Returns:
        True when a built workspace's artifacts are present, so resolving
        elsewhere would be operating on the wrong tree.
    """
    return bool(_workspace_markers(layout))


def _unsealed_refusal(config_path: Path, layout: Layout) -> str:
    """Why this config's own directory is refused rather than resolved.

    **Two** conditions reach this refusal and they are different mistakes: the
    config is not the workspace's own ``recon.yaml``, or the ``init.json``
    beside it is gone. A message asserting the one that does **not** hold tells
    the operator to restore a file sitting right there, so each branch
    describes only the condition it tested. Where both hold, the first is
    reported and every clause it prints is true of that case as well — the
    remedy falls back to the placeholder, because the seal it would otherwise
    name is itself missing.

    Neither branch describes a history this workspace may not have: a
    workspace sealed in place carries its **own** root in ``workspace:``, so
    *"the tree it was copied from"* is false there even though refusing is
    still right.

    What both share is the reason for refusing rather than falling back: the
    ``workspace:`` field is resolved only where no workspace surrounds the
    config, because a run's copy carries the pristine's root in that field and
    a tool sent there writes into the baseline every other run is made from.

    The remedy is a line to paste, so **its** grammar is a shell's while the
    rest of the message's is lines, and the path in it goes through
    :func:`ai_rfc.driver.stop._quoted` — the spelling that already knows
    ``shlex.quote`` alone is not enough, since a quoted newline still renders
    as two physical lines. It names the sealed config outright when one is
    there to name, both halves of the seal present, so that pointing at it
    resolves rather than landing back on this refusal. It falls back to the
    placeholder in the two cases where no path can be named: nothing sealed
    sits beside this config, or the one that does cannot be written as a
    single shell line. A path that does not resolve, and a path that does not
    survive being pasted, are the same unfollowable instruction this function
    exists to stop emitting.

    Args:
        config_path: The resolved ``AI_RFC_CONFIG``.
        layout: The candidate workspace, which is that config's own directory.

    Returns:
        The refusal text, ending in the one actionable remedy.
    """
    present = ", ".join(_workspace_markers(layout))
    remedy = f"{CONFIG_ENV}=<workspace>/{CONFIG_FILE_NAME}"
    if layout.config.is_file() and layout.init_record.is_file():
        try:
            remedy = f"{CONFIG_ENV}={_quoted(str(layout.config), 'the config')}"
        except DriverError:
            # ``_quoted`` refuses rather than escapes, which is right for a
            # line to paste: a mangled one that is nonetheless executable is
            # worse than none. Degrading to the placeholder rather than
            # letting that refusal propagate is what keeps *this* refusal
            # deliverable — :func:`ai_rfc.driver.printable` records that
            # raising on a reporting path replaces the answer with a second
            # failure, and here the refusal is the answer.
            pass
    if layout.config != config_path:
        return (
            f"{CONFIG_ENV}={config_path} sits in what looks like a workspace "
            f"({present} present) but is not that workspace's "
            f"{CONFIG_FILE_NAME}, so nothing here sealed it to this tree and "
            f"its workspace: field is not resolved while a workspace is "
            f"around it. Set {remedy}"
        )
    return (
        f"{CONFIG_ENV}={config_path} sits in what looks like a workspace "
        f"({present} present) whose {layout.init_record.name} is missing. "
        f"Without the seal a run's copy cannot be told from the tree it was "
        f"made from, and a copy's workspace: field names that tree, so the "
        f"field is not resolved here. Restore {layout.init_record}, or set "
        f"{remedy}"
    )


def _resolve_toolchain(declared: Path | None) -> Path | None:
    """The ``toolchain.json`` this context builds against, or ``None``.

    The config's field outranks the environment handle (D57's D2), but the
    handle keeps its own guard: a ``AI_RFC_TOOLCHAIN`` that names no file is
    an operator's typo either way, and swallowing it whenever the config
    happened to declare a real record would make the typo invisible.

    ``declared`` is never ``None`` in practice — ``config.py``'s table defaults
    it to ``<experiments root>/tools/toolchain.json`` — which is exactly why
    the field is tested with :meth:`~pathlib.Path.is_file` rather than against
    ``None``: reading it straight through would hand every context a toolchain
    and make ``core/draft.py``'s ``if ctx.toolchain is not None:`` always true.

    The ``is not None`` below survives that, deliberately, because *in
    practice* is a property of one producer and not of the type:
    :attr:`ai_rfc.config.ReconConfig.toolchain` is declared ``Path | None``
    and a config built by hand rather than loaded really does carry ``None``
    (``tests/driver/test_sweep.py`` does exactly that, and
    ``driver/sweep.py``'s own gate tests for it). Dropping the test would make
    this function total only over the values ``load_config`` happens to
    return, and ``mypy`` says so.

    Args:
        declared: The config's ``toolchain:`` field, defaulted or not.

    Returns:
        The resolved record, or ``None`` when neither source names a file.

    Raises:
        EnvError: If ``AI_RFC_TOOLCHAIN`` is set and does not name a file.
    """
    handle = os.environ.get(TOOLCHAIN_ENV)
    from_handle: Path | None = None
    if handle:
        from_handle = Path(handle).expanduser().resolve()
        if not from_handle.is_file():
            raise EnvError(f"{TOOLCHAIN_ENV}={from_handle} is not a file")
    if declared is not None and declared.is_file():
        return declared.resolve()
    return from_handle


def resolve_context() -> Context:
    """Read and validate the environment contract.

    Returns:
        The resolved context.

    Raises:
        EnvError: If ``AI_RFC_CONFIG`` is missing or does not name a file, if
            its directory is a workspace this config is not the seal of —
            either because the config is not that workspace's ``recon.yaml``
            or because its ``init.json`` is gone, which
            :func:`_unsealed_refusal` tells apart — if the workspace it
            resolves to is not a directory, or if ``AI_RFC_TOOLCHAIN`` is set
            and does not name a file.
        ConfigError: If the config does not validate. Deliberately not wrapped:
            :class:`~ai_rfc.config.ConfigParseError` is a subclass carrying the
            YAML parser's own multi-line block, and a caller dispatching on the
            type is what keeps that block intact.
    """
    config_env = os.environ.get(CONFIG_ENV)
    if not config_env:
        raise EnvError(
            f"{CONFIG_ENV} must be set; refusing to guess which workspace "
            "to operate on"
        )
    config_path = Path(config_env).expanduser().resolve()
    if not config_path.is_file():
        raise EnvError(f"{CONFIG_ENV}={config_path} is not a file")
    layout = Layout(config_path.parent)
    config = load_config(config_path)
    if layout.config == config_path and not missing_context_handles(layout.root):
        workspace_path = layout.root
    else:
        if _looks_like_a_workspace(layout):
            raise EnvError(_unsealed_refusal(config_path, layout))
        workspace_path = config.workspace.expanduser().resolve()
    if not workspace_path.is_dir():
        raise EnvError(
            f"workspace {workspace_path}, from {CONFIG_ENV}={config_path}, "
            "is not a directory"
        )
    toolchain = _resolve_toolchain(config.toolchain)
    return Context(workspace=workspace_path, toolchain=toolchain)
