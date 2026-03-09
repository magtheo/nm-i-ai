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
        self.collision_avoider = CollisionAvoider(lookahead_steps=4)
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
        logger.debug(f"Round {self.current_state.round}: Processing state with {len(self.current_state.bots)} bots")
        
        # Initialize pathfinder with current map if needed
        self.pathfinder.set_map(
            self.current_state.grid_width,
            self.current_state.grid_height,
            self.current_state.walls
        )
        
        # Update congestion based on bot positions
        bot_positions = [bot.position for bot in self.current_state.bots]
        self.pathfinder.update_congestion(bot_positions)
        
        # Assign tasks to bots (global optimization)
        tasks = self.task_assigner.assign_tasks(self.current_state)
        
        # Generate actions for each bot
        actions = self.action_generator.generate_actions(
            self.current_state,
            tasks
        )
        
        # Apply collision avoidance with multi-step lookahead
        actions = self.collision_avoider.resolve_conflicts(
            self.current_state,
            actions
        )
        
        logger.debug(f"Generated {len(actions)} actions")
        return actions
