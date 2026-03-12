"""Collision avoidance and one-tick reservation resolution."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .models import BotAction


@dataclass(frozen=True)
class CollisionStats:
    contested_cells: int
    blocked_moves: int
    swaps_prevented: int


def resolve_collisions_with_stats(
    plans: list[tuple[int, tuple[int, int], tuple[int, int]]],
    occupied: set[tuple[int, int]],
    *,
    reservation_horizon: int = 1,
) -> tuple[dict[int, tuple[int, int]], CollisionStats]:
    """Resolve move plans in bot-id order using a reservation table."""
    if reservation_horizon < 1:
        reservation_horizon = 1
    sorted_plans = sorted(plans, key=lambda p: p[0])
    desired_counts = Counter(desired for _bot_id, _cur, desired in sorted_plans)
    contested_cells = sum(1 for _cell, cnt in desired_counts.items() if cnt > 1)

    cur_by_bot: dict[int, tuple[int, int]] = {}
    desired_by_bot: dict[int, tuple[int, int]] = {}
    bot_by_cur: dict[tuple[int, int], int] = {}
    for bot_id, cur, desired in sorted_plans:
        cur_by_bot[bot_id] = cur
        desired_by_bot[bot_id] = desired
        bot_by_cur[cur] = bot_id

    reserved: set[tuple[int, int]] = set(occupied)
    resolved: dict[int, tuple[int, int]] = {}
    blocked_moves = 0
    swaps_prevented = 0

    for bot_id, cur, desired in sorted_plans:
        if desired in reserved:
            resolved[bot_id] = cur
            reserved.add(cur)
            blocked_moves += 1
            continue

        # Prevent direct A<->B swaps by forcing the current bot to wait.
        other_id = bot_by_cur.get(desired)
        if other_id is not None and desired_by_bot.get(other_id) == cur:
            resolved[bot_id] = cur
            reserved.add(cur)
            blocked_moves += 1
            swaps_prevented += 1
            continue

        resolved[bot_id] = desired
        reserved.add(desired)

    stats = CollisionStats(
        contested_cells=contested_cells,
        blocked_moves=blocked_moves,
        swaps_prevented=swaps_prevented,
    )
    return resolved, stats


def resolve_collisions(
    plans: list[tuple[int, tuple[int, int], tuple[int, int]]],
    occupied: set[tuple[int, int]],
    *,
    reservation_horizon: int = 1,
) -> dict[int, tuple[int, int]]:
    resolved, _stats = resolve_collisions_with_stats(
        plans,
        occupied,
        reservation_horizon=reservation_horizon,
    )
    return resolved


def action_for_move(
    current: tuple[int, int],
    target: tuple[int, int],
) -> BotAction:
    """Return the BotAction to move from *current* toward *target* (one step)."""
    dx = target[0] - current[0]
    dy = target[1] - current[1]
    if dx == 1:
        return BotAction.MOVE_RIGHT
    if dx == -1:
        return BotAction.MOVE_LEFT
    if dy == 1:
        return BotAction.MOVE_DOWN
    if dy == -1:
        return BotAction.MOVE_UP
    return BotAction.WAIT
