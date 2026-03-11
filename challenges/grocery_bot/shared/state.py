"""Game state parsing and representation."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Bot:
    """Represents a bot in the game."""
    id: int
    position: tuple[int, int]
    inventory: list[str] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Bot":
        return cls(
            id=data["id"],
            position=tuple(data["position"]),
            inventory=data.get("inventory", []).copy()
        )


@dataclass
class Item:
    """Represents an item on a shelf."""
    id: str
    type: str
    position: tuple[int, int]
    
    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        return cls(
            id=data["id"],
            type=data["type"],
            position=tuple(data["position"])
        )


@dataclass
class Order:
    """Represents an order (active or preview)."""
    id: str
    items_required: list[str]
    items_delivered: list[str]
    complete: bool
    status: str  # "active" or "preview"
    
    @classmethod
    def from_dict(cls, data: dict) -> "Order":
        return cls(
            id=data["id"],
            items_required=data["items_required"].copy(),
            items_delivered=data.get("items_delivered", []).copy(),
            complete=data.get("complete", False),
            status=data["status"]
        )
    
    @property
    def items_needed(self) -> list[str]:
        """Get items still needed for this order."""
        needed = self.items_required.copy()
        for delivered in self.items_delivered:
            if delivered in needed:
                needed.remove(delivered)
        return needed


@dataclass
class GameState:
    """Represents the complete game state."""
    round: int
    max_rounds: int
    grid_width: int
    grid_height: int
    walls: set[tuple[int, int]]
    bots: list[Bot]
    items: list[Item]
    orders: list[Order]
    drop_off: tuple[int, int]
    drop_off_zones: list[tuple[int, int]]
    score: int
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        grid = data.get("grid", {})
        walls_data = grid.get("walls", [])
        
        return cls(
            round=data.get("round", 0),
            max_rounds=data.get("max_rounds", 300),
            grid_width=grid.get("width", 16),
            grid_height=grid.get("height", 12),
            walls={tuple(w) for w in walls_data},
            bots=[Bot.from_dict(b) for b in data.get("bots", [])],
            items=[Item.from_dict(i) for i in data.get("items", [])],
            orders=[Order.from_dict(o) for o in data.get("orders", [])],
            drop_off=tuple(data.get("drop_off", [0, 0])),
            drop_off_zones=[tuple(z) for z in data.get("drop_off_zones", [])] or [tuple(data.get("drop_off", [0, 0]))],
            score=data.get("score", 0)
        )
    
    @property
    def active_order(self) -> Order | None:
        """Get the currently active order."""
        for order in self.orders:
            if order.status == "active":
                return order
        return None
    
    @property
    def preview_order(self) -> Order | None:
        """Get the preview order."""
        for order in self.orders:
            if order.status == "preview":
                return order
        return None
    
    def get_bot(self, bot_id: int) -> Bot | None:
        """Get a bot by ID."""
        for bot in self.bots:
            if bot.id == bot_id:
                return bot
        return None
    
    def get_item(self, item_id: str) -> Item | None:
        """Get an item by ID."""
        for item in self.items:
            if item.id == item_id:
                return item
        return None
    
    def get_items_by_type(self, item_type: str) -> list[Item]:
        """Get all items of a specific type."""
        return [item for item in self.items if item.type == item_type]
    
    def is_wall(self, x: int, y: int) -> bool:
        """Check if a position is a wall."""
        return (x, y) in self.walls
    
    def is_valid_position(self, x: int, y: int) -> bool:
        """Check if a position is valid (within bounds and not a wall)."""
        if x < 0 or x >= self.grid_width:
            return False
        if y < 0 or y >= self.grid_height:
            return False
        return not self.is_wall(x, y)
