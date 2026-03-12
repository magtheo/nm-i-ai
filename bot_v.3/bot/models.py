"""Pydantic models for the NMiAI Grocery Bot game protocol."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────

class BotAction(str, Enum):
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    MOVE_LEFT = "move_left"
    MOVE_RIGHT = "move_right"
    PICK_UP = "pick_up"
    DROP_OFF = "drop_off"
    WAIT = "wait"


class OrderStatus(str, Enum):
    ACTIVE = "active"
    PREVIEW = "preview"


# ── Coordinate helper ──────────────────────────────────────────────────────

class Pos:
    """Lightweight (x, y) helper – NOT a Pydantic model for speed."""
    __slots__ = ("x", "y")

    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Pos):
            return self.x == other.x and self.y == other.y
        if isinstance(other, (tuple, list)) and len(other) == 2:
            return self.x == other[0] and self.y == other[1]
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.x, self.y))

    def __repr__(self) -> str:
        return f"Pos({self.x}, {self.y})"

    def as_tuple(self) -> tuple[int, int]:
        return (self.x, self.y)

    def manhattan(self, other: Pos) -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


# ── Server → Bot messages ─────────────────────────────────────────────────

class GridInfo(BaseModel):
    width: int
    height: int
    walls: list[list[int]]  # [[x, y], ...]


class BotInfo(BaseModel):
    id: int
    position: list[int]  # [x, y]
    inventory: list[str]

    @property
    def pos(self) -> Pos:
        return Pos(self.position[0], self.position[1])


class ItemInfo(BaseModel):
    id: str
    type: str
    position: list[int]  # [x, y]

    @property
    def pos(self) -> Pos:
        return Pos(self.position[0], self.position[1])


class OrderInfo(BaseModel):
    id: str
    items_required: list[str]
    items_delivered: list[str]
    complete: bool
    status: OrderStatus


class GameState(BaseModel):
    type: str = "game_state"
    round: int
    max_rounds: int
    grid: GridInfo
    bots: list[BotInfo]
    items: list[ItemInfo]
    orders: list[OrderInfo]
    drop_off: list[int]  # [x, y]
    score: int
    active_order_index: int = 0
    total_orders: int = 50

    @property
    def drop_off_pos(self) -> Pos:
        return Pos(self.drop_off[0], self.drop_off[1])


class GameOver(BaseModel):
    type: str = "game_over"
    score: int
    items: Optional[int] = None
    orders: Optional[int] = None
    items_delivered: Optional[int] = None
    orders_completed: Optional[int] = None
    rounds_used: Optional[int] = None


# ── Bot → Server messages ─────────────────────────────────────────────────

class BotActionCommand(BaseModel):
    bot: int
    action: BotAction
    item_id: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"bot": self.bot, "action": self.action.value}
        if self.item_id is not None:
            d["item_id"] = self.item_id
        return d


class RoundActions(BaseModel):
    actions: list[BotActionCommand]

    def to_payload(self) -> dict:
        return {"actions": [a.to_dict() for a in self.actions]}
