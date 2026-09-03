"""Allow ``python -m ai_rfc.coverage``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
