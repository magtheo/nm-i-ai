"""Collision avoidance between bots."""

import logging
from typing import Any

from .state import GameState, Bot

logger = logging.getLogger(__name__)


class CollisionAvoider:
    """Resolves collisions between bot movements."""
    
    def resolve_conflicts(
        self,
        state: GameState,
        actions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve movement conflicts between bots.
        
        Uses a simple priority-based reservation system:
        1. Sort bots by priority (lower ID = higher priority)
        2. Higher priority bots reserve their target tiles
        3. Lower priority bots wait if their target is reserved
        
        Args:
            state: Current game state
            actions: List of proposed actions
            
        Returns:
            List of resolved actions
        """
        # Build bot position lookup
        bot_positions = {bot.id: bot.position for bot in state.bots}
        
        # Track reserved tiles (for movement)
        reserved: set[tuple[int, int]] = set()
        
        # Track which bots are moving where
        movements = {}  # bot_id -> target_position
        
        # First pass: collect all movements
        for action in actions:
            bot_id = action["bot"]
            target = self._get_target_position(
                bot_positions[bot_id],
                action
            )
            if target:
                movements[bot_id] = target
        
        # Second pass: resolve conflicts by priority
        resolved_actions = []
        
        # Sort by bot ID (lower = higher priority)
        sorted_actions = sorted(actions, key=lambda a: a["bot"])
        
        for action in sorted_actions:
            bot_id = action["bot"]
            current_pos = bot_positions[bot_id]
            
            # Check for anti-swap (two bots trying to swap positions)
            target = movements.get(bot_id)
            if target:
                # Check if another bot is moving to our current position
                for other_id, other_target in movements.items():
                    if other_id != bot_id and other_target == current_pos:
                        # Check if they're moving to where we are
                        if movements.get(other_id) == current_pos:
                            # Potential swap - lower priority bot waits
                            if bot_id > other_id:
                                # We have lower priority, wait
                                resolved_actions.append({"bot": bot_id, "action": "wait"})
                                continue
            
            # Check if target tile is reserved
            if target and target in reserved:
                # Tile is taken, wait instead
                resolved_actions.append({"bot": bot_id, "action": "wait"})
                continue
            
            # Reserve the target tile if moving
            if target:
                reserved.add(target)
            
            resolved_actions.append(action)
        
        return resolved_actions
    
    def _get_target_position(
        self,
        current: tuple[int, int],
        action: dict[str, Any]
    ) -> tuple[int, int] | None:
        """Get the target position for a movement action."""
        x, y = current
        action_type = action.get("action")
        
        if action_type == "move_up":
            return (x, y - 1)
        elif action_type == "move_down":
            return (x, y + 1)
        elif action_type == "move_left":
            return (x - 1, y)
        elif action_type == "move_right":
            return (x + 1, y)
        
        return None
