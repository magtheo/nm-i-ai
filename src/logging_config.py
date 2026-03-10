"""Multi-file logging configuration for the Grocery Bot."""

import logging
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class LogCategory(Enum):
    """Log categories for different aspects of the bot."""
    MAIN = "main"
    PATHFINDING = "pathfinding"
    TASKS = "tasks"
    ACTIONS = "actions"
    CONNECTION = "connection"
    COLLISION = "collision"
    BOT = "bot"


LOG_CONFIG = {
    LogCategory.MAIN: {
        "filename": "main.log",
        "level": logging.INFO,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": True,
    },
    LogCategory.PATHFINDING: {
        "filename": "pathfinding.log",
        "level": logging.DEBUG,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": False,
    },
    LogCategory.TASKS: {
        "filename": "tasks.log",
        "level": logging.DEBUG,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": False,
    },
    LogCategory.ACTIONS: {
        "filename": "actions.log",
        "level": logging.DEBUG,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": False,
    },
    LogCategory.CONNECTION: {
        "filename": "connection.log",
        "level": logging.DEBUG,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": False,
    },
    LogCategory.COLLISION: {
        "filename": "collision.log",
        "level": logging.DEBUG,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": False,
    },
    LogCategory.BOT: {
        "filename": "bot.log",
        "level": logging.DEBUG,
        "format": "%(asctime)s | %(levelname)-8s | %(message)s",
        "console": False,
    },
}

LOG_DESCRIPTIONS = {
    LogCategory.MAIN: "Overview: rounds, scores, errors, warnings",
    LogCategory.BOT: "Bot positions, stuck detection, decision making",
    LogCategory.PATHFINDING: "Navigation, BFS, distance calculations",
    LogCategory.TASKS: "Task assignment, scoring, prioritization",
    LogCategory.ACTIONS: "Action generation, move/pick_up/drop_off decisions",
    LogCategory.CONNECTION: "WebSocket messages, connection status",
    LogCategory.COLLISION: "Collision detection, resolution, wait actions",
}

_loggers: dict[LogCategory, logging.Logger] = {}


class LogFormatter(logging.Formatter):
    """Custom log formatter with timestamps."""

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        if fmt is None:
            fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
        if datefmt is None:
            datefmt = "%Y-%m-%d %H:%M:%S"
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        record.asctime = self.formatTime(record, self.datefmt)
        return super().format(record)


def get_log_reference(category: LogCategory) -> str:
    """Get a reference string pointing to a specific log file.
    
    Args:
        category: The log category to reference.
    
    Returns:
        String like "(see logs/pathfinding.log for details)"
    """
    filename = LOG_CONFIG[category]["filename"]
    return f"(see logs/{filename} for details)"


def log_issue(logger: logging.Logger, category: LogCategory, message: str, level: int = logging.WARNING) -> None:
    """Log an issue to the main logger with a reference to a detailed log file.
    
    Args:
        logger: The main logger to log to.
        category: The log category that contains detailed information.
        message: The message to log.
        level: Log level (default: WARNING).
    """
    reference = get_log_reference(category)
    logger.log(level, f"{message} {reference}")


def _build_log_files_reference(log_dir: str) -> str:
    """Build the log files reference section.
    
    Args:
        log_dir: The log directory path.
    
    Returns:
        Formatted string with log files reference.
    """
    lines = ["LOG FILES:"]
    for category in LogCategory:
        filename = LOG_CONFIG[category]["filename"]
        description = LOG_DESCRIPTIONS[category]
        padded_name = f"{log_dir}/{filename}".ljust(21)
        lines.append(f"  {padded_name} - {description}")
    return "\n".join(lines)


def setup_file_logging(verbose: bool = False, log_dir: str = "logs") -> dict[str, logging.Logger]:
    """Set up multi-file logging system.
    
    Args:
        verbose: If True, enable DEBUG level for console output on main logger.
        log_dir: Directory to store log files.
    
    Returns:
        Dictionary of loggers keyed by category name.
    """
    global _loggers
    
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)
    
    session_separator = "=" * 80
    session_start = f"NEW SESSION STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    for category, config in LOG_CONFIG.items():
        logger = logging.getLogger(f"bot.{category.value}")
        logger.handlers.clear()
        logger.setLevel(config["level"])
        
        file_path = log_path / config["filename"]
        file_handler = logging.FileHandler(
            file_path,
            mode="a",
            encoding="utf-8",
        )
        file_handler.setLevel(config["level"])
        file_handler.setFormatter(LogFormatter(config["format"]))
        logger.addHandler(file_handler)
        
        file_handler.emit(logging.LogRecord(
            name=logger.name,
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f"\n{session_separator}\n{session_start}\n{session_separator}",
            args=(),
            exc_info=None,
        ))
        
        if category == LogCategory.MAIN:
            log_files_section = _build_log_files_reference(log_dir)
            file_handler.emit(logging.LogRecord(
                name=logger.name,
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=log_files_section,
                args=(),
                exc_info=None,
            ))
        
        if config["console"]:
            console_level = logging.DEBUG if verbose else logging.INFO
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level)
            console_handler.setFormatter(LogFormatter(config["format"]))
            logger.addHandler(console_handler)
        
        _loggers[category] = logger
    
    result: dict[str, logging.Logger] = {}
    for category, logger in _loggers.items():
        result[category.name] = logger
    
    return result


def get_logger(category: LogCategory) -> logging.Logger:
    """Get a logger for a specific category.
    
    Args:
        category: The log category to get a logger for.
    
    Returns:
        Logger instance for the specified category.
        
    Raises:
        RuntimeError: If logging has not been set up yet.
    """
    if category not in _loggers:
        logger = logging.getLogger(f"bot.{category.value}")
        logger.setLevel(LOG_CONFIG[category]["level"])
        _loggers[category] = logger
    return _loggers[category]


def log_game_summary(logger: logging.Logger, result: dict) -> None:
    """Log the final game summary.
    
    Args:
        logger: Logger to use for output.
        result: Dictionary containing game results with keys like:
            - winner: The winning team/player
            - score: Final score information
            - rounds: Number of rounds played
            - duration: Game duration
            - errors: Any errors encountered
    """
    separator = "=" * 60
    
    logger.info(separator)
    logger.info("GAME SUMMARY")
    logger.info(separator)
    
    if "winner" in result:
        logger.info(f"Winner: {result['winner']}")
    
    if "score" in result:
        logger.info(f"Final Score: {result['score']}")
    
    if "rounds" in result:
        logger.info(f"Rounds Played: {result['rounds']}")
    
    if "duration" in result:
        logger.info(f"Game Duration: {result['duration']}")
    
    if "errors" in result and result["errors"]:
        logger.warning(f"Errors Encountered: {len(result['errors'])}")
        for error in result["errors"]:
            logger.warning(f"  - {error}")
    
    for key, value in result.items():
        if key not in ("winner", "score", "rounds", "duration", "errors"):
            logger.info(f"{key.replace('_', ' ').title()}: {value}")
    
    logger.info(separator)
    logger.info("For detailed logs, see:")
    for category in LogCategory:
        if category != LogCategory.MAIN:
            filename = LOG_CONFIG[category]["filename"]
            description = LOG_DESCRIPTIONS[category]
            logger.info(f"  logs/{filename} - {description}")
    
    logger.info(separator)
    logger.info(f"Session ended at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(separator)
