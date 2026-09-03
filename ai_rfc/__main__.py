"""``python -m ai_rfc <verb>`` — the same door as the ``ai-rfc`` script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
