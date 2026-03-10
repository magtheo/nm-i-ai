"""Action generation from tasks."""

from typing import Any

from .state import GameState, Bot
from .tasks import Task, TaskType
from .pathfinding import Pathfinder
from src.logging_config import get_logger, LogCategory

logger = get_logger(LogCategory.ACTIONS)


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
        
        Uses congestion-aware pathfinding when multiple bots are active
        to help avoid areas with other bots.
        """
        x, y = bot.position
        tx, ty = target
        
        logger.debug(f"  Bot {bot.id}: _move_toward_action from {bot.position} to {target}")
        
        use_congestion = len(bot_positions) > 1
        next_pos = self.pathfinder.get_next_step(bot.position, target, use_congestion=use_congestion)
        
        logger.debug(f"  Bot {bot.id}: pathfinder returned next_pos={next_pos}")
        
        if next_pos is None:
            logger.warning(f"  Bot {bot.id}: pathfinder returned None, waiting")
            return {"bot": bot.id, "action": "wait"}
        
        nx, ny = next_pos
        
        # Check if next_pos is valid (not a wall)
        if self.pathfinder.walls is not None and next_pos in self.pathfinder.walls:
            logger.error(f"  Bot {bot.id}: pathfinder returned WALL position {next_pos}!")
        
        # Check if next_pos is actually adjacent to current position
        dx = abs(nx - x)
        dy = abs(ny - y)
        if dx + dy != 1:
            logger.error(f"  Bot {bot.id}: pathfinder returned non-adjacent position {next_pos} from {bot.position}")
        
        if nx > x:
            return {"bot": bot.id, "action": "move_right"}
        elif nx < x:
            return {"bot": bot.id, "action": "move_left"}
        elif ny > y:
            return {"bot": bot.id, "action": "move_down"}
        elif ny < y:
            return {"bot": bot.id, "action": "move_up"}
        
        return {"bot": bot.id, "action": "wait"}
