"""Where the harness keeps its state: a runs root outside every CLAUDE.md ancestry.

Both names live in :mod:`ai_rfc.config` now — production reads them, and the
instrument imports them back rather than the other way round. This module is
the harness's spelling of the same two functions.
"""

from __future__ import annotations

from ..config import experiments_root as default_root
from ..config import profile_dir

__all__ = ["default_root", "profile_dir"]
