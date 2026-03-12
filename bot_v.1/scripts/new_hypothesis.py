from __future__ import annotations

import argparse
import re
import uuid
from datetime import datetime

from _lab_paths import EXPERIMENTS_ROOT, LEVELS, ensure_dir, write_yaml


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "experiment"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a hypothesis package under the lab experiments root.")
    parser.add_argument("title")
    parser.add_argument("--level", choices=list(LEVELS), default="expert")
    parser.add_argument("--owner", default="planner_agent")
    parser.add_argument("--metric", default="score")
    parser.add_argument("--mechanism", default="")
    args = parser.parse_args()

    date_key = datetime.now().strftime("%Y%m%d")
    experiment_id = f"exp_{date_key}_{uuid.uuid4().hex[:8]}_{slugify(args.title)[:24]}"
    root = ensure_dir(EXPERIMENTS_ROOT / "hypotheses" / date_key / experiment_id)
    write_yaml(
        root / "hypothesis.yaml",
        {
            "experiment_id": experiment_id,
            "title": args.title,
            "level": args.level,
            "owner": args.owner,
            "created_at": datetime.now().isoformat(),
            "hypothesis": args.title,
            "mechanism": args.mechanism or "Fill in expected causal mechanism.",
            "patch": "patch.diff",
            "success_metric": args.metric,
            "verdict": None,
        },
    )
    (root / "mechanism.md").write_text("# Mechanism\n\nDescribe why this should help.\n", encoding="utf-8")
    (root / "patch.diff").write_text("# Add patch here or reference commit.\n", encoding="utf-8")
    write_yaml(root / "success_metric.yaml", {"primary_metric": args.metric, "target": "define target", "comparison": "baseline"})
    (root / "verdict.md").write_text("# Verdict\n\nPending.\n", encoding="utf-8")
    print(root)


if __name__ == "__main__":
    main()
