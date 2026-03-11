"""Tests for state module."""

import pytest
from src.state import GameState, Bot, Item, Order


class TestBot:
    """Tests for Bot class."""
    
    def test_from_dict(self):
        """Test creating Bot from dict."""
        data = {"id": 0, "position": [3, 7], "inventory": ["milk"]}
        bot = Bot.from_dict(data)
        
        assert bot.id == 0
        assert bot.position == (3, 7)
        assert bot.inventory == ["milk"]


class TestItem:
    """Tests for Item class."""
    
    def test_from_dict(self):
        """Test creating Item from dict."""
        data = {"id": "item_0", "type": "milk", "position": [2, 1]}
        item = Item.from_dict(data)
        
        assert item.id == "item_0"
        assert item.type == "milk"
        assert item.position == (2, 1)


class TestOrder:
    """Tests for Order class."""
    
    def test_items_needed(self):
        """Test items_needed property."""
        order = Order(
            id="order_0",
            items_required=["milk", "bread", "eggs"],
            items_delivered=["milk"],
            complete=False,
            status="active"
        )
        
        assert order.items_needed == ["bread", "eggs"]
    
    def test_items_needed_all_delivered(self):
        """Test items_needed when all delivered."""
        order = Order(
            id="order_0",
            items_required=["milk"],
            items_delivered=["milk"],
            complete=False,
            status="active"
        )
        
        assert order.items_needed == []


class TestGameState:
    """Tests for GameState class."""
    
    def test_from_dict(self):
        """Test creating GameState from dict."""
        data = {
            "round": 5,
            "max_rounds": 300,
            "grid": {"width": 16, "height": 12, "walls": [[1, 1], [1, 2]]},
            "bots": [{"id": 0, "position": [3, 7], "inventory": []}],
            "items": [{"id": "item_0", "type": "milk", "position": [2, 1]}],
            "orders": [],
            "drop_off": [1, 10],
            "drop_off_zones": [[1, 10]],
            "score": 5
        }
        
        state = GameState.from_dict(data)
        
        assert state.round == 5
        assert state.grid_width == 16
        assert state.grid_height == 12
        assert state.walls == {(1, 1), (1, 2)}
        assert len(state.bots) == 1
        assert len(state.items) == 1
        assert state.drop_off == (1, 10)
    
    def test_active_order(self):
        """Test getting active order."""
        state = GameState(
            round=0,
            max_rounds=300,
            grid_width=16,
            grid_height=12,
            walls=set(),
            bots=[],
            items=[],
            orders=[
                Order("order_0", ["milk"], [], False, "active"),
                Order("order_1", ["bread"], [], False, "preview")
            ],
            drop_off=(1, 10),
            drop_off_zones=[(1, 10)],
            score=0
        )
        
        active = state.active_order
        assert active is not None
        assert active.id == "order_0"
    
    def test_preview_order(self):
        """Test getting preview order."""
        state = GameState(
            round=0,
            max_rounds=300,
            grid_width=16,
            grid_height=12,
            walls=set(),
            bots=[],
            items=[],
            orders=[
                Order("order_0", ["milk"], [], False, "active"),
                Order("order_1", ["bread"], [], False, "preview")
            ],
            drop_off=(1, 10),
            drop_off_zones=[(1, 10)],
            score=0
        )
        
        preview = state.preview_order
        assert preview is not None
        assert preview.id == "order_1"
    
    def test_is_wall(self):
        """Test wall checking."""
        state = GameState(
            round=0,
            max_rounds=300,
            grid_width=16,
            grid_height=12,
            walls={(5, 5), (6, 6)},
            bots=[],
            items=[],
            orders=[],
            drop_off=(1, 10),
            drop_off_zones=[(1, 10)],
            score=0
        )
        
        assert state.is_wall(5, 5) is True
        assert state.is_wall(6, 6) is True
        assert state.is_wall(0, 0) is False
    
    def test_get_items_by_type(self):
        """Test getting items by type."""
        state = GameState(
            round=0,
            max_rounds=300,
            grid_width=16,
            grid_height=12,
            walls=set(),
            bots=[],
            items=[
                Item("item_0", "milk", (1, 1)),
                Item("item_1", "milk", (2, 2)),
                Item("item_2", "bread", (3, 3))
            ],
            orders=[],
            drop_off=(1, 10),
            drop_off_zones=[(1, 10)],
            score=0
        )
        
        milk_items = state.get_items_by_type("milk")
        assert len(milk_items) == 2
        
        bread_items = state.get_items_by_type("bread")
        assert len(bread_items) == 1
