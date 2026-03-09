"""Task generation and assignment."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .state import GameState, Bot, Item, Order
from .pathfinding import Pathfinder

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
    
    def __repr__(self):
        if self.target_item:
            return f"Task({self.type.value}, item={self.target_item.type})"
        elif self.target_position:
            return f"Task({self.type.value}, pos={self.target_position})"
        return f"Task({self.type.value})"


class TaskAssigner:
    """Assigns tasks to bots based on game state."""
    
    def __init__(self, pathfinder: Pathfinder):
        self.pathfinder = pathfinder
    
    def assign_tasks(self, state: GameState) -> dict[int, Task]:
        """Assign tasks to all bots.
        
        Args:
            state: Current game state
            
        Returns:
            Dict mapping bot_id -> assigned task
        """
        tasks = {}
        assigned_items = set()  # Track which items are already assigned
        
        active_order = state.active_order
        preview_order = state.preview_order
        
        # Get needed items for active order
        active_needed = set()
        if active_order:
            active_needed = set(active_order.items_needed)
        
        # Get needed items for preview order
        preview_needed = set()
        if preview_order:
            preview_needed = set(preview_order.items_needed)
        
        # Track items already being carried
        carried_active = set()
        carried_preview = set()
        for bot in state.bots:
            for item_type in bot.inventory:
                if item_type in active_needed:
                    carried_active.add(item_type)
                elif item_type in preview_needed:
                    carried_preview.add(item_type)
        
        for bot in state.bots:
            task = self._assign_bot_task(
                bot, state, active_order, preview_order,
                active_needed, preview_needed,
                carried_active, carried_preview,
                assigned_items
            )
            tasks[bot.id] = task
            
            # Track assigned items
            if task.target_item:
                assigned_items.add(task.target_item.id)
        
        return tasks
    
    def _assign_bot_task(
        self,
        bot: Bot,
        state: GameState,
        active_order: Optional[Order],
        preview_order: Optional[Order],
        active_needed: set,
        preview_needed: set,
        carried_active: set,
        carried_preview: set,
        assigned_items: set
    ) -> Task:
        """Assign a task to a single bot."""
        
        # Priority 1: If at drop-off with useful items, drop them
        if bot.position in state.drop_off_zones:
            if self._has_useful_items(bot, active_needed, preview_needed):
                return Task(TaskType.DROP_OFF, priority=100.0)
        
        # Priority 2: If inventory is full, go to drop-off
        if len(bot.inventory) >= 3:
            nearest_drop = self._find_nearest_drop_off(bot.position, state.drop_off_zones)
            return Task(TaskType.MOVE_TO_DROP_OFF, target_position=nearest_drop, priority=90.0)
        
        # Priority 3: If carrying active items, consider dropping off
        if self._has_active_items(bot, active_needed):
            nearest_drop = self._find_nearest_drop_off(bot.position, state.drop_off_zones)
            dist = self.pathfinder.bfs_distance(bot.position, nearest_drop)
            # Higher priority if close to drop-off
            priority = 80.0 - dist
            return Task(TaskType.MOVE_TO_DROP_OFF, target_position=nearest_drop, priority=priority)
        
        # Priority 4: Pick up adjacent active item
        if active_order:
            adjacent_active = self._find_adjacent_item(
                bot, state, active_needed, assigned_items
            )
            if adjacent_active:
                return Task(TaskType.PICK_ACTIVE, target_item=adjacent_active, priority=70.0)
        
        # Priority 5: Pick up adjacent preview item
        if preview_order:
            adjacent_preview = self._find_adjacent_item(
                bot, state, preview_needed, assigned_items
            )
            if adjacent_preview:
                return Task(TaskType.PICK_PREVIEW, target_item=adjacent_preview, priority=50.0)
        
        # Priority 6: Move toward active item
        if active_order:
            best_active = self._find_best_item(
                bot, state, active_needed, assigned_items
            )
            if best_active:
                return Task(TaskType.MOVE_TO_ITEM, target_item=best_active, priority=60.0)
        
        # Priority 7: Move toward preview item (if idle)
        if preview_order:
            best_preview = self._find_best_item(
                bot, state, preview_needed, assigned_items
            )
            if best_preview:
                return Task(TaskType.MOVE_TO_ITEM, target_item=best_preview, priority=40.0)
        
        # Default: Wait
        return Task(TaskType.WAIT, priority=0.0)
    
    def _has_useful_items(self, bot: Bot, active_needed: set, preview_needed: set) -> bool:
        """Check if bot has items useful for current or next order."""
        for item_type in bot.inventory:
            if item_type in active_needed or item_type in preview_needed:
                return True
        return False
    
    def _has_active_items(self, bot: Bot, active_needed: set) -> bool:
        """Check if bot has items for the active order."""
        for item_type in bot.inventory:
            if item_type in active_needed:
                return True
        return False
    
    def _find_adjacent_item(
        self,
        bot: Bot,
        state: GameState,
        needed_types: set,
        assigned_items: set
    ) -> Optional[Item]:
        """Find an adjacent item of a needed type."""
        x, y = bot.position
        for item in state.items:
            if item.id in assigned_items:
                continue
            if item.type not in needed_types:
                continue
            ix, iy = item.position
            if abs(ix - x) + abs(iy - y) == 1:
                return item
        return None
    
    def _find_best_item(
        self,
        bot: Bot,
        state: GameState,
        needed_types: set,
        assigned_items: set
    ) -> Optional[Item]:
        """Find the best item to target (nearest unassigned)."""
        best_item = None
        best_dist = float('inf')
        
        for item in state.items:
            if item.id in assigned_items:
                continue
            if item.type not in needed_types:
                continue
            
            dist = self.pathfinder.bfs_distance(bot.position, item.position)
            if dist >= 0 and dist < best_dist:
                best_dist = dist
                best_item = item
        
        return best_item
    
    def _find_nearest_drop_off(
        self,
        position: tuple[int, int],
        drop_off_zones: list[tuple[int, int]]
    ) -> tuple[int, int]:
        """Find the nearest drop-off zone."""
        best_zone = drop_off_zones[0]
        best_dist = float('inf')
        
        for zone in drop_off_zones:
            dist = self.pathfinder.bfs_distance(position, zone)
            if dist >= 0 and dist < best_dist:
                best_dist = dist
                best_zone = zone
        
        return best_zone
