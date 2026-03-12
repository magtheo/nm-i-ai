"""Shared protocol types and constants for the Automated Forge system."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_ACTIONS: tuple[str, ...] = (
    "move_up",
    "move_down",
    "move_left",
    "move_right",
    "pick_up",
    "drop_off",
    "wait",
)

MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "move_up": (0, -1),
    "move_down": (0, 1),
    "move_left": (-1, 0),
    "move_right": (1, 0),
}


@dataclass(frozen=True)
class DifficultySpec:
    name: str
    width: int
    height: int
    bot_count: int
    aisle_count: int
    item_types: tuple[str, ...]
    order_size_min: int
    order_size_max: int


ITEM_TYPE_POOL: tuple[str, ...] = (
    "milk",
    "butter",
    "yogurt",
    "cheese",
    "eggs",
    "bread",
    "apple",
    "banana",
    "tomato",
    "onion",
    "coffee",
    "tea",
    "rice",
    "pasta",
    "beans",
    "cereal",
)

DIFFICULTIES: dict[str, DifficultySpec] = {
    "easy": DifficultySpec(
        name="easy",
        width=12,
        height=10,
        bot_count=1,
        aisle_count=2,
        item_types=ITEM_TYPE_POOL[:4],
        order_size_min=3,
        order_size_max=4,
    ),
    "medium": DifficultySpec(
        name="medium",
        width=16,
        height=12,
        bot_count=3,
        aisle_count=3,
        item_types=ITEM_TYPE_POOL[:8],
        order_size_min=3,
        order_size_max=5,
    ),
    "hard": DifficultySpec(
        name="hard",
        width=22,
        height=14,
        bot_count=5,
        aisle_count=4,
        item_types=ITEM_TYPE_POOL[:12],
        order_size_min=3,
        order_size_max=5,
    ),
    "expert": DifficultySpec(
        name="expert",
        width=28,
        height=18,
        bot_count=10,
        aisle_count=5,
        item_types=ITEM_TYPE_POOL[:16],
        order_size_min=4,
        order_size_max=6,
    ),
}


@dataclass
class BotState:
    id: int
    position: tuple[int, int]
    inventory: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "position": [self.position[0], self.position[1]],
            "inventory": list(self.inventory),
        }


@dataclass
class ItemState:
    id: str
    type: str
    position: tuple[int, int]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "position": [self.position[0], self.position[1]],
        }


@dataclass
class OrderState:
    id: str
    items_required: list[str]
    items_delivered: list[str] = field(default_factory=list)
    complete: bool = False

    def outstanding_counts(self) -> dict[str, int]:
        need: dict[str, int] = {}
        for item_type in self.items_required:
            need[item_type] = need.get(item_type, 0) + 1
        for item_type in self.items_delivered:
            if item_type in need and need[item_type] > 0:
                need[item_type] -= 1
        return {item_type: count for item_type, count in need.items() if count > 0}

    def to_payload(self, *, status: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "items_required": list(self.items_required),
            "items_delivered": list(self.items_delivered),
            "complete": bool(self.complete),
            "status": status,
        }
