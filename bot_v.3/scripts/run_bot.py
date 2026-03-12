"""run_bot.py — clean bot runner for bot_v.3.

Usage
-----
# Single run, expert difficulty, default config
python -m scripts.run_bot --difficulty expert

# Three runs with custom config
python -m scripts.run_bot --difficulty expert --config configs/expert.json --runs 3

# Debug mode (prints round-by-round output)
python -m scripts.run_bot --difficulty easy --debug
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Allow running as `python -m scripts.run_bot` from inside bot_v.3/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.decision_engine import DecisionEngine, EngineConfig
from bot.endpoint import request_game_session
from bot.client import GameWSClient
from bot.telemetry import RoundLogger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="bot_v.3 runner")
    p.add_argument(
        "--difficulty",
        default="expert",
        choices=["easy", "medium", "hard", "expert"],
        help="Map difficulty (default: expert)",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to engine config JSON (default: built-in defaults)",
    )
    p.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of games to play (default: 1; max 3 per live budget policy)",
    )
    p.add_argument(
        "--cooldown-sec",
        type=float,
        default=10.0,
        help="Seconds to wait between games (default: 10)",
    )
    p.add_argument(
        "--log-dir",
        default="logs/bot",
        help="Directory for round-level JSONL logs (default: logs/bot)",
    )
    p.add_argument(
        "--no-log",
        action="store_true",
        help="Disable JSONL logging",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose round-by-round console output",
    )
    return p.parse_args()


async def run_one_game(
    difficulty: str,
    engine: DecisionEngine,
    *,
    log_dir: str,
    no_log: bool,
    debug: bool,
) -> int:
    """Request a session, play one game, return score."""
    if debug:
        print(f"[run_bot] Requesting {difficulty} session…")

    session = request_game_session(difficulty)

    logger = None
    if not no_log:
        logger = RoundLogger(log_dir=log_dir, difficulty=difficulty)

    client = GameWSClient(
        session.ws_url,
        engine,
        logger=logger,
        debug=debug,
    )

    result = await client.play()
    score = result.score if result else 0

    if debug or True:  # always print final score
        print(
            f"[run_bot] difficulty={difficulty}  score={score}"
            + (
                f"  items={result.items_delivered}  orders={result.orders_completed}"
                if result and result.items_delivered is not None
                else ""
            )
        )

    return score


def main() -> None:
    args = parse_args()

    if args.runs > 3:
        print(
            f"[run_bot] WARNING: {args.runs} runs requested. "
            "Live budget policy allows at most 3 runs per batch.",
            file=sys.stderr,
        )

    # Load engine config
    if args.config:
        config = EngineConfig.from_json(args.config)
        print(f"[run_bot] Config loaded from {args.config}")
    else:
        config = EngineConfig()
        print("[run_bot] Using default EngineConfig")

    engine = DecisionEngine(config)
    scores: list[int] = []

    for run_idx in range(args.runs):
        if run_idx > 0:
            print(f"[run_bot] Cooldown {args.cooldown_sec}s…")
            time.sleep(args.cooldown_sec)

        print(f"[run_bot] Run {run_idx + 1}/{args.runs}")
        score = asyncio.run(
            run_one_game(
                args.difficulty,
                engine,
                log_dir=args.log_dir,
                no_log=args.no_log,
                debug=args.debug,
            )
        )
        scores.append(score)

    if args.runs > 1:
        import statistics
        print(
            f"\n[run_bot] Results ({args.runs} runs): "
            f"scores={scores}  median={statistics.median(scores)}"
        )


if __name__ == "__main__":
    main()
