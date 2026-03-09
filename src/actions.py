"""Action generation from tasks."""

import logging
from typing import Any

from .state import GameState, Bot
from .tasks import Task, TaskType
from .pathfinding import Pathfinder

logger = logging.getLogger(__name__)


class ActionGenerator:
    """Generates actions from assigned tasks."""
    
    def __init__(self, pathfinder: Pathfinder):
        self.pathfinder = pathfinder
    
    def generate_actions(
        self,
        state: GameState,
        tasks: dict[int, Task]
    ) -> list[dict[str, Any]]:
        """Generate actions for all bots based on their tasks.
        
        Args:
            state: Current game state
            tasks: Assigned tasks per bot
            
        Returns:
            List of action dictionaries
        """
        actions = []
        
        # Get all bot positions for congestion awareness
        bot_positions = [bot.position for bot in state.bots]
        
        for bot in state.bots:
            task = tasks.get(bot.id)
            if task:
                action = self._generate_action(bot, state, task, bot_positions)
            else:
                action = {"bot": bot.id, "action": "wait"}
            actions.append(action)
        
        return actions
    
    def _generate_action(
        self,
        bot: Bot,
        state: GameState,
        task: Task,
        bot_positions: list[tuple[int, int]]
    ) -> dict[str, Any]:
        """Generate an action for a bot based on its task."""
        
        if task.type == TaskType.DROP_OFF:
            return self._drop_off_action(bot)
        
        if task.type == TaskType.PICK_ACTIVE or task.type == TaskType.PICK_PREVIEW:
            return self._pick_up_action(bot, task.target_item)
        
        if task.type == TaskType.MOVE_TO_ITEM and task.target_item:
            return self._move_toward_action(
                bot, 
                task.target_item.position, 
                bot_positions
            )
        
        if task.type == TaskType.MOVE_TO_DROP_OFF and task.target_position:
            return self._move_toward_action(
                bot, 
                task.target_position, 
                bot_positions
            )
        
        # Default: wait
        return {"bot": bot.id, "action": "wait"}
    
    def _drop_off_action(self, bot: Bot) -> dict[str, Any]:
        """Generate a drop_off action."""
        return {"bot": bot.id, "action": "drop_off"}
    
    def _pick_up_action(self, bot: Bot, item) -> dict[str, Any]:
        """Generate a pick_up action."""
        if item and len(bot.inventory) < 3:
            return {"bot": bot.id, "action": "pick_up", "item_id": item.id}
        return {"bot": bot.id, "action": "wait"}
    
    def _move_toward_action(
        self,
        bot: Bot,
        target: tuple[int, int],
        bot_positions: list[tuple[int, int]]
    ) -> dict[str, Any]:
        """Generate a movement action toward a target.
        
        Uses congestion-aware pathfinding when multiple bots are present.
        """
        x, y = bot.position
        tx, ty = target
        
        # Use congestion-aware pathfinding for multi-bot scenarios
        use_congestion = len(bot_positions) > 1
        
        if use_congestion:
            # Exclude this bot's position from congestion calculation
            other_positions = [p for p in bot_positions if p != bot.position]
            next_pos = self.pathfinder.get_next_step(
                bot.position, 
                target, 
                use_congestion=True
            )
        else:
            next_pos = self.pathfinder.get_next_step(bot.position, target)
        
        if next_pos is None:
            # No path or already at target
            return {"bot": bot.id, "action": "wait"}
        
        nx, ny = next_pos
        
        # Determine direction
        if nx > x:
            return {"bot": bot.id, "action": "move_right"}
        elif nx < x:
            return {"bot": bot.id, "action": "move_left"}
        elif ny > y:
            return {"bot": bot.id, "action": "move_down"}
        elif ny < y:
            return {"bot": bot.id, "action": "move_up"}
        
        return {"bot": bot.id, "action": "wait"}
