#!/usr/bin/env python3
"""Main entry point for the Grocery Bot."""

import argparse
import asyncio
import sys

from config import GAME_TOKEN
from src.bot import GroceryBot
from src.connection import GameConnection
from src.logging_config import LogCategory, get_logger, log_game_summary, setup_file_logging
from src.observer import JSONOutput, Observer
from src.token_manager import VALID_DIFFICULTIES, TokenManager


async def run_bot(token: str, verbose: bool = False, observe: bool = False) -> dict:
    """Run the bot for a single game.
    
    Args:
        token: Game token from the website
        verbose: Enable verbose logging
        observe: Enable observation metrics
        
    Returns:
        Game over data
    """
    setup_file_logging(verbose)
    logger = get_logger(LogCategory.MAIN)
    
    logger.info("Starting Grocery Bot...")
    
    if observe:
        json_output = JSONOutput(output_dir="observer_logs")
        observer = Observer(enabled=True)
        logger.info("Observation enabled - metrics will be logged to observer_logs/")
    else:
        observer = Observer(enabled=False)
        json_output = None
    
    bot = GroceryBot(observer=observer)
    connection = GameConnection(token)
    
    try:
        result = await connection.play_game(bot)
        
        if observe and json_output:
            analysis = observer.analyze()
            analysis.print_report()
            filepath = json_output.save(analysis.to_dict())
            logger.info(f"Observation data saved to {filepath}")
        
        log_game_summary(logger, result)
        
        return result
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise


async def run_with_auto_token(difficulty: str, verbose: bool = False, observe: bool = False) -> dict:
    """Run the bot with automatic token fetching.
    
    Args:
        difficulty: Game difficulty level
        verbose: Enable verbose logging
        observe: Enable observation metrics
        
    Returns:
        Game over data
    """
    setup_file_logging(verbose)
    logger = get_logger(LogCategory.MAIN)
    
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
    
    return await run_bot(game_token, verbose, observe)


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
    parser.add_argument(
        "--observe", "-o",
        action="store_true",
        help="Enable observation and performance metrics"
    )
    
    args = parser.parse_args()
    
    if args.auto_token:
        result = asyncio.run(run_with_auto_token(args.difficulty, args.verbose, args.observe))
    elif not args.token:
        print("Error: No token provided. Use --token, --auto-token, or set NM_GAME_TOKEN env var")
        sys.exit(1)
    else:
        result = asyncio.run(run_bot(args.token, args.verbose, args.observe))
    
    print(f"\nGame Over!")
    print(f"  Score: {result.get('score', 0)}")
    print(f"  Items delivered: {result.get('items_delivered', 0)}")
    print(f"  Orders completed: {result.get('orders_completed', 0)}")


if __name__ == "__main__":
    main()
