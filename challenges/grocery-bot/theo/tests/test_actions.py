"""Tests for actions module."""

import pytest
from src.actions import ActionGenerator
from src.pathfinding import Pathfinder
from src.state import GameState, Bot, Item, Order
from src.tasks import Task, TaskType


class TestActionGenerator:
    """Tests for ActionGenerator class."""
    
    @pytest.fixture
    def pathfinder(self):
        pf = Pathfinder()
        pf.set_map(16, 12, set())
        return pf
    
    @pytest.fixture
    def generator(self, pathfinder):
        return ActionGenerator(pathfinder)
    
    @pytest.fixture
    def simple_state(self):
        return GameState(
            round=0,
            max_rounds=300,
            grid_width=16,
            grid_height=12,
            walls=set(),
            bots=[Bot(0, (5, 5), [])],
            items=[Item("item_0", "milk", (6, 5))],
            orders=[Order("order_0", ["milk"], [], False, "active")],
            drop_off=(1, 10),
            drop_off_zones=[(1, 10)],
            score=0
        )
    
    def test_generate_wait_action(self, generator, simple_state):
        """Test generating a wait action."""
        tasks = {0: Task(TaskType.WAIT)}
        actions = generator.generate_actions(simple_state, tasks)
        
        assert len(actions) == 1
        assert actions[0]["action"] == "wait"
    
    def test_generate_drop_off_action(self, generator, simple_state):
        """Test generating a drop_off action."""
        # Put bot at drop-off with item
        simple_state.bots[0].position = (1, 10)
        simple_state.bots[0].inventory = ["milk"]
        
        tasks = {0: Task(TaskType.DROP_OFF)}
        actions = generator.generate_actions(simple_state, tasks)
        
        assert len(actions) == 1
        assert actions[0]["action"] == "drop_off"
    
    def test_generate_pick_up_action(self, generator, simple_state):
        """Test generating a pick_up action."""
        # Put bot adjacent to item
        simple_state.bots[0].position = (5, 5)
        item = simple_state.items[0]
        item.position = (6, 5)  # Adjacent
        
        tasks = {0: Task(TaskType.PICK_ACTIVE, target_item=item)}
        actions = generator.generate_actions(simple_state, tasks)
        
        assert len(actions) == 1
        assert actions[0]["action"] == "pick_up"
        assert actions[0]["item_id"] == "item_0"
    
    def test_generate_move_action(self, generator, simple_state):
        """Test generating a move action."""
        tasks = {0: Task(TaskType.MOVE_TO_DROP_OFF, target_position=(1, 10))}
        actions = generator.generate_actions(simple_state, tasks)
        
        assert len(actions) == 1
        assert actions[0]["action"] in ["move_up", "move_down", "move_left", "move_right"]
    
    def test_pick_up_respects_inventory_limit(self, generator, simple_state):
        """Test that pick_up respects inventory limit."""
        # Fill inventory
        simple_state.bots[0].inventory = ["item1", "item2", "item3"]
        item = simple_state.items[0]
        
        tasks = {0: Task(TaskType.PICK_ACTIVE, target_item=item)}
        actions = generator.generate_actions(simple_state, tasks)
        
        # Should wait since inventory is full
        assert actions[0]["action"] == "wait"
