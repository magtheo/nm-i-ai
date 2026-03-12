from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


THIS_FILE = Path(__file__).resolve()
BOT_ROOT = Path(os.environ.get("GROCERY_BOT_ROOT", THIS_FILE.parents[1])).resolve()


def _discover_lab_root(bot_root: Path) -> Path:
    override = os.environ.get("GROCERY_LAB_ROOT")
    if override:
        return Path(override).resolve()
    for candidate in (bot_root, *bot_root.parents):
        if (candidate / ".lab_root").exists():
            return candidate.resolve()
    return bot_root.parent.resolve()


LAB_ROOT = _discover_lab_root(BOT_ROOT)
SHARED_ROOT = LAB_ROOT / "shared"
EXPERIMENTS_ROOT = LAB_ROOT / "experiments"
AGENTS_ROOT = LAB_ROOT / "agents"
ARCHIVE_ROOT = LAB_ROOT / "archive"
BOT_NAME = BOT_ROOT.name
LEVEL_SPECS: dict[str, tuple[int, int, int]] = {
    "easy": (12, 10, 1),
    "medium": (16, 12, 3),
    "hard": (22, 14, 5),
    "expert": (28, 18, 10),
    "nightmare": (30, 18, 20),
}
LEVELS = tuple(LEVEL_SPECS.keys())
TODAY = datetime.now().strftime("%Y%m%d")


@dataclass(frozen=True)
class LevelDetection:
    level: str
    width: int
    height: int
    bot_count: int


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in [":", "#", "\n", "\r", "\t", "[", "]", "{", "}", '"']):
        return json.dumps(text, ensure_ascii=False)
    if text.strip() != text or text.lower() in {"null", "true", "false"}:
        return json.dumps(text, ensure_ascii=False)
    return text


def yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(item)}")
        return lines or [f"{prefix}{{}}"]
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
        return lines or [f"{prefix}[]"]
    return [f"{prefix}{yaml_scalar(value)}"]


def write_yaml(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(yaml_lines(payload)))
        handle.write("\n")


def detect_level_from_state(state: dict[str, Any]) -> LevelDetection:
    grid = state.get("grid") or {}
    width = int(grid.get("width", 0))
    height = int(grid.get("height", 0))
    bot_count = len(state.get("bots") or [])
    for level, signature in LEVEL_SPECS.items():
        if signature == (width, height, bot_count):
            return LevelDetection(level=level, width=width, height=height, bot_count=bot_count)
    raise ValueError(f"Unknown level signature width={width}, height={height}, bots={bot_count}")


def move_path(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    shutil.move(str(src), str(dst))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
        index += 1
