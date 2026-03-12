from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from _lab_paths import BOT_ROOT, LEVELS, ARCHIVE_ROOT, ensure_dir, read_json, write_yaml, write_json


def promote(level: str, source: Path, experiment_id: str, branch: str, commit: str, score: int | None, date_value: str | None) -> None:
    read_json(source)
    best_dir = ensure_dir(BOT_ROOT / "best" / level)
    current_path = best_dir / "current.json"
    history_dir = ensure_dir(ARCHIVE_ROOT / "deprecated" / datetime.now().strftime("%Y%m%d") / "best_history" / level)
    if current_path.exists():
        shutil.copy2(current_path, history_dir / f"current_{datetime.now().strftime('%H%M%S')}.json")
    shutil.copy2(source, current_path)
    metadata = {
        "level": level,
        "experiment_id": experiment_id,
        "branch": branch,
        "commit": commit,
        "score": score,
        "date": date_value or datetime.now().date().isoformat(),
        "source_config": str(source),
    }
    write_yaml(best_dir / "metadata.yaml", metadata)
    write_json(best_dir / "registry.json", {"current": "current.json", "metadata": metadata})


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote a config to best/<level>/current.json")
    parser.add_argument("level", choices=list(LEVELS))
    parser.add_argument("source")
    parser.add_argument("--experiment-id", default="manual")
    parser.add_argument("--branch", default="unknown")
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--score", type=int, default=None)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    promote(args.level, Path(args.source).resolve(), args.experiment_id, args.branch, args.commit, args.score, args.date)
    print(f"best/{args.level}/current.json updated")


if __name__ == "__main__":
    main()
