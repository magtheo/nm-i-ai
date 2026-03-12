from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from _lab_paths import ARCHIVE_ROOT, BOT_ROOT, TODAY, ensure_dir, move_path, unique_path, write_json

FILE_PATTERNS = [
    re.compile(r"^_tmp_", re.IGNORECASE),
    re.compile(r".*\.(bak|orig|rej|tmp)$", re.IGNORECASE),
    re.compile(r".*(debug|dump|manual_copy|duplicate|copy)\.(json|yaml|yml|txt|md)$", re.IGNORECASE),
]
DIR_PATTERNS = [
    re.compile(r"^\.tmp_", re.IGNORECASE),
    re.compile(r".*(snapshot_copy|debug|dump|backup)$", re.IGNORECASE),
]
SKIP = {".venv", "__pycache__", "runs", "best", "tests", "scripts", "configs"}


def match_reason(path: Path) -> str | None:
    patterns = DIR_PATTERNS if path.is_dir() else FILE_PATTERNS
    for pattern in patterns:
        if pattern.match(path.name):
            return pattern.pattern
    return None


def discover() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path in BOT_ROOT.rglob("*"):
        if any(part in SKIP for part in path.parts):
            continue
        reason = match_reason(path)
        if reason:
            items.append({"path": str(path.relative_to(BOT_ROOT)), "kind": "directory" if path.is_dir() else "file", "reason": reason})
    items.sort(key=lambda item: item["path"])
    return items


def apply(items: list[dict[str, str]], copy_only: bool) -> dict[str, object]:
    destination_root = ensure_dir(ARCHIVE_ROOT / "deprecated" / TODAY / BOT_ROOT.name)
    moved: list[dict[str, str]] = []
    for item in items:
        src = BOT_ROOT / item["path"]
        if not src.exists():
            continue
        dst = unique_path(destination_root / item["path"])
        ensure_dir(dst.parent)
        if copy_only:
            if src.is_dir():
                import shutil
                shutil.copytree(src, dst)
            else:
                import shutil
                shutil.copy2(src, dst)
            action = "copied"
        else:
            move_path(src, dst)
            action = "moved"
        moved.append({"source": item["path"], "destination": str(dst.relative_to(BOT_ROOT.parent)), "action": action, "reason": item["reason"]})
    manifest = {"date": TODAY, "bot_root": str(BOT_ROOT), "items": moved}
    write_json(destination_root / "cleanup_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive likely deprecated bot artifacts to the lab archive.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--copy", action="store_true")
    args = parser.parse_args()

    items = discover()
    if not args.apply:
        print(json.dumps({"count": len(items), "items": items}, indent=2, ensure_ascii=False))
        return
    print(json.dumps(apply(items, copy_only=args.copy), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
