"""Cooperative path planning helpers used by DecisionEngine WHCA mode.

This module provides lightweight, dependency-free approximations for:
- selecting a subset of mutually conflicting bots
- planning one-step moves under short-horizon reservations

The implementation is intentionally small and robust so that WHCA mode can run
in this workspace without requiring external planner modules.
"""
from __future__ import annotations

from collections import defaultdict, deque

from .pathfinding import bfs_distance, bfs_shortest_path


PlanRow = tuple[int, tuple[int, int], tuple[int, int]]


def _normalize_cell(cell: tuple[int, int]) -> tuple[int, int]:
    return (int(cell[0]), int(cell[1]))


def _normalize_plans(plans) -> list[PlanRow]:
    out: list[PlanRow] = []
    for row in plans:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            continue
        bot_id = int(row[0])
        cur = _normalize_cell(row[1])
        desired = _normalize_cell(row[2])
        out.append((bot_id, cur, desired))
    return out


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def _bounded_path(
    *,
    grid,
    start: tuple[int, int],
    goal: tuple[int, int],
    blocked: set[tuple[int, int]],
    window: int,
) -> list[tuple[int, int]]:
    path = bfs_shortest_path(grid, start, goal, blocked=blocked)
    if not path:
        return [start]
    horizon = max(1, int(window))
    if len(path) - 1 <= horizon:
        return path
    return path[: horizon + 1]


def _is_pair_conflict(a: PlanRow, b: PlanRow) -> bool:
    _ida, cur_a, desired_a = a
    _idb, cur_b, desired_b = b

    # Competing for the same target cell.
    if desired_a == desired_b:
        return True

    # Direct swap conflict.
    if desired_a == cur_b and desired_b == cur_a:
        return True

    # One bot moves into the other bot's current location while that bot waits.
    if desired_a == cur_b and desired_b == cur_b:
        return True
    if desired_b == cur_a and desired_a == cur_a:
        return True

    return False


