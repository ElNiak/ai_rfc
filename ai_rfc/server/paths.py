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

``AI_RFC_WORKSPACE`` is still exported — by :mod:`ai_rfc.driver.session`,
:mod:`ai_rfc.driver.arms` and :mod:`ai_rfc.experiment.preflight` — because arm
B's and arm C's rendered prompts spell paths as ``$AI_RFC_WORKSPACE/...`` at
some twenty sites. It is no longer what this module reads.

``AI_RFC_TOOLCHAIN`` is optional; when set it names the ``toolchain.json`` the
build gate uses. It is consulted only when the config's own ``toolchain:``
names no file: that field is defaulted rather than optional
(``config.py:642``), so reading it straight through would hand every context a
toolchain and make ``core/draft.py``'s ``if ctx.toolchain is not None:`` always
true.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import load_config
from ..lifecycle.common import CONFIG_ENV
from ..lifecycle.workspace import Layout

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


def resolve_context() -> Context:
    """Read and validate the environment contract.

    Returns:
        The resolved context.

    Raises:
        EnvError: If ``AI_RFC_CONFIG`` is missing or does not name a file, if
            the workspace it resolves to is not a directory, or if
            ``AI_RFC_TOOLCHAIN`` is set and does not name a file.
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
    sealed = layout.config == config_path and layout.init_record.is_file()
    config = load_config(config_path)
    workspace_path = layout.root if sealed else config.workspace.expanduser().resolve()
    if not workspace_path.is_dir():
        raise EnvError(f"workspace {workspace_path} is not a directory")
    toolchain: Path | None = None
    if config.toolchain is not None and config.toolchain.is_file():
        toolchain = config.toolchain.resolve()
    elif os.environ.get(TOOLCHAIN_ENV):
        toolchain = Path(os.environ[TOOLCHAIN_ENV]).resolve()
        if not toolchain.is_file():
            raise EnvError(f"{TOOLCHAIN_ENV}={toolchain} is not a file")
    return Context(workspace=workspace_path, toolchain=toolchain)
