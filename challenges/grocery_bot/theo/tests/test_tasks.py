"""Tests for TaskAssigner and task prioritization logic."""

import pytest
from challenges.grocery_bot.theo.tasks import TaskAssigner, TaskType, ScoringConfig
from challenges.grocery_bot.theo.pathfinding import Pathfinder
from challenges.grocery_bot.shared.state import GameState, Bot, Item, Order
from challenges.grocery_bot.shared.config import (
    WEIGHT_ACTIVE_ITEM,
    WEIGHT_ORDER_COMPLETION,
    MIN_ITEMS_FOR_DROP_OFF
)

class TestTaskAssigner:
    """Tests for TaskAssigner class."""
    
    @pytest.fixture
    def pathfinder(self):
        pf = Pathfinder()
        # Simple 10x10 map
        pf.set_map(10, 10, set())
        return pf
    
    @pytest.fixture
    def assigner(self, pathfinder):
        return TaskAssigner(pathfinder)
    
    @pytest.fixture
    def base_state(self):
        """Create a base state with 1 bot and 1 active order."""
        return GameState(
            round=1, # Round > 0 to test incremental updates
            max_rounds=300,
            grid_width=10,
            grid_height=10,
            walls=set(),
            bots=[Bot(0, (5, 5), [])],
            items=[],
            orders=[Order("order_0", ["milk", "butter"], [], False, "active")],
            drop_off=(1, 1),
            drop_off_zones=[(1, 1)],
            score=0
        )

    def test_drop_off_priority_when_completing_order(self, assigner, base_state):
        """Test that dropping off is prioritized when it completes the order."""
        # Bot at drop-off with 1 item, and only 1 item needed to complete
        base_state.bots[0].position = (1, 1)
        base_state.bots[0].inventory = ["milk"]
        base_state.orders[0].items_required = ["milk"] # Only milk needed
        base_state.orders[0].items_delivered = []
        
        tasks = assigner.assign_tasks(base_state)
        assert tasks[0].type == TaskType.DROP_OFF
        # Score should include completion bonus
        assert tasks[0].score >= WEIGHT_ORDER_COMPLETION

    def test_move_to_drop_off_when_completing_order(self, assigner, base_state):
        """Test that moving to drop-off is prioritized even with 1 item if it completes the order."""
        # Bot away from drop-off with 1 item, and only 1 item needed
        base_state.bots[0].position = (5, 5)
        base_state.bots[0].inventory = ["milk"]
        base_state.orders[0].items_required = ["milk"]
        base_state.orders[0].items_delivered = []
        
        tasks = assigner.assign_tasks(base_state)
        # Should want to move to drop-off because it completes the order
        assert tasks[0].type == TaskType.MOVE_TO_DROP_OFF
        assert tasks[0].target_position == (1, 1)

    def test_prioritize_active_over_preview(self, assigner, base_state):
        """Test that active items are prioritized over preview items."""
        # Active item at (5, 6), Preview item at (5, 4)
        active_item = Item("i1", "milk", (5, 6))
        preview_item = Item("i2", "cheese", (5, 4))
        base_state.items = [active_item, preview_item]
        base_state.orders.append(Order("order_1", ["cheese"], [], False, "preview"))
        
        tasks = assigner.assign_tasks(base_state)
        # Should pick the active milk over the preview cheese
        assert tasks[0].target_item.id == "i1"
        assert tasks[0].type in [TaskType.PICK_ACTIVE, TaskType.MOVE_TO_ITEM]

    def test_inventory_clogging_prevention(self, assigner, base_state):
        """Test that we don't pick up preview items if inventory is getting full."""
        # Bot already has 2 items
        base_state.bots[0].inventory = ["apple", "bread"]
        # Adjacent preview item
        preview_item = Item("i1", "cheese", (5, 6))
        base_state.items = [preview_item]
        base_state.orders.append(Order("order_1", ["cheese"], [], False, "preview"))
        
        tasks = assigner.assign_tasks(base_state)
        # Should NOT pick up the preview item because inventory would be full (3/3)
        # and we need space for active items
        assert tasks[0].type != TaskType.PICK_PREVIEW
        assert tasks[0].type != TaskType.MOVE_TO_ITEM or tasks[0].target_item.id != "i1"

    def test_incremental_spatial_index(self, assigner, base_state):
        """Test that spatial index handles items correctly across rounds."""
        # Round 0: Initial build
        base_state.round = 0
        item1 = Item("i1", "milk", (2, 2))
        base_state.items = [item1]
        assigner.assign_tasks(base_state)
        assert "milk" in assigner._spatial_indices
        assert item1 in assigner._spatial_indices["milk"].get_nearby_items((2, 2))
        
        # Round 1: Item removed (picked up)
        base_state.round = 1
        base_state.items = []
        assigner.assign_tasks(base_state)
        assert item1 not in assigner._spatial_indices["milk"].get_nearby_items((2, 2))
        
        # Round 2: New item added
        base_state.round = 2
        item2 = Item("i2", "butter", (3, 3))
        base_state.items = [item2]
        assigner.assign_tasks(base_state)
        assert "butter" in assigner._spatial_indices
        assert item2 in assigner._spatial_indices["butter"].get_nearby_items((3, 3))

    def test_unreachable_item_handling(self, assigner, base_state, pathfinder):
        """Test that unreachable items (due to walls) are not assigned."""
        # Item surrounded by walls
        item = Item("i1", "milk", (9, 9))
        base_state.items = [item]
        pathfinder.walls = {(8, 9), (9, 8), (8, 8)} # Box in the corner
        pathfinder.set_map(10, 10, pathfinder.walls)
        
        tasks = assigner.assign_tasks(base_state)
        # Should not try to move to unreachable item
        assert tasks[0].type == TaskType.WAIT or tasks[0].type == TaskType.MOVE_TO_DROP_OFF # might move to drop off if it has fallback
