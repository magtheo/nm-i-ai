"""Main bot class that orchestrates decision making."""

import random
from typing import Any

from .state import GameState
from .tasks import TaskAssigner
from .actions import ActionGenerator
from .collision import CollisionAvoider
from .pathfinding import Pathfinder
from .observer import Observer
from src.logging_config import get_logger, LogCategory

logger = get_logger(LogCategory.BOT)

STUCK_RECOVERY_THRESHOLD = 10
STUCK_TASK_CLEAR_THRESHOLD = 20


class GroceryBot:
    """Main bot class that coordinates all decision making."""
    
    def __init__(self, observer: Observer | None = None):
        self.observer = observer or Observer(enabled=False)
        self.pathfinder = Pathfinder()
        self.task_assigner = TaskAssigner(self.pathfinder)
        self.action_generator = ActionGenerator(self.pathfinder)
        self.collision_avoider = CollisionAvoider(lookahead_steps=4, pathfinder=self.pathfinder)
        self.current_state: GameState | None = None
        self._last_positions: dict[int, tuple[int, int]] = {}
        self._stuck_counts: dict[int, int] = {}
        self._intended_positions: dict[int, tuple[int, int]] = {}
    
    def _find_escape_position(self, bot_pos: tuple[int, int], bot_positions: list[tuple[int, int]]) -> tuple[int, int] | None:
        """Find a valid adjacent position to escape to when stuck.
        
        Args:
            bot_pos: Current position of the stuck bot
            bot_positions: Positions of all bots (to avoid collisions)
            
        Returns:
            A valid adjacent position, or None if no escape is possible
        """
        directions = [(0, -1), (0, 1), (-1, 0), (1, 0)]
        random.shuffle(directions)
        
        for dx, dy in directions:
            nx, ny = bot_pos[0] + dx, bot_pos[1] + dy
            if self.pathfinder.is_valid(nx, ny) and (nx, ny) not in bot_positions:
                return (nx, ny)
        return None
    
    def _get_recovery_action(self, bot_id: int, bot_pos: tuple[int, int], bot_positions: list[tuple[int, int]]) -> dict[str, Any] | None:
        """Generate a recovery action for a stuck bot.
        
        Args:
            bot_id: ID of the stuck bot
            bot_pos: Current position of the stuck bot
            bot_positions: Positions of all bots
            
        Returns:
            A recovery action dict, or None if no recovery is possible
        """
        escape_pos = self._find_escape_position(bot_pos, bot_positions)
        if escape_pos:
            x, y = bot_pos
            nx, ny = escape_pos
            
            if ny < y:
                action = "move_up"
            elif ny > y:
                action = "move_down"
            elif nx > x:
                action = "move_right"
            elif nx < x:
                action = "move_left"
            else:
                return None
            
            logger.info(f"  Bot {bot_id} RECOVERY: {action} to escape position {escape_pos}")
            return {"bot": bot_id, "action": action}
        return None
    
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
        
        # Clear dynamic obstacles from previous round before stuck detection adds new ones
        self.pathfinder.clear_dynamic_obstacles()
        
        # Start round observation
        with self.observer.session(f"round_{state.round}", score=state.score):
            # Phase: State parsing
            with self.observer.phase("parsing"):
                pass  # Parsing happened above, just record timing marker
            
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
            
            # Log bot positions to track movement
            for bot in state.bots:
                logger.info(f"  Bot {bot.id} at {bot.position} | Inventory: {bot.inventory or 'empty'}")
            
            # Track positions to detect stuck bots
            bots_needing_recovery: dict[int, int] = {}
            for bot in state.bots:
                last_pos = self._last_positions.get(bot.id)
                if last_pos == bot.position:
                    self._stuck_counts[bot.id] = self._stuck_counts.get(bot.id, 0) + 1
                    blocked_pos = self._intended_positions.get(bot.id)
                    if blocked_pos and self._stuck_counts[bot.id] >= 2:
                        pass
                        logger.debug(f"  Bot {bot.id} blocked at {blocked_pos}")
                    if self._stuck_counts[bot.id] >= 5 and should_log_info:
                        logger.warning(f"  Bot {bot.id} STUCK at {bot.position} for {self._stuck_counts[bot.id]} rounds!")
                    if self._stuck_counts[bot.id] >= STUCK_RECOVERY_THRESHOLD:
                        bots_needing_recovery[bot.id] = self._stuck_counts[bot.id]
                else:
                    self._stuck_counts[bot.id] = 0
                self._last_positions[bot.id] = bot.position
            
            # Phase: Initialize pathfinder
            with self.observer.phase("pathfinding"):
                self.pathfinder.set_map(
                    state.grid_width,
                    state.grid_height,
                    state.walls
                )
                
                self.pathfinder.set_obstacles([item.position for item in state.items])
                
                # Debug: Log wall information
                if state.round == 0 or state.round % 50 == 0:
                    logger.info(f"  Map: {state.grid_width}x{state.grid_height}, {len(state.walls)} walls")
                    if len(state.walls) > 0:
                        sample_walls = list(state.walls)[:5]
                        logger.info(f"  Sample walls: {sample_walls}")
                
                # Update congestion based on bot positions
                bot_positions = [bot.position for bot in state.bots]
                self.pathfinder.update_congestion(bot_positions)
            
            # Phase: Assign tasks
            with self.observer.phase("tasks"):
                tasks = self.task_assigner.assign_tasks(state)
            logger.debug(f"Assigned tasks: {tasks}")
            
            # Phase: Generate actions
            with self.observer.phase("actions"):
                actions = self.action_generator.generate_actions(state, tasks)
            
            # Phase: Collision avoidance
            with self.observer.phase("collision"):
                # Extract goal positions from tasks for collision avoidance
                goals = {}
                for bot_id, task in tasks.items():
                    if task.target_position:
                        goals[bot_id] = task.target_position
                    elif task.target_item:
                        goals[bot_id] = task.target_item.position
                
                original_actions = {a["bot"]: a for a in actions}
                actions = self.collision_avoider.resolve_conflicts(state, actions, goals, stuck_counts=self._stuck_counts)
            
            # Apply stuck recovery for bots that have been stuck too long
            bot_positions = [bot.position for bot in state.bots]
            for i, action in enumerate(actions):
                bot_id = action.get("bot")
                if bot_id in bots_needing_recovery:
                    stuck_count = bots_needing_recovery[bot_id]
                    bot = next((b for b in state.bots if b.id == bot_id), None)
                    if bot:
                        if stuck_count >= STUCK_TASK_CLEAR_THRESHOLD:
                            if bot_id in tasks:
                                logger.info(f"  Bot {bot_id} severely stuck ({stuck_count} rounds), clearing task for reassignment")
                                del tasks[bot_id]
                        recovery_action = self._get_recovery_action(bot_id, bot.position, bot_positions)
                        if recovery_action:
                            actions[i] = recovery_action
                            self._stuck_counts[bot_id] = 0
            
            # Track intended positions for stuck detection
            for action in actions:
                bot_id = action.get("bot")
                bot = next((b for b in state.bots if b.id == bot_id), None)
                if bot:
                    target = action.get("target")
                    if target and action.get("action") == "move":
                        self._intended_positions[bot_id] = target
                    else:
                        self._intended_positions[bot_id] = bot.position

            # Record metrics
            stuck_count = sum(1 for c in self._stuck_counts.values() if c >= 3)
            wait_count = sum(1 for a in actions if a.get("action") == "wait")
            inventory_count = sum(len(bot.inventory) for bot in state.bots)
            
            self.observer.counter("stuck_bots").increment(stuck_count)
            self.observer.counter("wait_actions").increment(wait_count)
            self.observer.gauge("inventory_items").set(inventory_count)
            self.observer.counter("rounds").increment()
            
            # Log any changes made by collision avoidance
            for action in actions:
                bot_id = action.get("bot", "?")
                final_action = action.get("action", "?")
                original_action = original_actions.get(bot_id, {}).get("action", "?")
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
