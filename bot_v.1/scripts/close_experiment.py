from __future__ import annotations

import argparse
from datetime import datetime

from _lab_paths import EXPERIMENTS_ROOT, write_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Close an experiment and write a verdict to the lab experiments root.")
    parser.add_argument("experiment_id")
    parser.add_argument("verdict", choices=["accepted", "rejected", "inconclusive", "rolled_back"])
    parser.add_argument("--summary", default="")
    parser.add_argument("--owner", default="review_agent")
    parser.add_argument("--metric-value", default="")
    parser.add_argument("--next-step", default="")
    args = parser.parse_args()

    date_key = datetime.now().strftime("%Y%m%d")
    verdict_path = EXPERIMENTS_ROOT / "verdicts" / date_key / f"{args.experiment_id}.yaml"
    write_yaml(
        verdict_path,
        {
            "experiment_id": args.experiment_id,
            "verdict": args.verdict,
            "owner": args.owner,
            "closed_at": datetime.now().isoformat(),
            "summary": args.summary or "Add summary.",
            "metric_value": args.metric_value or None,
            "next_step": args.next_step or None,
        },
    )
    print(verdict_path)


if __name__ == "__main__":
    main()
