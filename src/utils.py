"""Utility functions."""

from typing import Any


def manhattan_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Calculate Manhattan distance between two points."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def format_action(action: dict[str, Any]) -> str:
    """Format an action for logging."""
    bot_id = action.get("bot", "?")
    action_type = action.get("action", "?")
    
    if action_type == "pick_up":
        item_id = action.get("item_id", "?")
        return f"Bot {bot_id}: pick_up({item_id})"
    
    return f"Bot {bot_id}: {action_type}"
