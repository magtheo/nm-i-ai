"""Thin wrapper for local offline simulator/autotune entry."""
from __future__ import annotations

import sys
from pathlib import Path


THIS_FILE = Path(__file__).resolve()
BOT_ROOT = THIS_FILE.parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from bot import _simulator


def main() -> None:
    _simulator.main()


if __name__ == "__main__":
    main()

