"""Entry point for running the grocery bot against the game server.

Usage:
    python -m modules.bot.runner --url "wss://game.ainm.no/ws?token=..." [--debug] [--save-states]
    python -m modules.bot.runner --token "eyJ..." --difficulty easy [--debug]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from .client import GameWSClient
from .decision_engine import DecisionEngine
from .telemetry import RoundLogger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NMiAI Grocery Bot Runner")
    p.add_argument("--url", type=str, default=None,
                   help="Full WebSocket URL including token")
    p.add_argument("--token", type=str, default=None,
                   help="JWT token (combined with --difficulty to build URL)")
    p.add_argument("--difficulty", type=str, default="easy",
                   choices=["easy", "medium", "hard", "expert"])
    p.add_argument("--debug", action="store_true", default=True,
                   help="Print round-by-round debug output")
    p.add_argument("--save-states", action="store_true", default=False,
                   help="Save full game state each round (large logs)")
    p.add_argument("--use-astar", action="store_true", default=False,
                   help="Use A* instead of BFS for pathfinding")
    p.add_argument("--log-dir", type=str, default="logs/bot",
                   help="Directory for game logs")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    # Build URL
    if args.url:
        url = args.url
        difficulty = args.difficulty
    elif args.token:
        url = f"wss://game.ainm.no/ws?token={args.token}"
        difficulty = args.difficulty
    else:
        print("ERROR: Provide --url or --token")
        sys.exit(1)

    print(f"=== NMiAI Grocery Bot ===")
    print(f"Difficulty: {difficulty}")
    print(f"Pathfinder: {'A*' if args.use_astar else 'BFS'}")
    print(f"Debug: {args.debug}")
    print()

    engine = DecisionEngine(use_astar=args.use_astar, debug=args.debug, verbose=False)
    logger = RoundLogger(
        log_dir=args.log_dir,
        difficulty=difficulty,
        save_states=True,  # always save states for debugging
    )

    client = GameWSClient(
        url=url,
        engine=engine,
        logger=logger,
        debug=args.debug,
    )

    result = await client.play()

    if result:
        print(f"\n{'='*40}")
        print(f"FINAL SCORE: {result.score}")
        if result.items is not None:
            print(f"Items delivered: {result.items}")
        if result.orders is not None:
            print(f"Orders completed: {result.orders}")
        print(f"Log: {logger.log_path}")
    else:
        print("\nGame ended without game_over message.")


if __name__ == "__main__":
    asyncio.run(main())