def largest_conflict_component(
    grid,
    plans,
    goals_by_bot,
    blocked=None,
    window: int = 8,
):
    """Return the largest connected conflict subset of bot IDs.

    Conflict edges are added for direct one-step conflicts and for near-term
    bounded path overlap to the bots' stated goals.
    """
    rows = _normalize_plans(plans)
    if not rows:
        return set()
    if len(rows) == 1:
        return {rows[0][0]}

    blocked_set = {_normalize_cell(cell) for cell in (blocked or set())}
    goals = {int(bot_id): _normalize_cell(goal) for bot_id, goal in dict(goals_by_bot or {}).items()}
    horizon = max(1, int(window))

    adjacency: dict[int, set[int]] = {bot_id: set() for bot_id, _cur, _desired in rows}

    for idx in range(len(rows)):
        bot_a, cur_a, desired_a = rows[idx]
        goal_a = goals.get(bot_a, desired_a)
        path_a = _bounded_path(
            grid=grid,
            start=cur_a,
            goal=goal_a,
            blocked=blocked_set,
            window=horizon,
        )
        body_a = set(path_a[1:])

        for jdx in range(idx + 1, len(rows)):
            bot_b, cur_b, desired_b = rows[jdx]
            if _is_pair_conflict(rows[idx], rows[jdx]):
                adjacency[bot_a].add(bot_b)
                adjacency[bot_b].add(bot_a)
                continue

            goal_b = goals.get(bot_b, desired_b)
            path_b = _bounded_path(
                grid=grid,
                start=cur_b,
                goal=goal_b,
                blocked=blocked_set,
                window=horizon,
            )
            body_b = set(path_b[1:])
            if body_a & body_b:
                adjacency[bot_a].add(bot_b)
                adjacency[bot_b].add(bot_a)

    components: list[set[int]] = []
    seen: set[int] = set()
    for bot_id in sorted(adjacency):
        if bot_id in seen:
            continue
        q = deque([bot_id])
        seen.add(bot_id)
        comp: set[int] = set()
        while q:
            cur = q.popleft()
            comp.add(cur)
            for nxt in adjacency.get(cur, set()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                q.append(nxt)
        components.append(comp)

    if not components:
        return set()
    components.sort(key=lambda comp: (-len(comp), min(comp)))
    return set(components[0])


def _reconstruct_space_time_path(
    parent: dict[tuple[tuple[int, int], int], tuple[tuple[int, int], int] | None],
    end_state: tuple[tuple[int, int], int],
) -> list[tuple[int, int]]:
    path: list[tuple[int, int]] = []
    cursor: tuple[tuple[int, int], int] | None = end_state
    while cursor is not None:
        path.append(cursor[0])
        cursor = parent.get(cursor)
    path.reverse()
    return path


def _plan_path_with_reservations(
    *,
    grid,
    start: tuple[int, int],
    goal: tuple[int, int],
    blocked: set[tuple[int, int]],
    window: int,
    vertex_reserved: dict[int, set[tuple[int, int]]],
    edge_reserved: set[tuple[tuple[int, int], tuple[int, int], int]],
) -> list[tuple[int, int]]:
    start_state = (start, 0)
    queue = deque([start_state])
    parent: dict[tuple[tuple[int, int], int], tuple[tuple[int, int], int] | None] = {start_state: None}

    best_state = start_state
    best_rank = (_manhattan(start, goal), 0)

    while queue:
        pos, t = queue.popleft()
        rank = (_manhattan(pos, goal), -int(t))
        if rank < best_rank:
            best_rank = rank
            best_state = (pos, t)

        if pos == goal:
            best_state = (pos, t)
            break

        if t >= window:
            continue

        nt = t + 1
        candidates = [pos]
        candidates.extend(grid.neighbors(pos[0], pos[1]))
        for nxt in candidates:
            nxt = _normalize_cell(nxt)
            if nxt in blocked:
                continue
            if nxt in vertex_reserved.get(nt, set()):
                continue
            # Prevent opposite-direction swap on the same tick.
            if (nxt, pos, nt) in edge_reserved:
                continue

            state = (nxt, nt)
            if state in parent:
                continue
            parent[state] = (pos, t)
            queue.append(state)

    return _reconstruct_space_time_path(parent, best_state)


def plan_windowed_next_steps(
    grid,
    plans,
    goals_by_bot,
    occupied,
    blocked=None,
    window: int = 8,
    deliverer_ids=None,
):
    """Plan cooperative one-step moves with short-horizon reservations."""
    rows = _normalize_plans(plans)
    if not rows:
        return {}

    goals = {int(bot_id): _normalize_cell(goal) for bot_id, goal in dict(goals_by_bot or {}).items()}
    blocked_set = {_normalize_cell(cell) for cell in (blocked or set())}
    occupied_set = {_normalize_cell(cell) for cell in (occupied or set())}
    deliverers = {int(bot_id) for bot_id in (deliverer_ids or set())}
    horizon = max(1, int(window))

    def priority_key(row: PlanRow) -> tuple[int, int, int]:
        bot_id, cur, desired = row
        goal = goals.get(bot_id, desired)
        dist = bfs_distance(grid, cur, goal, blocked=blocked_set)
        return (0 if bot_id in deliverers else 1, int(dist), int(bot_id))

    ordered = sorted(rows, key=priority_key)

    vertex_reserved: dict[int, set[tuple[int, int]]] = defaultdict(set)
    edge_reserved: set[tuple[tuple[int, int], tuple[int, int], int]] = set()
    for t in range(1, horizon + 1):
        vertex_reserved[t].update(occupied_set)

    next_by_bot: dict[int, tuple[int, int]] = {}

    for bot_id, cur, desired in ordered:
        goal = goals.get(bot_id, desired)
        path = _plan_path_with_reservations(
            grid=grid,
            start=cur,
            goal=goal,
            blocked=blocked_set,
            window=horizon,
            vertex_reserved=vertex_reserved,
            edge_reserved=edge_reserved,
        )
        if len(path) <= 1:
            next_cell = cur
        else:
            next_cell = path[1]

        next_by_bot[int(bot_id)] = _normalize_cell(next_cell)

        # Reserve this bot's planned path cells over the horizon.
        if path:
            for t in range(1, len(path)):
                prev = _normalize_cell(path[t - 1])
                now = _normalize_cell(path[t])
                vertex_reserved[t].add(now)
                edge_reserved.add((prev, now, t))
            last = _normalize_cell(path[-1])
            for t in range(len(path), horizon + 1):
                vertex_reserved[t].add(last)

    return next_by_bot
