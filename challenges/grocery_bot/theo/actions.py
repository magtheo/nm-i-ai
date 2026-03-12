"""Action generation from tasks."""

from typing import Any, Optional

from challenges.grocery_bot.shared.state import GameState, Bot
from challenges.grocery_bot.theo.tasks import Task, TaskType
from challenges.grocery_bot.theo.pathfinding import Pathfinder
from challenges.grocery_bot.theo.utils import is_adjacent, MAX_INVENTORY_SIZE
from tools.logging_config import get_logger, LogCategory

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
            item_pos = task.target_item.position
            if is_adjacent(bot.position, item_pos):
                return self._pick_up_action(bot, task.target_item)
            adjacent_target = self._get_adjacent_position(bot.position, item_pos)
            if adjacent_target:
                return self._move_toward_action(
                    bot, 
                    adjacent_target, 
                    bot_positions
                )
            return {"bot": bot.id, "action": "wait"}
        
        if task.type == TaskType.MOVE_TO_DROP_OFF and task.target_position:
            return self._move_toward_action(
                bot, 
                task.target_position, 
                bot_positions
            )
        
        # Default: wait
        return {"bot": bot.id, "action": "wait"}
    
    def _get_adjacent_position(
        self, 
        bot_pos: tuple[int, int], 
        target_pos: tuple[int, int]
    ) -> Optional[tuple[int, int]]:
        """Find the best adjacent position to navigate to for reaching a target.
        
        For items on shelves, we need to navigate to an adjacent position
        rather than the item's position itself. Uses batched BFS for efficiency.
        
        Args:
            bot_pos: Current bot position
            target_pos: Target item position
            
        Returns:
            Best adjacent position to navigate to, or None if none reachable
        """
        x, y = target_pos
        adjacent_positions = [
            (x - 1, y),
            (x + 1, y),
            (x, y - 1),
            (x, y + 1),
        ]
        
        valid_adjacent = [
            pos for pos in adjacent_positions 
            if self.pathfinder.is_valid(pos[0], pos[1])
        ]
        
        if not valid_adjacent:
            return None
        
        distances = self.pathfinder.get_distances_to_positions(bot_pos, valid_adjacent)
        
        best_pos = None
        best_distance = float('inf')
        
        for adj_pos in valid_adjacent:
            dist = distances.get(adj_pos, -1)
            if dist > 0 and dist < best_distance:
                best_distance = dist
                best_pos = adj_pos
        
        return best_pos

    def _drop_off_action(self, bot: Bot) -> dict[str, Any]:
        """Generate a drop_off action."""
        return {"bot": bot.id, "action": "drop_off"}
    
    def _pick_up_action(self, bot: Bot, item) -> dict[str, Any]:
        """Generate a pick_up action."""
        if item and len(bot.inventory) < MAX_INVENTORY_SIZE:
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
            return {"bot": bot.id, "action": "wait"}
        
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
