"""Allow ``python -m ai_rfc.history``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
