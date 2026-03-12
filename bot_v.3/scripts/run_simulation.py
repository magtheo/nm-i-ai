"""run_simulation.py — offline simulation runner for bot_v.3.

Runs the forge strategy file against the local simulator without needing
a live game server or JWT token.  Use this for all algorithmic hypothesis
testing before spending live-run budget.

Usage
-----
# Quick smoke check across all difficulties
python -m scripts.run_simulation

# Single difficulty with a specific seed
python -m scripts.run_simulation --difficulty expert --seed 7004

# Custom strategy file
python -m scripts.run_simulation --strategy forge/strategy.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.simulator import DEFAULT_DIFFICULTY_SEEDS, GrocerySimulator, evaluate_strategy_file


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="bot_v.3 offline simulation runner")
    p.add_argument(
        "--strategy",
        default=str(ROOT / "forge" / "strategy.py"),
        help="Path to strategy .py file (default: forge/strategy.py)",
    )
    p.add_argument(
        "--difficulty",
        default=None,
        choices=["easy", "medium", "hard", "expert"],
        help="Run only this difficulty (default: all four)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override seed for the chosen difficulty",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=300,
        help="Max rounds per game (default: 300)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON result instead of summary table",
    )
    return p.parse_args()


def print_summary(result_dict: dict) -> None:
    print(f"\n{'Difficulty':<12} {'Score':>6} {'Items':>6} {'Orders':>7} {'Blocked':>8} {'Error'}")
    print("-" * 60)
    for run in result_dict.get("runs", []):
        print(
            f"{run['difficulty']:<12} "
            f"{run['score']:>6} "
            f"{run['items_delivered']:>6} "
            f"{run['orders_completed']:>7} "
            f"{run['blocked_moves']:>8} "
            f"{run['error'] or ''}"
        )
    avg = result_dict.get("average_score", 0.0)
    print(f"\nAverage score: {avg:.1f}")
    if result_dict.get("has_errors"):
        print(f"Errors detected: {result_dict.get('worst_error')}")


def main() -> None:
    args = parse_args()
    strategy_path = Path(args.strategy).resolve()

    if not strategy_path.exists():
        print(f"[run_simulation] Strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[run_simulation] Strategy: {strategy_path}")

    if args.difficulty:
        seed = args.seed or DEFAULT_DIFFICULTY_SEEDS.get(args.difficulty, 0)
        print(f"[run_simulation] Running {args.difficulty} (seed={seed})…")
        from forge.core import load_strategy_callable
        strategy_fn = load_strategy_callable(strategy_path)
        sim = GrocerySimulator(
            difficulty=args.difficulty,
            seed=seed,
            max_rounds=args.max_rounds,
        )
        summary = sim.run(strategy_fn)
        result = {"runs": [summary.to_dict()], "average_score": float(summary.score),
                  "has_errors": summary.error is not None, "worst_error": summary.error}
    else:
        print("[run_simulation] Running all difficulties…")
        seed_overrides: dict[str, int] = {}
        if args.seed is not None:
            print(f"  (seed override {args.seed} ignored when running all difficulties)")
        batch = evaluate_strategy_file(
            strategy_file=strategy_path,
            max_rounds=args.max_rounds,
        )
        result = batch.to_dict()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)


if __name__ == "__main__":
    main()
