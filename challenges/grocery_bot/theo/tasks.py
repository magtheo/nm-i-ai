"""Task generation and assignment with global optimization and route bundling."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from collections import defaultdict

from challenges.grocery_bot.shared.state import GameState, Bot, Item, Order
from challenges.grocery_bot.theo.pathfinding import Pathfinder
from challenges.grocery_bot.theo.utils import is_adjacent, MAX_INVENTORY_SIZE, SpatialIndex
from challenges.grocery_bot.shared.config import (
    WEIGHT_ACTIVE_ITEM, 
    WEIGHT_ORDER_COMPLETION, 
    WEIGHT_PREVIEW_ITEM,
    WEIGHT_POSITIONING,
    MIN_ITEMS_FOR_DROP_OFF
)
from tools.logging_config import get_logger, LogCategory

logger = get_logger(LogCategory.TASKS)


@dataclass
class ScoringConfig:
    """Configuration for task scoring"""
    distance_threshold: int = 5
    bundling_bonus: float = 0.3


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
    bundle_items: list[Item] = field(default_factory=list)
    
    def __repr__(self):
        if self.bundle_items and len(self.bundle_items) > 1:
            types = [i.type for i in self.bundle_items]
            return f"Task({self.type.value}, bundle={types}, score={self.score:.1f})"
        if self.target_item:
            return f"Task({self.type.value}, item={self.target_item.type}, score={self.score:.1f})"
        elif self.target_position:
            return f"Task({self.type.value}, pos={self.target_position}, score={self.score:.1f})"
        return f"Task({self.type.value}, score={self.score:.1f})"


class TaskAssigner:
    """Assigns tasks to bots using global optimization with route bundling."""
    
    def __init__(self, pathfinder: Pathfinder, observer=None):
        self.pathfinder = pathfinder
        self.observer = observer
        self._zone_load: dict[tuple[int, int], int] = {}
        self._distance_cache: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}
        self._spatial_indices: dict[str, SpatialIndex] = {}
    
    def assign_tasks(self, state: GameState) -> dict[int, Task]:
        """Assign tasks to all bots using global optimization."""
        active_order = state.active_order
        preview_order = state.preview_order
        
        self._distance_cache = {}
        
        # Sub-phase: rebuild spatial indices
        with self._phase("tasks:spatial_indices"):
            self._rebuild_spatial_indices(state)
        
        # Sub-phase: update congestion
        with self._phase("tasks:congestion"):
            bot_positions_list = [bot.position for bot in state.bots]
            self.pathfinder.update_congestion(bot_positions_list)
        
        self._zone_load = {zone: 0 for zone in state.drop_off_zones}
        
        # Sub-phase: calculate needed items
        with self._phase("tasks:needed_items"):
            active_needed = self._get_needed_items(active_order, state.bots, "active")
            preview_needed = self._get_needed_items(preview_order, state.bots, "preview")
        
        # Sub-phase: generate candidates
        with self._phase("tasks:generate_candidates"):
            all_candidates = self._generate_all_candidates(
                state, active_order, preview_order, active_needed, preview_needed
            )
        
        # Sub-phase: global assignment
        with self._phase("tasks:global_assignment"):
            assignments = self._global_assignment(state.bots, all_candidates, state)
        
        for bot_id, task in assignments.items():
            if task.target_position and task.target_position in state.drop_off_zones:
                self._zone_load[task.target_position] += 1
        
        tasks = {}
        for bot in state.bots:
            if bot.id in assignments:
                tasks[bot.id] = assignments[bot.id]
            else:
                tasks[bot.id] = self._get_fallback_task(bot, state, active_needed, preview_needed)
        
        return tasks
    
    def _phase(self, name: str):
        """Context manager for sub-phase timing with log markers."""
        logger.debug(f"[PHASE:{name}] start")
        if self.observer:
            return self.observer.phase(name)
        from contextlib import nullcontext
        return nullcontext()

    
    def _get_needed_items(self, order: Optional[Order], bots: list[Bot], order_type: str) -> dict[str, int]:
        """Get count of each item type still needed."""
        if not order:
            return {}
        
        needed = defaultdict(int)
        for item_type in order.items_needed:
            needed[item_type] += 1
        
        for item_type in order.items_delivered:
            if needed[item_type] > 0:
                needed[item_type] -= 1
        
        for bot in bots:
            for item_type in bot.inventory:
                if item_type in needed and needed[item_type] > 0:
                    needed[item_type] -= 1
        
        return dict(needed)
    
    def _rebuild_spatial_indices(self, state: GameState):
        """Rebuild spatial indices for all item types."""
        self._spatial_indices.clear()
        
        for item in state.items:
            if not hasattr(item, 'position') or item.position is None:
                continue
            if item.type not in self._spatial_indices:
                self._spatial_indices[item.type] = SpatialIndex()
            self._spatial_indices[item.type].add_item(item)
    
    def _generate_all_candidates(self, state: GameState, active_order: Optional[Order], 
                                  preview_order: Optional[Order], active_needed: dict[str, int],
                                  preview_needed: dict[str, int]) -> dict[int, list[Task]]:
        """Generate candidate tasks for each bot."""
        candidates = {}
        
        for bot in state.bots:
            bot_candidates = self._generate_bot_candidates(
                bot, state, active_order, preview_order, active_needed, preview_needed
            )
            candidates[bot.id] = bot_candidates
        
        return candidates
    
    def _generate_bot_candidates(self, bot: Bot, state: GameState, active_order: Optional[Order],
                                  preview_order: Optional[Order], active_needed: dict[str, int],
                                  preview_needed: dict[str, int]) -> list[Task]:
        """Generate candidate tasks for a single bot with route bundling."""
        candidates = []
        
        # Check if at drop-off with useful items (active OR preview)
        if bot.position in state.drop_off_zones:
            # Check active items
            active_count = sum(1 for t in bot.inventory if active_order and t in active_order.items_needed) if active_order else 0
            # Check preview items
            preview_count = sum(1 for t in bot.inventory if preview_order and t in preview_order.items_required) if preview_order else 0
            
            if active_count > 0 or preview_count > 0:
                score = self._score_drop_off(bot, state, active_order, active_needed)
                # Boost score if carrying preview items too
                if preview_count > 0:
                    score += WEIGHT_PREVIEW_ITEM * preview_count
                candidates.append(Task(TaskType.DROP_OFF, score=score))
        
        # Count active items in inventory (check against order's items_needed, not active_needed)
        if active_order:
            active_in_inventory = sum(1 for t in bot.inventory if t in active_order.items_needed)
        else:
            active_in_inventory = 0
        
        # If inventory is full, must go to drop-off (unless already there)
        if len(bot.inventory) >= MAX_INVENTORY_SIZE:
            if bot.position in state.drop_off_zones:
                # Already at drop-off, create DROP_OFF task
                score = self._score_drop_off(bot, state, active_order, active_needed)
                candidates.append(Task(TaskType.DROP_OFF, score=score))
            else:
                zone, score = self._score_move_to_drop_off(bot, state, active_order, active_needed)
                candidates.append(Task(TaskType.MOVE_TO_DROP_OFF, target_position=zone, score=score))
            return candidates
        
        # ROUTE BUNDLING: If carrying active items, consider whether to drop off or pick more
        if active_in_inventory >= MIN_ITEMS_FOR_DROP_OFF:
            zone, score = self._score_move_to_drop_off(bot, state, active_order, active_needed)
            candidates.append(Task(TaskType.MOVE_TO_DROP_OFF, target_position=zone, score=score))
            
            if len(bot.inventory) < MAX_INVENTORY_SIZE:
                for item_type, count in active_needed.items():
                    if count <= 0:
                        continue
                    for item in state.get_items_by_type(item_type):
                        dist = abs(bot.position[0] - item.position[0]) + abs(bot.position[1] - item.position[1])
                        if dist > 1 and dist <= ScoringConfig.distance_threshold:
                            score = self._score_pick_active(bot, item, state, active_order, active_needed)
                            score *= (1 + ScoringConfig.bundling_bonus * active_in_inventory)
                            candidates.append(Task(TaskType.MOVE_TO_ITEM, target_item=item, score=score))
        
        # Pick up adjacent active items (high priority)
        for item_type, count in active_needed.items():
            if count <= 0:
                continue
            for item in state.get_items_by_type(item_type):
                if is_adjacent(bot.position, item.position):
                    score = self._score_pick_active(bot, item, state, active_order, active_needed)
                    candidates.append(Task(TaskType.PICK_ACTIVE, target_item=item, score=score))
        
        # Pick up adjacent preview items
        for item_type, count in preview_needed.items():
            if count <= 0:
                continue
            for item in state.get_items_by_type(item_type):
                if is_adjacent(bot.position, item.position):
                    score = self._score_pick_preview(bot, item, state)
                    candidates.append(Task(TaskType.PICK_PREVIEW, target_item=item, score=score))
        
        for item_type, count in active_needed.items():
            if count <= 0:
                continue
            for item in self._get_closest_items_of_type(item_type, state, bot, limit=3):
                score = self._score_move_to_item(bot, item, state, active_order, active_needed, is_active=True)
                candidates.append(Task(TaskType.MOVE_TO_ITEM, target_item=item, score=score))
        
        for item_type, count in preview_needed.items():
            if count <= 0:
                continue
            for item in self._get_closest_items_of_type(item_type, state, bot, limit=3):
                score = self._score_move_to_item(bot, item, state, preview_order, active_needed, is_active=False)
                candidates.append(Task(TaskType.MOVE_TO_ITEM, target_item=item, score=score))
        
        # Always include wait as fallback
        candidates.append(Task(TaskType.WAIT, score=0.0))

        return candidates

    def _global_assignment(self, bots: list[Bot], all_candidates: dict[int, list[Task]],
                          state: GameState) -> dict[int, Task]:
        """Globally assign tasks to maximize total benefit."""
        assignments = {}
        assigned_items = set()
        
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
                continue
            
            if task.target_item and task.target_item.id in assigned_items:
                continue
            
            if not self._is_task_valid(task, state, bot_id):
                continue
            
            assignments[bot_id] = task
            
            if task.target_item:
                assigned_items.add(task.target_item.id)
        
        return assignments
    
    def _is_task_valid(self, task: Task, state: GameState, bot_id: int) -> bool:
        """Check if a task is still valid."""
        bot = state.get_bot(bot_id)
        if not bot:
            return False
        
        if task.type in (TaskType.PICK_ACTIVE, TaskType.PICK_PREVIEW):
            if not task.target_item:
                return False
            if len(bot.inventory) >= MAX_INVENTORY_SIZE:
                return False
            if not state.get_item(task.target_item.id):
                return False
        
        return True
    
    def _get_fallback_task(self, bot: Bot, state: GameState, active_needed: dict[str, int],
                          preview_needed: dict[str, int]) -> Task:
        """Get a fallback task when no good candidates exist."""
        # If at drop-off with items, drop them
        if bot.position in state.drop_off_zones and bot.inventory:
            return Task(TaskType.DROP_OFF, score=10)
        if self._has_useful_items(bot, active_needed, preview_needed):
            zone = self._find_best_drop_off_balanced(bot.position, state.drop_off_zones)
            return Task(TaskType.MOVE_TO_DROP_OFF, target_position=zone, score=10)
        return Task(TaskType.WAIT, score=0)
    
    # Scoring functions
    
    def _score_drop_off(self, bot: Bot, state: GameState, active_order: Optional[Order],
                       active_needed: dict[str, int]) -> float:
        """Score for dropping off items."""
        score = 0.0
        active_count = 0
        
        if active_order:
            for item_type in bot.inventory:
                if item_type in active_order.items_needed:
                    score += WEIGHT_ACTIVE_ITEM
                    active_count += 1
        
        
        # Base bonus for carrying active items (same as move_to_drop_off)
        if active_count > 0:
            score += 2.0
        
        # Immediate action bonus - drop_off can be done right now!
        score += 1.0
        # BIG bonus for completing order
        if active_order:
            remaining = len(active_order.items_needed) - len(active_order.items_delivered)
            if remaining <= active_count:
                score += WEIGHT_ORDER_COMPLETION
                # Extra bonus for completing - this is the key to high scores!
                score += WEIGHT_ORDER_COMPLETION * 0.5
        
        return score
    
    def _score_move_to_drop_off(self, bot: Bot, state: GameState, active_order: Optional[Order],
                                active_needed: dict[str, int]) -> tuple[tuple[int, int], float]:
        """Score for moving to drop-off zone."""
        zone = self._find_best_drop_off_balanced(bot.position, state.drop_off_zones)
        distance = self.pathfinder.bfs_distance(bot.position, zone)
        
        active_count = sum(1 for t in bot.inventory if active_order and t in active_order.items_needed)
        score = active_count * WEIGHT_ACTIVE_ITEM
        
        # Significant base bonus when carrying ANY active items
        if active_count > 0:
            score += 2.0
        
        # Extra bonus when delivery would complete the order
        if active_order and active_count > 0:
            remaining = len(active_order.items_needed) - len(active_order.items_delivered)
            if remaining <= active_count:
                score += WEIGHT_ORDER_COMPLETION * 0.3
        
        # Bonus for having multiple active items (bundling)
        if active_count >= MIN_ITEMS_FOR_DROP_OFF:
            score *= 1.2
        
        # Reduced distance penalty (0.05 instead of 0.1)
        if distance > 0:
            score = score / (1 + distance * 0.05)
        
        return zone, score
    
    def _score_pick_active(self, bot: Bot, item: Item, state: GameState,
                          active_order: Optional[Order], active_needed: dict[str, int]) -> float:
        """Score for picking up an active order item."""
        score = WEIGHT_ACTIVE_ITEM
        
        # Bonus if this completes the order
        if active_order:
            remaining = len(active_order.items_needed) - len(active_order.items_delivered)
            if remaining == 1:
                score += WEIGHT_ORDER_COMPLETION
        
        # Reduced bundling bonus (0.05 instead of 0.1)
        active_in_inventory = sum(1 for t in bot.inventory if active_order and t in active_order.items_needed)
        if active_in_inventory > 0:
            score *= (1 + 0.05 * active_in_inventory)
        
        return score
    
    def _score_pick_preview(self, bot: Bot, item: Item, state: GameState) -> float:
        """Score for picking up a preview order item."""
        return WEIGHT_PREVIEW_ITEM
    
    def _score_move_to_item(self, bot: Bot, item: Item, state: GameState,
                           order: Optional[Order], active_needed: dict[str, int], is_active: bool) -> float:
        distance = abs(bot.position[0] - item.position[0]) + abs(bot.position[1] - item.position[1])
        
        if distance <= 0:
            return 0.0
        
        base_value = WEIGHT_ACTIVE_ITEM if is_active else WEIGHT_PREVIEW_ITEM
        
        if is_active and order:
            remaining = len(order.items_needed) - len(order.items_delivered)
            if remaining == 1:
                base_value += WEIGHT_ORDER_COMPLETION
        
        active_in_inventory = sum(1 for t in bot.inventory if order and t in order.items_needed)
        if active_in_inventory > 0:
            # Bonus for bundling - picking up more items while already carrying
            base_value *= (1 + 0.15 * active_in_inventory)
        
        score = base_value / (1 + distance * 0.2)
        
        congestion = self.pathfinder.get_congestion_at(item.position)
        score *= (1.0 / (1 + congestion * 0.5))
        
        return score
    
    # Helper functions
    
    def _has_useful_items(self, bot: Bot, active_needed: dict[str, int],
                         preview_needed: dict[str, int]) -> bool:
        # Check if any inventory item is needed (needed dicts may have 0 count for items already accounted for)
        for item_type in bot.inventory:
            if item_type in active_needed or item_type in preview_needed:
                return True
        return False
    
    def _get_closest_items_of_type(self, item_type: str, state: GameState, bot: Bot, limit: int = 3) -> list[Item]:
        if item_type in self._spatial_indices:
            index = self._spatial_indices[item_type]
            candidates = []
            radius_cells = 1
            max_radius = max(state.grid_width, state.grid_height) // index.cell_size + 1
            
            while len(candidates) < limit and radius_cells <= max_radius:
                nearby = index.get_nearby_items(bot.position, radius_cells)
                for item in nearby:
                    if item not in candidates:
                        candidates.append(item)
                radius_cells += 1
            
            if candidates:
                candidates_with_dist = [
                    (item, abs(bot.position[0] - item.position[0]) + abs(bot.position[1] - item.position[1]))
                    for item in candidates
                ]
                candidates_with_dist.sort(key=lambda x: x[1])
                return [item for item, _ in candidates_with_dist[:limit]]
        
        items = state.get_items_by_type(item_type)
        if len(items) <= limit:
            return items
        
        items_with_dist = []
        for item in items:
            dist = abs(bot.position[0] - item.position[0]) + abs(bot.position[1] - item.position[1])
            items_with_dist.append((item, dist))
        
        items_with_dist.sort(key=lambda x: x[1])
        return [item for item, _ in items_with_dist[:limit]]
    
    def _get_distance_to_item(self, bot_pos: tuple[int, int], item_pos: tuple[int, int]) -> int:
        """Get distance from bot to nearest adjacent position of an item.
        
        Uses batched BFS for efficiency.
        """
        cache_key = (bot_pos, item_pos)
        if cache_key in self._distance_cache:
            return self._distance_cache[cache_key]
        
        x, y = item_pos
        
        valid_adjacent = []
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            adj_pos = (x + dx, y + dy)
            if self.pathfinder.is_valid(adj_pos[0], adj_pos[1]):
                valid_adjacent.append(adj_pos)
        
        if not valid_adjacent:
            self._distance_cache[cache_key] = -1
            return -1
        
        if bot_pos in valid_adjacent:
            self._distance_cache[cache_key] = 0
            return 0
        
        distances = self.pathfinder.get_distances_to_positions(bot_pos, valid_adjacent)
        
        min_distance = float('inf')
        for adj_pos in valid_adjacent:
            dist = distances.get(adj_pos, -1)
            if dist > 0 and dist < min_distance:
                min_distance = dist
        
        result = int(min_distance) if min_distance != float('inf') else -1
        self._distance_cache[cache_key] = result
        return result
    
    def _find_best_drop_off_balanced(self, position: tuple[int, int],
                                     drop_off_zones: list[tuple[int, int]]) -> tuple[int, int]:
        if len(drop_off_zones) == 1:
            return drop_off_zones[0]
        
        best_zone = drop_off_zones[0]
        best_score = float('inf')
        
        for zone in drop_off_zones:
            distance = self.pathfinder.bfs_distance(position, zone)
            if distance < 0:
                continue
            load = self._zone_load.get(zone, 0)
            score = distance + load * 2
            if score < best_score:
                best_score = score
                best_zone = zone
        
        return best_zone
