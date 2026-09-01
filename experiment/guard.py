#!/usr/bin/env python3
"""PreToolUse guard: block a Bash command outside this arm's prefixes.

Claude Code runs this with the arm's prefixes as arguments and the hook payload
on stdin. Exit 2 is the only signal that actually blocks the call; a JSON
``permissionDecision`` of ``deny`` is ignored by 2.1.247.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment.enforcement import is_allowed  # noqa: E402


def main(argv: list[str]) -> int:
    """Allow or block one Bash call.

    Args:
        argv: The arm's allowed command prefixes.

    Returns:
        0 to allow, 2 to block. Anything unreadable blocks.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write("guard: unreadable hook payload\n")
        return 2
    command = str((payload.get("tool_input") or {}).get("command", ""))
    if is_allowed(command, argv):
        return 0
    prefixes = ", ".join(repr(prefix) for prefix in argv) or "(none)"
    sys.stderr.write(f"denied: this arm may run only {prefixes}; refused: {command}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
