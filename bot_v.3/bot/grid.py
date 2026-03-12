"""Grid representation and spatial utilities for the grocery store."""
from __future__ import annotations

from typing import Iterable

from .models import GridInfo, Pos


class Grid:
    """Immutable grid built from a GameState.grid payload."""

    __slots__ = ("width", "height", "_walls")

    def __init__(self, info: GridInfo):
        self.width = info.width
        self.height = info.height
        # frozenset of (x, y) tuples for O(1) wall lookup
        self._walls: frozenset[tuple[int, int]] = frozenset(
            (w[0], w[1]) for w in info.walls
        )

    # ── Queries ────────────────────────────────────────────────────────

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_wall(self, x: int, y: int) -> bool:
        return (x, y) in self._walls

    def is_walkable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and not self.is_wall(x, y)

    def neighbors(self, x: int, y: int) -> list[tuple[int, int]]:
        """Return walkable 4-directional neighbours."""
        result: list[tuple[int, int]] = []
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nx, ny = x + dx, y + dy
            if self.is_walkable(nx, ny):
                result.append((nx, ny))
        return result

    def walkable_neighbors_of(self, pos: Pos) -> list[tuple[int, int]]:
        """Return walkable cells adjacent to *pos* (pos itself may be a wall/shelf)."""
        result: list[tuple[int, int]] = []
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nx, ny = pos.x + dx, pos.y + dy
            if self.is_walkable(nx, ny):
                result.append((nx, ny))
        return result

    def all_walkable(self) -> Iterable[tuple[int, int]]:
        for y in range(self.height):
            for x in range(self.width):
                if self.is_walkable(x, y):
                    yield (x, y)
