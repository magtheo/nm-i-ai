"""Main bot class that orchestrates decision making."""

import logging
from typing import Any

from .state import GameState
from .tasks import TaskAssigner
from .actions import ActionGenerator
from .collision import CollisionAvoider
from .pathfinding import Pathfinder

logger = logging.getLogger(__name__)


class GroceryBot:
    """Main bot class that coordinates all decision making."""
    
    def __init__(self):
        self.pathfinder = Pathfinder()
        self.task_assigner = TaskAssigner(self.pathfinder)
        self.action_generator = ActionGenerator(self.pathfinder)
        self.collision_avoider = CollisionAvoider(lookahead_steps=4, pathfinder=self.pathfinder)
        self.current_state: GameState | None = None
    
    def process_round(self, state_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Process a game state and return actions for all bots.
        
        Args:
            state_data: Raw game state from server
            
        Returns:
            List of action dictionaries for each bot
        """
        # Parse state
        self.current_state = GameState.from_dict(state_data)
        state = self.current_state
        
        should_log_info = state.round == 0 or state.round % 10 == 0
        
        if should_log_info:
            logger.info(f"=== Round {state.round}/{state.max_rounds} | Score: {state.score} ===")
        
        logger.debug(f"Bots: {len(state.bots)} | Items: {len(state.items)} | Orders: {len(state.orders)}")
        
        active = state.active_order
        preview = state.preview_order
        
        if active:
            if should_log_info:
                logger.info(f"  Active order needs: {active.items_needed}")
            logger.debug(f"  Active order: {active.id} | Needed: {active.items_needed} | Delivered: {active.items_delivered}")
        else:
            logger.warning("  No active order!")
        if preview:
            logger.debug(f"  Preview order: {preview.id} | Items: {preview.items_required}")
        
        logger.debug(f"  Drop-off zones: {state.drop_off_zones}")
        
        for bot in state.bots:
            logger.debug(f"  Bot {bot.id} at {bot.position} | Inventory: {bot.inventory or 'empty'}")
            if should_log_info and bot.inventory:
                logger.info(f"  Bot {bot.id} carrying: {bot.inventory}")
            if should_log_info and bot.position in state.drop_off_zones:
                logger.info(f"  Bot {bot.id} is at drop-off zone")
        
        # Initialize pathfinder with current map if needed
        self.pathfinder.set_map(
            state.grid_width,
            state.grid_height,
            state.walls
        )
        
        # Update congestion based on bot positions
        bot_positions = [bot.position for bot in state.bots]
        self.pathfinder.update_congestion(bot_positions)
        
        # Assign tasks to bots (global optimization)
        tasks = self.task_assigner.assign_tasks(state)
        logger.debug(f"Assigned tasks: {tasks}")
        
        # Generate actions for each bot
        actions = self.action_generator.generate_actions(state, tasks)
        
        # Extract goal positions from tasks for collision avoidance
        goals = {}
        for bot_id, task in tasks.items():
            if task.target_position:
                goals[bot_id] = task.target_position
            elif task.target_item:
                goals[bot_id] = task.target_item.position
        
        # Apply collision avoidance with multi-step lookahead
        original_actions = {a["bot"]: a["action"] for a in actions}
        actions = self.collision_avoider.resolve_conflicts(state, actions, goals)

        # Log any changes made by collision avoidance
        for action in actions:
            bot_id = action.get("bot", "?")
            final_action = action.get("action", "?")
            original_action = original_actions.get(bot_id, "?")
            target = action.get("target")
            task = tasks.get(bot_id)

            if final_action != original_action:
                logger.info(f"  Bot {bot_id}: {original_action} -> {final_action} (collision avoidance)")
            elif final_action == "drop_off":
                logger.info(f"  Bot {bot_id}: DROP_OFF (carrying item to deliver)")
            elif should_log_info:
                if target:
                    logger.info(f"  Bot {bot_id}: {final_action} -> {target} | Task: {task}")
                else:
                    logger.info(f"  Bot {bot_id}: {final_action} | Task: {task}")

            if final_action == "wait" and should_log_info:
                logger.info(f"  Bot {bot_id}: WAIT (blocked)")
            logger.debug(f"  Bot {bot_id} action: {action}")
        
        if not actions:
            logger.warning("  No actions generated!")
        
        return actions
