"""Improved baseline strategy for bot_v.3 Forge — incorporating best v.2 insights.

Key improvements over the original minimal strategy:
  - Strict active-first ordering (never waste capacity on preview when active needs it)
  - Delivery concurrency cap (max 2 simultaneous deliverers prevents drop-off congestion)
  - Committed demand accounting (don't re-assign items already being carried to drop-off)
  - Transition stash: when order ≥90% complete, hold empty bots near drop-off
  - Anti-starvation: bots stuck for STARVATION_ROUNDS rounds get freed to pick anything

Required interface (do NOT change):
    decide_intents(game_state: dict) -> list[dict]
"""
from __future__ import annotations

from collections import Counter
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Configuration (tune these via experiments)
# ─────────────────────────────────────────────────────────────────────────────

MAX_DELIVERERS = 2
COMMIT_RADIUS = 2
STARVATION_ROUNDS = 6
PREVIEW_SAFETY_SLOTS = 1
TRANSITION_STASH_COMPLETION = 0.9


# ─────────────────────────────────────────────────────────────────────────────
# Module-level stall tracking state
# ─────────────────────────────────────────────────────────────────────────────

_prev_positions: dict[int, tuple[int, int]] = {}
_stall_counts: dict[int, int] = {}


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _active_and_preview(
    orders: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    active = preview = None
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
    result: Counter[str] = Counter()
    for item_type, count in required.items():
        remaining = count - delivered.get(item_type, 0)
        if remaining > 0:
            result[item_type] = remaining
    return result


def _walkable_neighbors(
    cell: tuple[int, int],
    walls: set[tuple[int, int]],
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        nx, ny = cell[0] + dx, cell[1] + dy
        if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in walls:
            out.append((nx, ny))
    return out


def _committed_bot_ids(
    bots: list[dict[str, Any]],
    active_need: Counter[str],
    drop_off: tuple[int, int],
    commit_radius: int,
) -> set[int]:
    """Bots carrying active-matching items AND near drop-off or inventory-full."""
    needed = dict(active_need)
    committed: set[int] = set()
    for bot in sorted(bots, key=lambda b: int(b.get("id", -1))):
        inv = [str(v) for v in bot.get("inventory", [])]
        matching = sum(1 for t in inv if needed.get(t, 0) > 0)
        if matching <= 0:
            continue
        pos_raw = bot.get("position", [0, 0])
        bot_pos = (int(pos_raw[0]), int(pos_raw[1]))
        near = _manhattan(bot_pos, drop_off) <= commit_radius
        full = len(inv) >= 3
        if near or full:
            committed.add(int(bot.get("id", -1)))
            for t in inv:
                if needed.get(t, 0) > 0:
                    needed[t] -= 1
    return committed


def _completion_ratio(order: dict[str, Any] | None) -> float:
    if not order:
        return 0.0
    req = order.get("items_required", [])
    dlv = order.get("items_delivered", [])
    if not req:
        return 1.0
    return min(1.0, len(dlv) / len(req))


def _update_stalls(bots: list[dict[str, Any]]) -> None:
    for bot in bots:
        bot_id = int(bot.get("id", -1))
        pos_raw = bot.get("position", [0, 0])
        cur = (int(pos_raw[0]), int(pos_raw[1]))
        if _prev_positions.get(bot_id) == cur:
            _stall_counts[bot_id] = _stall_counts.get(bot_id, 0) + 1
        else:
            _stall_counts[bot_id] = 0
        _prev_positions[bot_id] = cur


def _is_stalled(bot_id: int) -> bool:
    return _stall_counts.get(bot_id, 0) >= STARVATION_ROUNDS


def decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return high-level intents consumed by the immutable forge core."""
    grid = dict(game_state.get("grid", {}))
    width = int(grid.get("width", 0))
    height = int(grid.get("height", 0))
    walls: set[tuple[int, int]] = {
        (int(w[0]), int(w[1]))
        for w in grid.get("walls", [])
        if isinstance(w, (list, tuple)) and len(w) == 2
    }

    bots = sorted(game_state.get("bots", []), key=lambda b: int(b.get("id", -1)))
    items = list(game_state.get("items", []))
    orders = list(game_state.get("orders", []))
    drop_off_raw = game_state.get("drop_off", [0, 0])
    drop_off = (int(drop_off_raw[0]), int(drop_off_raw[1]))

    _update_stalls(bots)

    active_order, preview_order = _active_and_preview(orders)
    active_need = _remaining_need(active_order)
    preview_need = _remaining_need(preview_order)

    committed_ids = _committed_bot_ids(bots, active_need, drop_off, COMMIT_RADIUS)

    # Serviceable deficit: active need minus what committed bots are already carrying
    serviceable_deficit: Counter[str] = Counter(active_need)
    for bot in sorted(bots, key=lambda b: int(b.get("id", -1))):
        if int(bot.get("id", -1)) not in committed_ids:
            continue
        for t in bot.get("inventory", []):
            t = str(t)
            if serviceable_deficit.get(t, 0) > 0:
                serviceable_deficit[t] -= 1
                if serviceable_deficit[t] == 0:
                    del serviceable_deficit[t]

    # Preview prefetch gate
    total_free_slots = sum(max(0, 3 - len(bot.get("inventory", []))) for bot in bots)
    can_preview = (
        len(serviceable_deficit) == 0
        and len(preview_need) > 0
        and total_free_slots > PREVIEW_SAFETY_SLOTS
    )

    # Transition stash gate
    transition_mode = _completion_ratio(active_order) >= TRANSITION_STASH_COMPLETION

    reserved_item_ids: set[str] = set()
    intents: list[dict[str, Any]] = []

    # ── Pass 1: delivery assignment (closest-first, capped at MAX_DELIVERERS) ──
    delivery_candidates: list[tuple[int, int, dict[str, Any]]] = []  # (dist, id, bot)
    for bot in bots:
        inv = [str(t) for t in bot.get("inventory", [])]
        pos_raw = bot.get("position", [0, 0])
        bot_pos = (int(pos_raw[0]), int(pos_raw[1]))
        if any(active_need.get(t, 0) > 0 for t in inv):
            delivery_candidates.append((_manhattan(bot_pos, drop_off), int(bot.get("id", -1)), bot))

    delivery_candidates.sort()
    assigned_delivery: set[int] = set()
    for dist, bot_id, _bot in delivery_candidates:
        if len(assigned_delivery) >= MAX_DELIVERERS and not _is_stalled(bot_id):
            continue
        assigned_delivery.add(bot_id)

    # ── Pass 2: per-bot intents ──────────────────────────────────────────────
    for bot in bots:
        bot_id = int(bot.get("id", -1))
        pos_raw = bot.get("position", [0, 0])
        bot_pos = (int(pos_raw[0]), int(pos_raw[1]))
        inv = [str(t) for t in bot.get("inventory", [])]

        # Deliver
        if bot_id in assigned_delivery:
            if bot_pos == drop_off:
                intents.append({"bot": bot_id, "action": "drop_off"})
            else:
                intents.append({"bot": bot_id, "target": list(drop_off)})
            continue

        # Full inventory → force deliver
        if len(inv) >= 3:
            if bot_pos == drop_off:
                intents.append({"bot": bot_id, "action": "drop_off"})
            else:
                intents.append({"bot": bot_id, "target": list(drop_off)})
            continue

        # Transition stash: empty bots wait when order is nearly done and bots are covering it
        if transition_mode and len(inv) == 0:
            bots_covering = sum(
                1 for b in bots
                if any(active_need.get(str(t), 0) > 0 for t in b.get("inventory", []))
            )
            if bots_covering >= len(active_need):
                intents.append({"bot": bot_id, "action": "wait"})
                continue

        # Determine demand to serve
        if len(serviceable_deficit) > 0 or not can_preview:
            demand_counter: Counter[str] = Counter(serviceable_deficit)
            demand_weight_map = {t: 10.0 for t in demand_counter}
        else:
            demand_counter = Counter(preview_need)
            demand_weight_map = {t: 3.0 for t in demand_counter}

        # Stalled bots get access to all demand
        if _is_stalled(bot_id):
            demand_counter = Counter(serviceable_deficit) + Counter(preview_need)
            demand_weight_map = {
                t: (10.0 if t in serviceable_deficit else 3.0)
                for t in demand_counter
            }

        best_item: dict[str, Any] | None = None
        best_target: tuple[int, int] | None = None
        best_score: float = -1e18

        for item in items:
            item_id = str(item.get("id", ""))
            if not item_id or item_id in reserved_item_ids:
                continue
            item_type = str(item.get("type", ""))
            if demand_counter.get(item_type, 0) <= 0:
                continue
            demand_w = demand_weight_map.get(item_type, 1.0)

            item_pos_raw = item.get("position", [0, 0])
            item_pos = (int(item_pos_raw[0]), int(item_pos_raw[1]))

            # Adjacent (directly pickable)?
            if _manhattan(bot_pos, item_pos) == 1:
                best_item = item
                best_target = bot_pos
                best_score = demand_w + 1000
                break

            pickup_cells = _walkable_neighbors(item_pos, walls, width, height)
            if not pickup_cells:
                continue

            nearest = min(pickup_cells, key=lambda p: _manhattan(bot_pos, p))
            dist = _manhattan(bot_pos, nearest)
            score = demand_w - dist
            if score > best_score:
                best_score = score
                best_item = item
                best_target = nearest

        if best_item is None or best_target is None:
            if inv:
                intents.append({"bot": bot_id, "target": list(drop_off)})
            else:
                intents.append({"bot": bot_id, "action": "wait"})
            continue

        reserved_item_ids.add(str(best_item.get("id", "")))
        item_type = str(best_item.get("type", ""))
        if serviceable_deficit.get(item_type, 0) > 0:
            serviceable_deficit[item_type] -= 1
            if serviceable_deficit[item_type] == 0:
                del serviceable_deficit[item_type]
        elif preview_need.get(item_type, 0) > 0:
            preview_need[item_type] -= 1
            if preview_need[item_type] == 0:
                del preview_need[item_type]

        item_pos_raw = best_item.get("position", [0, 0])
        item_pos = (int(item_pos_raw[0]), int(item_pos_raw[1]))

        if _manhattan(bot_pos, item_pos) == 1:
            intents.append({
                "bot": bot_id,
                "action": "pick_up",
                "item_id": str(best_item.get("id", "")),
            })
        else:
            intents.append({"bot": bot_id, "target": [best_target[0], best_target[1]]})

    return intents
