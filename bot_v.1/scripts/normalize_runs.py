from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from _lab_paths import BOT_ROOT, LEVELS, detect_level_from_state, ensure_dir, read_json, unique_path, write_json

SOURCE_ROOTS = [BOT_ROOT / ".seed_artifacts", BOT_ROOT / "artifacts", BOT_ROOT / "logs"]


def infer_run_date(path: Path) -> str:
    name = path.name
    for part in name.split("_"):
        if len(part) == 8 and part.isdigit():
            return part
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d")


def discover() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    seen: set[Path] = set()
    for source_root in SOURCE_ROOTS:
        if not source_root.exists():
            continue
        for state0 in source_root.rglob("state0.json"):
            run_dir = state0.parent.resolve()
            if run_dir in seen:
                continue
            seen.add(run_dir)
            try:
                detection = detect_level_from_state(read_json(state0))
            except Exception:
                continue
            found.append(
                {
                    "source": str(run_dir.relative_to(BOT_ROOT)),
                    "level": detection.level,
                    "width": detection.width,
                    "height": detection.height,
                    "bot_count": detection.bot_count,
                    "run_date": infer_run_date(run_dir),
                    "run_name": run_dir.name if run_dir.name.startswith("run_") else f"run_{run_dir.name}",
                }
            )
    found.sort(key=lambda item: str(item["source"]))
    return found


def normalize(items: list[dict[str, object]], mode: str) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for item in items:
        src = BOT_ROOT / str(item["source"])
        destination = unique_path(BOT_ROOT / "runs" / str(item["level"]) / str(item["run_date"]) / str(item["run_name"]))
        ensure_dir(destination.parent)
        if mode == "copy":
            import shutil
            shutil.copytree(src, destination)
            action = "copied"
        else:
            import shutil
            shutil.move(str(src), str(destination))
            action = "moved"
        manifest = {
            "source": item["source"],
            "destination": str(destination.relative_to(BOT_ROOT)),
            "action": action,
            "level": item["level"],
            "level_signature": {"width": item["width"], "height": item["height"], "bot_count": item["bot_count"]},
            "normalized_at": datetime.now().isoformat(),
        }
        write_json(destination / "run_manifest.json", manifest)
        results.append(manifest)
    return {"mode": mode, "levels": list(LEVELS), "count": len(results), "runs": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize bot run artifacts into runs/<level>/<date>/run_<id>")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=["copy", "move"], default="copy")
    parser.add_argument("--max-runs", type=int, default=0)
    args = parser.parse_args()

    items = discover()
    if args.max_runs > 0:
        items = items[: args.max_runs]
    if args.dry_run:
        print(json.dumps({"count": len(items), "runs": items}, indent=2, ensure_ascii=False))
        return
    print(json.dumps(normalize(items, args.mode), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
