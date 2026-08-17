"""`python -m backend.engine ...`, see `cli.py`."""

from __future__ import annotations

import sys

from backend.engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
