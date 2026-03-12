"""run_champion.py — inspect, set, and manage the champion registry.

Usage
-----
# Show current champion
python -m scripts.run_champion show

# Show full champion history
python -m scripts.run_champion history

# Show all hypotheses (with status)
python -m scripts.run_champion hypotheses

# List hypotheses by status
python -m scripts.run_champion hypotheses --status promoted
python -m scripts.run_champion hypotheses --status archived

# Rollback to a previous champion (by index in history, 0 = most recent)
python -m scripts.run_champion rollback --index 0

# Manually set a strategy as champion (force — no score check)
python -m scripts.run_champion set-champion forge/strategy.py --name my_baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.champion import (
    ChampionRecord,
    describe_champion,
    load_champion,
    load_champion_history,
    set_champion,
)
from bot.hypothesis import HypothesisManager


def cmd_show(_args: argparse.Namespace) -> None:
    champ = load_champion()
    if champ is None:
        print("No champion established yet.")
        print("  Run: python -m scripts.run_hypothesis --seed-champion forge/strategy.py")
        return
    print(describe_champion())
    print(json.dumps(champ.to_dict(), indent=2))


def cmd_history(args: argparse.Namespace) -> None:
    history = load_champion_history()
    if not history:
        print("No champion history found.")
        return
    print(f"Champion history ({len(history)} records):\n")
    for i, rec in enumerate(reversed(history)):
        tag = f"  [{i}]"
        print(
            f"{tag} {rec.hypothesis_name:<30} "
            f"score={rec.average_score:.2f}  "
            f"{rec.promoted_at[:19]}"
        )
        if args.verbose:
            for d, s in sorted(rec.per_difficulty.items()):
                print(f"         {d:<12}: {s:.0f}")


def cmd_hypotheses(args: argparse.Namespace) -> None:
    mgr = HypothesisManager()
    all_records = mgr.list_all()
    if args.status:
        all_records = [r for r in all_records if r.status == args.status]
    if not all_records:
        print(f"No hypotheses found{' with status=' + args.status if args.status else ''}.")
        return
    print(mgr.summary_table())


def cmd_rollback(args: argparse.Namespace) -> None:
    history = load_champion_history()
    if not history:
        print("No champion history to roll back to.")
        sys.exit(1)
    idx = args.index
    if idx < 0 or idx >= len(history):
        print(f"Index {idx} out of range (0..{len(history) - 1})")
        sys.exit(1)
    # history is oldest-first; reversed → newest-first
    target = list(reversed(history))[idx]
    print(f"Rolling back to: {target.hypothesis_name!r}  score={target.average_score:.2f}")
    set_champion(target, force=True)
    print("Done.")
    print(describe_champion())


def cmd_set_champion(args: argparse.Namespace) -> None:
    strategy_path = Path(args.strategy_file).resolve()
    if not strategy_path.exists():
        print(f"File not found: {strategy_path}", file=sys.stderr)
        sys.exit(1)
    score_str = args.score
    avg_score = float(score_str) if score_str else 0.0
    record = ChampionRecord(
        strategy_file=str(strategy_path),
        average_score=avg_score,
        per_difficulty={},
        promoted_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"),
        hypothesis_name=args.name or strategy_path.stem,
        notes=args.notes or "",
    )
    set_champion(record, force=True)
    print(f"Champion set: {describe_champion()}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Champion registry management")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="Show current champion")

    hist_p = sub.add_parser("history", help="Show champion history")
    hist_p.add_argument("-v", "--verbose", action="store_true")

    hyp_p = sub.add_parser("hypotheses", help="List all hypotheses")
    hyp_p.add_argument("--status", default=None,
                        choices=["proposed", "evaluating", "promoted", "archived"])

    roll_p = sub.add_parser("rollback", help="Roll back champion to a previous version")
    roll_p.add_argument("--index", type=int, default=0,
                         help="0 = most recent history entry")

    set_p = sub.add_parser("set-champion", help="Manually force-set a champion")
    set_p.add_argument("strategy_file")
    set_p.add_argument("--name", default=None)
    set_p.add_argument("--score", default="0.0")
    set_p.add_argument("--notes", default="")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    dispatch = {
        "show": cmd_show,
        "history": cmd_history,
        "hypotheses": cmd_hypotheses,
        "rollback": cmd_rollback,
        "set-champion": cmd_set_champion,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
