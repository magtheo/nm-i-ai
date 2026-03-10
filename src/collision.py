"""Collision avoidance between bots with multi-step reservation."""

import logging
from typing import Any
from collections import defaultdict

from .state import GameState, Bot

logger = logging.getLogger(__name__)


class CollisionAvoider:
    """Resolves collisions between bot movements with multi-step lookahead."""
    
    def __init__(self, lookahead_steps: int = 4):
        self.lookahead_steps = lookahead_steps
    
    def resolve_conflicts(
        self,
        state: GameState,
        actions: list[dict[str, Any]],
        goals: dict[int, tuple[int, int]] | None = None
    ) -> list[dict[str, Any]]:
        """Resolve movement conflicts between bots.
        
        Uses a priority-based reservation system with multi-step lookahead:
        1. Sort bots by priority
        2. Higher priority bots reserve their planned paths
        3. Lower priority bots re-route or wait
        
        Args:
            state: Current game state
            actions: List of proposed actions
            goals: Optional dict mapping bot_id to goal position
            
        Returns:
            List of resolved actions
        """
        if len(state.bots) <= 1:
            return actions  # No collision possible with single bot
        
        # Build bot position lookup
        bot_positions = {bot.id: bot.position for bot in state.bots}
        
        # Build bot lookup
        bot_lookup = {bot.id: bot for bot in state.bots}
        
        # Calculate priority for each bot
        priorities = self._calculate_priorities(state, bot_lookup)
        
        # Build reservation table: position -> (bot_id, step)
        # Also track reverse: bot_id -> reserved positions
        reservation_table: dict[tuple[int, int], set[int]] = defaultdict(set)
        bot_reservations: dict[int, list[tuple[int, int]]] = {}
        
        # Calculate planned paths for each bot
        planned_paths = {}
        for action in actions:
            bot_id = action["bot"]
            current_pos = bot_positions[bot_id]
            path = self._get_planned_path(current_pos, action, self.lookahead_steps)
            planned_paths[bot_id] = path
        
        # Sort actions by priority (higher priority = lower number)
        sorted_actions = sorted(actions, key=lambda a: priorities.get(a["bot"], 999))
        
        resolved_actions = []
        
        for action in sorted_actions:
            bot_id = action["bot"]
            current_pos = bot_positions[bot_id]
            planned_path = planned_paths[bot_id]
            
            # Check for conflicts with already-reserved positions
            conflict = self._check_path_conflict(
                bot_id, planned_path, reservation_table, current_pos
            )
            
            if conflict:
                # Try to find alternative action
                goal_pos = goals.get(bot_id) if goals else None
                alternative = self._find_alternative_action(
                    bot_id, current_pos, action, 
                    reservation_table, bot_positions, priorities, goal_pos
                )
                
                if alternative:
                    resolved_actions.append(alternative)
                    # Reserve the alternative path
                    alt_path = self._get_planned_path(current_pos, alternative, self.lookahead_steps)
                    for i, pos in enumerate(alt_path):
                        reservation_table[pos].add(bot_id)
                    bot_reservations[bot_id] = alt_path
                else:
                    # No alternative, must wait
                    resolved_actions.append({"bot": bot_id, "action": "wait"})
                    # Reserve current position
                    reservation_table[current_pos].add(bot_id)
                    bot_reservations[bot_id] = [current_pos] * self.lookahead_steps
            else:
                # No conflict, use original action
                resolved_actions.append(action)
                # Reserve the path
                for pos in planned_path:
                    reservation_table[pos].add(bot_id)
                bot_reservations[bot_id] = planned_path
        
        return resolved_actions
    
    def _calculate_priorities(
        self, 
        state: GameState, 
        bot_lookup: dict[int, Bot]
    ) -> dict[int, int]:
        """Calculate priority for each bot (lower = higher priority).
        
        Priority factors:
        1. Carrying items for active order (highest priority)
        2. Close to completing delivery
        3. Inventory full
        4. Bot ID (tie-breaker)
        """
        priorities = {}
        active_order = state.active_order
        active_needed = set(active_order.items_needed) if active_order else set()
        
        for bot in state.bots:
            priority = 1000  # Base priority
            
            # Check if carrying active items
            carrying_active = any(
                item_type in active_needed 
                for item_type in bot.inventory
            )
            
            if carrying_active:
                priority -= 500  # High priority for active delivery
            
            # Check inventory fullness
            inventory_ratio = len(bot.inventory) / 3
            priority -= int(inventory_ratio * 200)
            
            # Check distance to nearest drop-off
            min_dist = min(
                abs(bot.position[0] - zone[0]) + abs(bot.position[1] - zone[1])
                for zone in state.drop_off_zones
            )
            priority -= max(0, 100 - min_dist * 5)  # Closer = higher priority
            
            # Tie-breaker: lower bot ID = higher priority
            priority += bot.id
            
            priorities[bot.id] = priority
        
        return priorities
    
    def _get_planned_path(
        self,
        start: tuple[int, int],
        action: dict[str, Any],
        steps: int
    ) -> list[tuple[int, int]]:
        """Get the planned path for an action over multiple steps.
        
        Returns a list of positions the bot will occupy.
        """
        path = [start]
        current = start
        
        # Get first movement
        direction = self._get_movement_direction(action.get("action"))
        
        if direction:
            dx, dy = direction
            next_pos = (current[0] + dx, current[1] + dy)
            path.append(next_pos)
            current = next_pos
        
        # For remaining steps, assume bot stays in place or continues
        # (we don't know future actions, so assume staying)
        while len(path) < steps + 1:
            path.append(current)
        
        return path
    
    def _get_movement_direction(self, action_type: str) -> tuple[int, int] | None:
        """Get direction vector for a movement action."""
        directions = {
            "move_up": (0, -1),
            "move_down": (0, 1),
            "move_left": (-1, 0),
            "move_right": (1, 0)
        }
        return directions.get(action_type)
    
    def _check_path_conflict(
        self,
        bot_id: int,
        path: list[tuple[int, int]],
        reservation_table: dict[tuple[int, int], set[int]],
        current_pos: tuple[int, int]
    ) -> bool:
        """Check if a path conflicts with existing reservations.
        
        Checks for:
        1. Same position conflict (two bots at same tile at same time)
        2. Swap conflict (two bots crossing paths)
        """
        for i, pos in enumerate(path):
            if pos in reservation_table:
                reserved_by = reservation_table[pos]
                # Check if any other bot has reserved this position
                other_bots = reserved_by - {bot_id}
                if other_bots:
                    return True
        
        return False
    
    def _find_alternative_action(
        self,
        bot_id: int,
        current_pos: tuple[int, int],
        original_action: dict[str, Any],
        reservation_table: dict[tuple[int, int], set[int]],
        bot_positions: dict[int, tuple[int, int]],
        priorities: dict[int, int],
        goal_pos: tuple[int, int] | None = None
    ) -> dict[str, Any] | None:
        """Try to find an alternative action that doesn't conflict.
        
        Tries:
        1. Alternative directions toward same goal
        2. Wait action
        
        If goal_pos is provided, prefers directions that reduce distance to goal.
        """
        original_direction = self._get_movement_direction(original_action.get("action"))
        
        if not original_direction:
            return None  # Non-movement action, no alternative
        
        # Try all other directions
        all_directions = [(0, -1), (0, 1), (-1, 0), (1, 0)]
        
        # Collect valid alternatives
        valid_alternatives = []
        
        for dx, dy in all_directions:
            if (dx, dy) == original_direction:
                continue  # Skip original direction
            
            new_pos = (current_pos[0] + dx, current_pos[1] + dy)
            
            # Check if this position is reserved
            if new_pos not in reservation_table or bot_id in reservation_table[new_pos]:
                # Also check no other bot is currently at this position
                position_occupied = any(
                    pos == new_pos and other_id != bot_id
                    for other_id, pos in bot_positions.items()
                )
                
                if not position_occupied:
                    valid_alternatives.append((dx, dy, new_pos))
        
        if not valid_alternatives:
            return None
        
        # If goal is provided, sort by distance to goal (ascending)
        if goal_pos:
            def distance_to_goal(alt):
                _, _, new_pos = alt
                return abs(new_pos[0] - goal_pos[0]) + abs(new_pos[1] - goal_pos[1])
            
            valid_alternatives.sort(key=distance_to_goal)
        
        # Pick the best alternative (first after sorting, or first found if no goal)
        dx, dy, _ = valid_alternatives[0]
        action_name = self._direction_to_action(dx, dy)
        return {"bot": bot_id, "action": action_name}
    
    def _direction_to_action(self, dx: int, dy: int) -> str:
        """Convert direction vector to action name."""
        if dy < 0:
            return "move_up"
        elif dy > 0:
            return "move_down"
        elif dx < 0:
            return "move_left"
        elif dx > 0:
            return "move_right"
        return "wait"
