"""Mutation zone for Codex: high-level strategy intents only.

Required interface:
    decide_intents(game_state: dict) -> list[dict]
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _active_and_preview(orders: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    active = None
    preview = None
    for order in orders:
        status = str(order.get("status", "")).strip().lower()
        if status == "active":
            active = order
        elif status == "preview":
            preview = order
    return active, preview


def _remaining_need(order: dict[str, Any] | None) -> Counter[str]:
    if not order:
        return Counter()
    required = Counter(str(v) for v in order.get("items_required", []))
    delivered = Counter(str(v) for v in order.get("items_delivered", []))
    for item_type, delivered_count in delivered.items():
        required[item_type] -= delivered_count
        if required[item_type] <= 0:
            del required[item_type]
    return required


def _walkable_neighbors(cell: tuple[int, int], walls: set[tuple[int, int]], width: int, height: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        nx, ny = cell[0] + dx, cell[1] + dy
        ncell = (nx, ny)
        if nx < 0 or ny < 0 or nx >= width or ny >= height:
            continue
        if ncell in walls:
            continue
        out.append(ncell)
    return out


def decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return high-level intents consumed by immutable forge core."""
    grid = dict(game_state.get("grid", {}))
    width = int(grid.get("width", 0))
    height = int(grid.get("height", 0))
    walls = {
        (int(w[0]), int(w[1]))
        for w in grid.get("walls", [])
        if isinstance(w, (list, tuple)) and len(w) == 2
    }

    bots = sorted(list(game_state.get("bots", [])), key=lambda row: int(row.get("id", -1)))
    items = list(game_state.get("items", []))
    orders = list(game_state.get("orders", []))
    drop_off_raw = game_state.get("drop_off", [0, 0])
    drop_off = (int(drop_off_raw[0]), int(drop_off_raw[1]))

    active_order, preview_order = _active_and_preview(orders)
    active_need = _remaining_need(active_order)
    preview_need = _remaining_need(preview_order)

    reserved_item_ids: set[str] = set()
    intents: list[dict[str, Any]] = []

    for bot in bots:
        bot_id = int(bot.get("id", -1))
        pos_raw = bot.get("position", [0, 0])
        bot_pos = (int(pos_raw[0]), int(pos_raw[1]))
        inventory = [str(v) for v in bot.get("inventory", [])]

        # If we can score now, deliver.
        if bot_pos == drop_off:
            if any(active_need.get(item_type, 0) > 0 for item_type in inventory):
                intents.append({"bot": bot_id, "action": "drop_off"})
                continue
        elif any(active_need.get(item_type, 0) > 0 for item_type in inventory):
            intents.append({"bot": bot_id, "target": [drop_off[0], drop_off[1]]})
            continue

        if len(inventory) >= 3:
            intents.append({"bot": bot_id, "target": [drop_off[0], drop_off[1]]})
            continue

        best_item: dict[str, Any] | None = None
        best_target: tuple[int, int] | None = None
        best_distance = 10**9

        for item in items:
            item_id = str(item.get("id", ""))
            if not item_id or item_id in reserved_item_ids:
                continue
            item_type = str(item.get("type", ""))
            item_pos_raw = item.get("position", [0, 0])
            item_pos = (int(item_pos_raw[0]), int(item_pos_raw[1]))

            demand_weight = 0
            if active_need.get(item_type, 0) > 0:
                demand_weight = 2
            elif preview_need.get(item_type, 0) > 0:
                demand_weight = 1
            if demand_weight == 0:
                continue

            if _manhattan(bot_pos, item_pos) == 1:
                best_item = item
                best_target = bot_pos
                best_distance = -demand_weight
                break

            for pickup_cell in _walkable_neighbors(item_pos, walls, width, height):
                distance = _manhattan(bot_pos, pickup_cell)
                score = (distance * 10) - demand_weight
                if score < best_distance:
                    best_distance = score
                    best_item = item
                    best_target = pickup_cell

        if best_item is None or best_target is None:
            intents.append({"bot": bot_id, "action": "wait"})
            continue

        reserved_item_ids.add(str(best_item.get("id", "")))
        item_pos_raw = best_item.get("position", [0, 0])
        item_pos = (int(item_pos_raw[0]), int(item_pos_raw[1]))

        if _manhattan(bot_pos, item_pos) == 1:
            intents.append(
                {
                    "bot": bot_id,
                    "action": "pick_up",
                    "item_id": str(best_item.get("id")),
                }
            )
            continue

        intents.append({"bot": bot_id, "target": [best_target[0], best_target[1]]})

    return intents
