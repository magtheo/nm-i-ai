#!/usr/bin/env python3
"""Main entry point for the Grocery Bot."""

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

from challenges.grocery_bot.shared.config import GAME_TOKEN
from tools.connection import GameConnection
from tools.logging_config import (
    LogCategory,
    get_logger,
    log_game_summary,
    setup_file_logging,
)
from tools.observer import JSONOutput, Observer
from tools.observer.analysis import set_context
from tools.token_manager import VALID_DIFFICULTIES, TokenManager

# Available bot implementations
AVAILABLE_BOTS = ["theo", "mykyta", "member3"]


def get_bot_class(bot_name: str):
    """Dynamically import and return the bot class for the specified implementation."""
    if bot_name not in AVAILABLE_BOTS:
        raise ValueError(f"Unknown bot: {bot_name}. Available: {AVAILABLE_BOTS}")

    module_path = f"challenges.grocery_bot.{bot_name}.bot"
    try:
        module = importlib.import_module(module_path)
        return module.GroceryBot
    except ImportError as e:
        raise ImportError(f"Could not import bot '{bot_name}': {e}")


async def run_bot(
    token: str,
    bot_name: str = "theo",
    verbose: bool = False,
    observe: bool = False,
    challenge: str = "grocery_bot",
) -> dict:
    """Run the bot for a single game.

    Args:
        token: Game token from the website
        bot_name: Which bot implementation to use
        verbose: Enable verbose logging
        observe: Enable observation metrics
        challenge: Challenge name

    Returns:
        Game over data
    """
    log_dir = Path("logs") / challenge / bot_name

    setup_file_logging(verbose, log_dir=str(log_dir))
    set_context(bot_name, challenge)
    logger = get_logger(LogCategory.MAIN)

    logger.info(f"Starting Grocery Bot ({bot_name})...")

    if observe:
        json_output = JSONOutput(output_dir=str(log_dir))
        observer = Observer(enabled=True)
        logger.info(f"Observation enabled - metrics will be logged to {log_dir}/")
    else:
        observer = Observer(enabled=False)
        json_output = None

    # Get the bot class dynamically
    BotClass = get_bot_class(bot_name)
    bot = BotClass(observer=observer)
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


async def run_with_auto_token(
    difficulty: str,
    bot_name: str = "theo",
    verbose: bool = False,
    observe: bool = False,
    challenge: str = "grocery_bot",
) -> dict:
    """Run the bot with automatic token fetching.

    Args:
        difficulty: Game difficulty level
        bot_name: Which bot implementation to use
        verbose: Enable verbose logging
        observe: Enable observation metrics
        challenge: Challenge name

    Returns:
        Game over data
    """
    log_dir = Path("logs") / challenge / bot_name / difficulty

    setup_file_logging(verbose, log_dir=str(log_dir))
    set_context(bot_name, challenge)
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

    return await run_bot(game_token, bot_name, verbose, observe, challenge)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Grocery Bot")
    parser.add_argument(
        "--token",
        "-t",
        type=str,
        default=GAME_TOKEN,
        help="Game token (or set NM_GAME_TOKEN env var)",
    )
    parser.add_argument(
        "--auto-token",
        "-a",
        action="store_true",
        help="Automatically fetch token via browser (opens browser for login if needed)",
    )
    parser.add_argument(
        "--challenge",
        "-c",
        type=str,
        default="grocery_bot",
        help="Challenge name (default: grocery_bot)",
    )
    parser.add_argument(
        "--difficulty",
        "-d",
        type=str,
        default="medium",
        choices=VALID_DIFFICULTIES,
        help="Game difficulty (default: medium, used with --auto-token)",
    )
    parser.add_argument(
        "--bot",
        "-b",
        type=str,
        default="theo",
        choices=AVAILABLE_BOTS,
        help="Bot implementation to use (default: theo)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--observe",
        "-o",
        action="store_true",
        help="Enable observation and performance metrics",
    )

    args = parser.parse_args()

    if args.auto_token:
        result = asyncio.run(
            run_with_auto_token(
                args.difficulty, args.bot, args.verbose, args.observe, args.challenge
            )
        )
    elif not args.token:
        print(
            "Error: No token provided. Use --token, --auto-token, or set NM_GAME_TOKEN env var"
        )
        sys.exit(1)
    else:
        result = asyncio.run(
            run_bot(args.token, args.bot, args.verbose, args.observe, args.challenge)
        )

    print(f"\nGame Over!")
    print(f"  Challenge: {args.challenge}")
    print(f"  Bot: {args.bot}")
    print(f"  Score: {result.get('score', 0)}")
    print(f"  Items delivered: {result.get('items_delivered', 0)}")
    print(f"  Orders completed: {result.get('orders_completed', 0)}")


if __name__ == "__main__":
    main()
