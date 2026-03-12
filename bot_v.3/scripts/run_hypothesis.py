"""run_hypothesis.py — test a hypothesis strategy against the current champion.

This is the main safe experimentation entry point.  It runs the candidate
strategy in offline simulation, compares it to the current champion, and
promotes it only if it genuinely beats the champion.

The old champion is NEVER lost — it is always archived to
``registry/champions_history.jsonl`` before any promotion.

Usage
-----
# Test a hypothesis (auto-names by file)
python -m scripts.run_hypothesis forge/hypotheses/my_idea.py

# Test with a specific name and description
python -m scripts.run_hypothesis forge/hypotheses/my_idea.py \\
    --name preview_lookahead_v1 \\
    --description "Add 2-step preview pre-fetch window"

# Require a minimum improvement before promotion
python -m scripts.run_hypothesis forge/hypotheses/my_idea.py \\
    --min-improvement 2.0

# Just evaluate without promoting (dry run)
python -m scripts.run_hypothesis forge/hypotheses/my_idea.py --dry-run

# Seed the champion from a specific strategy (one-time setup)
python -m scripts.run_hypothesis --seed-champion forge/strategy.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.champion import describe_champion, load_champion
from forge.evolution import run_hypothesis, seed_champion_from_file
from forge.simulator import DEFAULT_DIFFICULTY_SEEDS


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Test a hypothesis strategy against the current champion"
    )
    p.add_argument(
        "strategy_file",
        nargs="?",
        default=None,
        help="Path to candidate strategy .py file",
    )
    p.add_argument(
        "--name",
        default=None,
        help="Hypothesis name slug (default: derived from filename + timestamp)",
    )
    p.add_argument(
        "--description",
        default="",
        help="Human-readable description of this hypothesis",
    )
    p.add_argument(
        "--min-improvement",
        type=float,
        default=0.0,
        help="Minimum score improvement required for promotion (default: 0.0)",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=300,
        help="Max simulation rounds per difficulty (default: 300)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate without promoting (archive even if score improves)",
    )
    p.add_argument(
        "--seed-champion",
        metavar="STRATEGY_FILE",
        default=None,
        help="Seed the champion from this strategy file and exit",
    )
    p.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard", "expert", "all"],
        default="all",
        help="Which difficulties to run (default: all)",
    )
    p.add_argument(
        "--seed-override",
        metavar="DIFF=SEED",
        action="append",
        default=[],
        help="Override seed for a difficulty, e.g. --seed-override expert=9999",
    )
    return p.parse_args()


def _parse_seed_overrides(raw: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for token in raw:
        if "=" not in token:
            print(f"[run_hypothesis] WARNING: ignoring bad seed override {token!r}", file=sys.stderr)
            continue
        key, val = token.split("=", 1)
        out[key.strip().lower()] = int(val.strip())
    return out


def main() -> None:
    args = parse_args()

    seed_overrides = _parse_seed_overrides(args.seed_override)

    # ── Mode: seed champion ──────────────────────────────────────────────────
    if args.seed_champion:
        seed_champion_from_file(
            args.seed_champion,
            hypothesis_name="initial_seed",
            max_rounds=args.max_rounds,
            difficulty_seeds=seed_overrides,
            force=True,
            verbose=True,
        )
        print(f"\n[run_hypothesis] {describe_champion()}")
        return

    # ── Mode: evaluate hypothesis ────────────────────────────────────────────
    if not args.strategy_file:
        print("[run_hypothesis] ERROR: strategy_file is required (or use --seed-champion)", file=sys.stderr)
        sys.exit(1)

    strategy_path = Path(args.strategy_file).resolve()
    if not strategy_path.exists():
        print(f"[run_hypothesis] ERROR: strategy file not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)

    # Derive hypothesis name if not given
    if args.name:
        hypothesis_name = args.name
    else:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hypothesis_name = f"{strategy_path.stem}_{ts}"

    # Print current champion before evaluation
    champ = load_champion()
    if champ is None:
        print(
            "[run_hypothesis] No champion established yet.\n"
            "  Run:  python -m scripts.run_hypothesis --seed-champion forge/strategy.py",
        )
    else:
        print(f"[run_hypothesis] Current: {describe_champion()}")

    min_improvement = args.min_improvement
    if args.dry_run:
        # Evaluate but use impossibly large min_improvement to prevent promotion
        min_improvement = 1e18

    record = run_hypothesis(
        hypothesis_name,
        strategy_path,
        description=args.description,
        max_rounds=args.max_rounds,
        difficulty_seeds=seed_overrides or None,
        min_improvement=min_improvement,
        verbose=True,
    )

    # Final summary
    print(f"\n{'─'*70}")
    if args.dry_run:
        print(f"[run_hypothesis] DRY RUN — result: {record.status} (no champion updated)")
    else:
        print(f"[run_hypothesis] Result: {record.status}")
    print(f"[run_hypothesis] {describe_champion()}")
    print(f"{'─'*70}\n")

    if record.status == "archived" and not args.dry_run:
        sys.exit(1)  # Non-zero exit so CI can detect non-promotions


if __name__ == "__main__":
    main()
