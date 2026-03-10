#!/usr/bin/env python3
"""Main entry point for the Grocery Bot."""

import argparse
import asyncio
import logging
import sys

from config import GAME_TOKEN, LOG_FORMAT, LOG_LEVEL
from src.bot import GroceryBot
from src.connection import GameConnection
from src.token_manager import TokenManager, VALID_DIFFICULTIES


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else getattr(logging, LOG_LEVEL)
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)]
    )


async def run_bot(token: str, verbose: bool = False) -> dict:
    """Run the bot for a single game.
    
    Args:
        token: Game token from the website
        verbose: Enable verbose logging
        
    Returns:
        Game over data
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)
    
    logger.info("Starting Grocery Bot...")
    
    bot = GroceryBot()
    connection = GameConnection(token)
    
    try:
        result = await connection.play_game(bot)
        return result
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise


async def run_with_auto_token(difficulty: str, verbose: bool = False) -> dict:
    """Run the bot with automatic token fetching.
    
    Args:
        difficulty: Game difficulty level
        verbose: Enable verbose logging
        
    Returns:
        Game over data
    """
    setup_logging(verbose)
    logger = logging.getLogger(__name__)
    
    token_manager = TokenManager()
    
    try:
        game_token = await token_manager.fetch_game_token(difficulty)
    except ImportError as e:
        logger.error(str(e))
        sys.exit(1)
    except TimeoutError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to fetch game token: {e}")
        sys.exit(1)
    
    return await run_bot(game_token, verbose)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Grocery Bot")
    parser.add_argument(
        "--token", "-t",
        type=str,
        default=GAME_TOKEN,
        help="Game token (or set NM_GAME_TOKEN env var)"
    )
    parser.add_argument(
        "--auto-token", "-a",
        action="store_true",
        help="Automatically fetch token via browser (opens browser for login if needed)"
    )
    parser.add_argument(
        "--difficulty", "-d",
        type=str,
        default="medium",
        choices=VALID_DIFFICULTIES,
        help="Game difficulty (default: medium, used with --auto-token)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.auto_token:
        result = asyncio.run(run_with_auto_token(args.difficulty, args.verbose))
    elif not args.token:
        print("Error: No token provided. Use --token, --auto-token, or set NM_GAME_TOKEN env var")
        sys.exit(1)
    else:
        result = asyncio.run(run_bot(args.token, args.verbose))
    
    print(f"\nGame Over!")
    print(f"  Score: {result.get('score', 0)}")
    print(f"  Items delivered: {result.get('items', 0)}")
    print(f"  Orders completed: {result.get('orders', 0)}")


if __name__ == "__main__":
    main()
