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
    except Exception as error:
        # Categorical on purpose. Naming the types leaves the guarantee false
        # for every type nobody named: json.load raises RecursionError on deep
        # nesting, which is not a ValueError, and an escaped exception exits 1,
        # which permits the call. "Unreadable" cannot be a list of exceptions.
        sys.stderr.write(f"guard: unreadable hook payload: {error!r}\n")
        return 2
    # Valid JSON is not necessarily an object, and ``tool_input`` can be truthy
    # without being one — in both cases ``.get`` raises, and an exception here
    # exits 1, which does not block. Unreadable must mean blocked, not allowed.
    if not isinstance(payload, dict):
        sys.stderr.write("guard: hook payload is not an object\n")
        return 2
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.stderr.write("guard: tool_input is not an object\n")
        return 2
    command = str(tool_input.get("command", ""))
    if is_allowed(command, argv):
        return 0
    prefixes = ", ".join(repr(prefix) for prefix in argv) or "(none)"
    sys.stderr.write(f"denied: this arm may run only {prefixes}; refused: {command}\n")
    return 2


if __name__ == "__main__":
    # Last line of the same defence: an exception escaping main() would exit 1,
    # and 1 does not block. Whatever went wrong, the call does not run.
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # noqa: BLE001 - blocking is the only safe exit
        sys.stderr.write(f"guard: refusing after an internal error: {error!r}\n")
        sys.exit(2)
