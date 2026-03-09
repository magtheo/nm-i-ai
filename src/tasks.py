"""Task generation and assignment with global optimization."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from collections import defaultdict

from .state import GameState, Bot, Item, Order
from .pathfinding import Pathfinder
from config import (
    WEIGHT_ACTIVE_ITEM, 
    WEIGHT_ORDER_COMPLETION, 
    WEIGHT_PREVIEW_ITEM,
    WEIGHT_POSITIONING
)

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """Types of tasks a bot can perform."""
    PICK_ACTIVE = "pick_active"
    PICK_PREVIEW = "pick_preview"
    DROP_OFF = "drop_off"
    MOVE_TO_ITEM = "move_to_item"
    MOVE_TO_DROP_OFF = "move_to_drop_off"
    WAIT = "wait"


@dataclass
class Task:
    """Represents a task for a bot."""
    type: TaskType
    target_item: Optional[Item] = None
    target_position: Optional[tuple[int, int]] = None
    priority: float = 0.0
    score: float = 0.0
    
    def __repr__(self):
        if self.target_item:
            return f"Task({self.type.value}, item={self.target_item.type}, score={self.score:.1f})"
        elif self.target_position:
            return f"Task({self.type.value}, pos={self.target_position}, score={self.score:.1f})"
        return f"Task({self.type.value}, score={self.score:.1f})"


class TaskAssigner:
    """Assigns tasks to bots using global optimization."""
    
    def __init__(self, pathfinder: Pathfinder):
        self.pathfinder = pathfinder
        self._zone_load: dict[tuple[int, int], int] = {}  # Track bots heading to each zone
    
    def assign_tasks(self, state: GameState) -> dict[int, Task]:
        """Assign tasks to all bots using global optimization.
        
        Uses a cost matrix approach:
        1. Generate all candidate tasks
        2. Build cost/benefit matrix for bot × task combinations
        3. Assign tasks to maximize total benefit
        4. Handle conflicts and remaining bots
        
        Args:
            state: Current game state
            
        Returns:
            Dict mapping bot_id -> assigned task
        """
        active_order = state.active_order
        preview_order = state.preview_order
        
        # Update pathfinder with congestion data
        bot_positions = [bot.position for bot in state.bots]
        self.pathfinder.update_congestion(bot_positions)
        
        # Reset zone load tracking
        self._zone_load = {zone: 0 for zone in state.drop_off_zones}
        
        # Track what's needed
        active_needed = self._get_needed_items(active_order, state.bots, "active")
        preview_needed = self._get_needed_items(preview_order, state.bots, "preview")
        
        # Generate candidate tasks for each bot
        all_candidates = self._generate_all_candidates(
            state, active_order, preview_order, active_needed, preview_needed
        )
        
        # Build cost matrix and assign globally
        assignments = self._global_assignment(state.bots, all_candidates, state)
        
        # Update zone load tracking based on assignments
        for bot_id, task in assignments.items():
            if task.target_position and task.target_position in state.drop_off_zones:
                self._zone_load[task.target_position] += 1
        
        # Convert to final tasks
        tasks = {}
        for bot in state.bots:
            if bot.id in assignments:
                tasks[bot.id] = assignments[bot.id]
            else:
                # Fallback task
                tasks[bot.id] = self._get_fallback_task(bot, state, active_needed, preview_needed)
        
        return tasks
    
    def _get_needed_items(
        self, 
        order: Optional[Order], 
        bots: list[Bot], 
        order_type: str
    ) -> dict[str, int]:
        """Get count of each item type still needed.
        
        Accounts for items already being carried by bots.
        """
        if not order:
            return {}
        
        needed = defaultdict(int)
        for item_type in order.items_needed:
            needed[item_type] += 1
        
        # Subtract items already delivered
        for item_type in order.items_delivered:
            if needed[item_type] > 0:
                needed[item_type] -= 1
        
        # Subtract items being carried by bots
        for bot in bots:
            for item_type in bot.inventory:
                if item_type in needed and needed[item_type] > 0:
                    needed[item_type] -= 1
        
        return dict(needed)
    
    def _generate_all_candidates(
        self,
        state: GameState,
        active_order: Optional[Order],
        preview_order: Optional[Order],
        active_needed: dict[str, int],
        preview_needed: dict[str, int]
    ) -> dict[int, list[Task]]:
        """Generate candidate tasks for each bot."""
        candidates = {}
        
        for bot in state.bots:
            bot_candidates = self._generate_bot_candidates(
                bot, state, active_order, preview_order,
                active_needed, preview_needed
            )
            candidates[bot.id] = bot_candidates
        
        return candidates
    
    def _generate_bot_candidates(
        self,
        bot: Bot,
        state: GameState,
        active_order: Optional[Order],
        preview_order: Optional[Order],
        active_needed: dict[str, int],
        preview_needed: dict[str, int]
    ) -> list[Task]:
        """Generate candidate tasks for a single bot."""
        candidates = []
        
        # Check if at drop-off with useful items
        if bot.position in state.drop_off_zones:
            if self._has_useful_items(bot, active_needed, preview_needed):
                score = self._score_drop_off(bot, state, active_order)
                candidates.append(Task(TaskType.DROP_OFF, score=score))
        
        # If inventory is full, must go to drop-off
        if len(bot.inventory) >= 3:
            zone, score = self._score_move_to_drop_off(bot, state, active_needed)
            candidates.append(Task(
                TaskType.MOVE_TO_DROP_OFF, 
                target_position=zone, 
                score=score
            ))
            return candidates  # No other options when full
        
        # If carrying active items, consider drop-off
        if self._has_active_items(bot, active_needed):
            zone, score = self._score_move_to_drop_off(bot, state, active_needed)
            candidates.append(Task(
                TaskType.MOVE_TO_DROP_OFF,
                target_position=zone,
                score=score
            ))
        
        # Pick up adjacent active items
        for item_type, count in active_needed.items():
            if count <= 0:
                continue
            for item in state.get_items_by_type(item_type):
                if self._is_adjacent(bot.position, item.position):
                    score = self._score_pick_active(bot, item, state, active_order)
                    candidates.append(Task(TaskType.PICK_ACTIVE, target_item=item, score=score))
        
        # Pick up adjacent preview items
        for item_type, count in preview_needed.items():
            if count <= 0:
                continue
            for item in state.get_items_by_type(item_type):
                if self._is_adjacent(bot.position, item.position):
                    score = self._score_pick_preview(bot, item, state)
                    candidates.append(Task(TaskType.PICK_PREVIEW, target_item=item, score=score))
        
        # Move toward active items
        for item_type, count in active_needed.items():
            if count <= 0:
                continue
            for item in state.get_items_by_type(item_type):
                score = self._score_move_to_item(bot, item, state, active_order, is_active=True)
                candidates.append(Task(TaskType.MOVE_TO_ITEM, target_item=item, score=score))
        
        # Move toward preview items (lower priority)
        for item_type, count in preview_needed.items():
            if count <= 0:
                continue
            for item in state.get_items_by_type(item_type):
                score = self._score_move_to_item(bot, item, state, preview_order, is_active=False)
                candidates.append(Task(TaskType.MOVE_TO_ITEM, target_item=item, score=score))
        
        # Always include wait as fallback
        candidates.append(Task(TaskType.WAIT, score=0.0))
        
        return candidates
    
    def _global_assignment(
        self,
        bots: list[Bot],
        all_candidates: dict[int, list[Task]],
        state: GameState
    ) -> dict[int, Task]:
        """Globally assign tasks to maximize total benefit.
        
        Uses greedy assignment with conflict resolution:
        1. Rank all (bot, task) pairs by score
        2. Assign highest-score pairs first
        3. Skip conflicts (same item assigned to multiple bots)
        4. Continue until all bots have tasks
        """
        assignments = {}
        assigned_items = set()  # Items already assigned to a bot
        
        # Build list of all (bot_id, task, score) tuples
        all_pairs = []
        for bot in bots:
            for task in all_candidates.get(bot.id, []):
                all_pairs.append((bot.id, task, task.score))
        
        # Sort by score descending
        all_pairs.sort(key=lambda x: x[2], reverse=True)
        
        # Assign greedily
        for bot_id, task, score in all_pairs:
            if bot_id in assignments:
                continue  # Bot already has a task
            
            # Check for item conflict
            if task.target_item and task.target_item.id in assigned_items:
                continue  # Item already assigned
            
            # Check if task is still valid
            if not self._is_task_valid(task, state, bot_id):
                continue
            
            # Assign the task
            assignments[bot_id] = task
            
            if task.target_item:
                assigned_items.add(task.target_item.id)
        
        return assignments
    
    def _is_task_valid(self, task: Task, state: GameState, bot_id: int) -> bool:
        """Check if a task is still valid."""
        bot = state.get_bot(bot_id)
        if not bot:
            return False
        
        if task.type == TaskType.PICK_ACTIVE or task.type == TaskType.PICK_PREVIEW:
            # Check item still exists and bot has space
            if not task.target_item:
                return False
            if len(bot.inventory) >= 3:
                return False
            # Check item still on map
            if not state.get_item(task.target_item.id):
                return False
        
        return True
    
    def _get_fallback_task(
        self,
        bot: Bot,
        state: GameState,
        active_needed: dict[str, int],
        preview_needed: dict[str, int]
    ) -> Task:
        """Get a fallback task when no good candidates exist."""
        # If carrying useful items, go to drop-off
        if self._has_useful_items(bot, active_needed, preview_needed):
            zone = self._find_best_drop_off_balanced(bot.position, state.drop_off_zones)
            return Task(TaskType.MOVE_TO_DROP_OFF, target_position=zone, score=10)
        
        # Otherwise wait
        return Task(TaskType.WAIT, score=0)
    
    # Scoring functions
    
    def _score_drop_off(self, bot: Bot, state: GameState, active_order: Optional[Order]) -> float:
        """Score for dropping off items."""
        score = 0.0
        
        # Count useful items in inventory
        if active_order:
            for item_type in bot.inventory:
                if item_type in active_order.items_needed:
                    score += WEIGHT_ACTIVE_ITEM
        
        # Bonus for completing order
        if active_order:
            remaining = len(active_order.items_needed) - len(active_order.items_delivered)
            if remaining <= len(bot.inventory):
                score += WEIGHT_ORDER_COMPLETION
        
        return score
    
    def _score_move_to_drop_off(
        self, 
        bot: Bot, 
        state: GameState, 
        active_needed: dict[str, int]
    ) -> tuple[tuple[int, int], float]:
        """Score for moving to drop-off zone with load balancing."""
        zone = self._find_best_drop_off_balanced(bot.position, state.drop_off_zones)
        distance = self.pathfinder.bfs_distance(bot.position, zone)
        
        # Base score for useful items
        score = sum(WEIGHT_ACTIVE_ITEM for t in bot.inventory if t in active_needed)
        
        # Penalize distance
        if distance > 0:
            score = score * (1.0 / (1 + distance * 0.1))
        
        return zone, score
    
    def _score_pick_active(
        self, 
        bot: Bot, 
        item: Item, 
        state: GameState, 
        active_order: Optional[Order]
    ) -> float:
        """Score for picking up an active order item."""
        score = WEIGHT_ACTIVE_ITEM
        
        # Bonus if this completes the order
        if active_order:
            remaining = len(active_order.items_needed) - len(active_order.items_delivered)
            if remaining == 1:
                score += WEIGHT_ORDER_COMPLETION
        
        return score
    
    def _score_pick_preview(self, bot: Bot, item: Item, state: GameState) -> float:
        """Score for picking up a preview order item."""
        return WEIGHT_PREVIEW_ITEM
    
    def _score_move_to_item(
        self,
        bot: Bot,
        item: Item,
        state: GameState,
        order: Optional[Order],
        is_active: bool
    ) -> float:
        """Score for moving toward an item."""
        distance = self.pathfinder.bfs_distance(bot.position, item.position)
        
        if distance <= 0:
            return 0.0
        
        # Base value
        base_value = WEIGHT_ACTIVE_ITEM if is_active else WEIGHT_PREVIEW_ITEM
        
        # Bonus for order completion potential
        if is_active and order:
            remaining = len(order.items_needed) - len(order.items_delivered)
            if remaining == 1:
                base_value += WEIGHT_ORDER_COMPLETION
        
        # Score inversely proportional to distance
        score = base_value / (1 + distance * 0.2)
        
        # Penalty for congestion near item
        congestion = self.pathfinder.get_congestion_at(item.position)
        score *= (1.0 / (1 + congestion * 0.5))
        
        return score
    
    # Helper functions
    
    def _has_useful_items(
        self, 
        bot: Bot, 
        active_needed: dict[str, int], 
        preview_needed: dict[str, int]
    ) -> bool:
        """Check if bot has items useful for current or next order."""
        for item_type in bot.inventory:
            if item_type in active_needed or item_type in preview_needed:
                return True
        return False
    
    def _has_active_items(self, bot: Bot, active_needed: dict[str, int]) -> bool:
        """Check if bot has items for the active order."""
        for item_type in bot.inventory:
            if item_type in active_needed:
                return True
        return False
    
    def _is_adjacent(self, pos1: tuple[int, int], pos2: tuple[int, int]) -> bool:
        """Check if two positions are adjacent (Manhattan distance 1)."""
        return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1]) == 1
    
    def _find_best_drop_off_balanced(
        self,
        position: tuple[int, int],
        drop_off_zones: list[tuple[int, int]]
    ) -> tuple[int, int]:
        """Find the best drop-off zone considering load balancing.
        
        Balances between:
        1. Distance to zone
        2. Current load at zone (bots heading there)
        """
        if len(drop_off_zones) == 1:
            return drop_off_zones[0]
        
        best_zone = drop_off_zones[0]
        best_score = float('inf')
        
        for zone in drop_off_zones:
            distance = self.pathfinder.bfs_distance(position, zone)
            if distance < 0:
                continue
            
            # Load penalty: more bots heading there = worse
            load = self._zone_load.get(zone, 0)
            
            # Combined score: distance + load penalty
            score = distance + load * 2
            
            if score < best_score:
                best_score = score
                best_zone = zone
        
        return best_zone
