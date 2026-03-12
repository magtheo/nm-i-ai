"""Immutable A* pathfinding used by the forge core layer."""
from __future__ import annotations

import heapq


def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def astar_next_step(
    *,
    start: tuple[int, int],
    goal: tuple[int, int],
    width: int,
    height: int,
    walls: set[tuple[int, int]],
    blocked: set[tuple[int, int]] | None = None,
) -> tuple[int, int] | None:
    """Return the next grid cell on the shortest path, or None if unreachable."""
    if start == goal:
        return start

    blocked_cells = blocked or set()

    def neighbors(cell: tuple[int, int]) -> list[tuple[int, int]]:
        x, y = cell
        out: list[tuple[int, int]] = []
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nx, ny = x + dx, y + dy
            ncell = (nx, ny)
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if ncell in walls or ncell in blocked_cells:
                continue
            out.append(ncell)
        return out

    open_heap: list[tuple[int, int, tuple[int, int]]] = []
    heapq.heappush(open_heap, (_heuristic(start, goal), 0, start))

    g_score: dict[tuple[int, int], int] = {start: 0}
    parents: dict[tuple[int, int], tuple[int, int]] = {}

    while open_heap:
        _, g_cost, current = heapq.heappop(open_heap)
        if current == goal:
            step = current
            while parents.get(step) and parents[step] != start:
                step = parents[step]
            return step

        if g_cost > g_score.get(current, 10**9):
            continue

        for nxt in neighbors(current):
            candidate = g_cost + 1
            if candidate >= g_score.get(nxt, 10**9):
                continue
            g_score[nxt] = candidate
            parents[nxt] = current
            f_cost = candidate + _heuristic(nxt, goal)
            heapq.heappush(open_heap, (f_cost, candidate, nxt))

    return None
