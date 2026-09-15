"""Root entry point shim (PRD 17.2/17.13).

17.13 spells the commands as ``python cli.py book build ...``. The package's
console script ``maze-book`` is the same entry point; this file exists so the
documented invocation works from a fresh checkout without installing anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from maze_book.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
