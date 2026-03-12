"""Workspace compatibility package for scripts that import ``bot.*``.

The active bot source tree in this workspace lives in ``bot_v.1/`` rather than
``bot/``. Expose that tree as the ``bot`` package without copying code.
"""
from __future__ import annotations

from pathlib import Path


_BOT_ROOT = Path(__file__).resolve().parent.parent / "bot_v.1"
if not _BOT_ROOT.exists():
    raise ModuleNotFoundError(f"Expected bot source tree at {_BOT_ROOT}")

# Make `import bot.<module>` resolve into the existing bot_v.1 source tree.
__path__ = [str(_BOT_ROOT)]
