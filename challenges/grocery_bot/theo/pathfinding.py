"""BFS pathfinding with congestion awareness."""

from collections import deque
from typing import Optional
from dataclasses import dataclass

from challenges.grocery_bot.shared.config import CACHE_DISTANCE_TABLES
from tools.logging_config import get_logger, LogCategory

logger = get_logger(LogCategory.PATHFINDING)


@dataclass
class PathResult:
    """Result of a pathfinding query."""

    distance: int
    next_step: Optional[tuple[int, int]]
    path: list[tuple[int, int]]


class Pathfinder:
    """Handles pathfinding using BFS with congestion awareness."""

    def __init__(self):
        self.width = 0
        self.height = 0
        self.walls: set[tuple[int, int]] = set()
        self.obstacles: set[tuple[int, int]] = set()
        self._dynamic_obstacles: set[tuple[int, int]] = set()
        self._distance_cache: dict[tuple, dict[tuple, int]] = {}
        self._congestion_map: dict[tuple[int, int], float] = {}

    def set_map(self, width: int, height: int, walls: set[tuple[int, int]]) -> None:
        """Set the current map configuration."""
        if self.width == width and self.height == height and self.walls == walls:
            return

        self.width = width
        self.height = height
        self.walls = walls
        self.obstacles = set()
        self._dynamic_obstacles = set()
        self._distance_cache = {}
        self._congestion_map = {}
        logger.debug(f"Map set: {width}x{height}, {len(walls)} walls")

    def invalidate_positions(self, positions: set[tuple[int, int]]) -> None:
        """Invalidate only cache entries involving the given positions.

        This is smarter than clearing the entire cache - it only removes
        entries where either the start or goal is one of the changed positions.

        Args:
            positions: Set of positions that have changed
        """
        if not self._distance_cache:
            return

        positions_to_clear = positions & self._distance_cache.keys()
        for pos in positions_to_clear:
            del self._distance_cache[pos]

        for start_pos in list(self._distance_cache.keys()):
            goals_to_remove = [
                goal for goal in self._distance_cache[start_pos] if goal in positions
            ]
            for goal in goals_to_remove:
                del self._distance_cache[start_pos][goal]

        logger.debug(f"Invalidated cache for {len(positions)} positions")

    def set_obstacles(self, positions: set[tuple[int, int]]) -> None:
        """Set shelf positions where items are placed (semi-permanent obstacles)."""
        if self.obstacles == positions:
            return

        self.obstacles = positions.copy()
        # Full cache clear on obstacle changes to avoid stale intermediate paths
        self._distance_cache.clear()

        logger.debug(f"Obstacles set: {len(positions)} shelf positions, cache cleared")

    def add_dynamic_obstacle(self, position: tuple[int, int]) -> None:
        """Mark a position as blocked at runtime (e.g., bot got stuck there)."""
        self._dynamic_obstacles.add(position)
        logger.debug(f"Dynamic obstacle added at {position}")

    def clear_dynamic_obstacles(self) -> None:
        """Reset all dynamic obstacles (call at start of each round)."""
        self._dynamic_obstacles = set()
        logger.debug("Dynamic obstacles cleared")

    def remove_obstacle(self, position: tuple[int, int]) -> None:
        """Remove a specific obstacle when an item is picked up."""
        self.obstacles.discard(position)
        self._dynamic_obstacles.discard(position)
        # Full cache clear to avoid stale intermediate paths
        self._distance_cache.clear()
        logger.debug(f"Obstacle removed at {position}, cache cleared")

    def update_congestion(self, bot_positions: list[tuple[int, int]]) -> None:
        """Update congestion map based on bot positions.

        Higher congestion near bots makes paths through those areas
        less desirable.
        """
        self._congestion_map = {}

        for pos in bot_positions:
            # Add congestion at bot position
            self._congestion_map[pos] = self._congestion_map.get(pos, 0) + 1.0

            # Add smaller congestion to neighbors
            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                neighbor = (pos[0] + dx, pos[1] + dy)
                self._congestion_map[neighbor] = (
                    self._congestion_map.get(neighbor, 0) + 0.3
                )

    def is_valid(self, x: int, y: int) -> bool:
        """Check if a position is valid for movement."""
        if x < 0 or x >= self.width:
            return False
        if y < 0 or y >= self.height:
            return False
        pos = (x, y)
        return (
            pos not in self.walls
            and pos not in self.obstacles
            and pos not in self._dynamic_obstacles
        )

    def get_neighbors(self, x: int, y: int) -> list[tuple[int, int]]:
        """Get valid neighboring positions."""
        neighbors = []
        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            nx, ny = x + dx, y + dy
            if self.is_valid(nx, ny):
                neighbors.append((nx, ny))
        return neighbors

    def bfs_distance(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
        use_congestion: bool = False,
    ) -> int:
        """Calculate shortest path distance using BFS.

        Args:
            start: Starting position
            goal: Target position
            use_congestion: If True, consider congestion in path cost

        Returns:
            Distance in steps, or -1 if no path exists
        """
        if start == goal:
            return 0

        # Goal is allowed to be an obstacle (shelf), but start must be valid
        if not self.is_valid(start[0], start[1]):
            return -1

        # Check cache (only for simple distance queries)
        if not use_congestion and CACHE_DISTANCE_TABLES:
            if start in self._distance_cache:
                if goal in self._distance_cache[start]:
                    return self._distance_cache[start][goal]

        visited = {start}
        queue = deque([(start, 0)])

        while queue:
            current, dist = queue.popleft()

            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                neighbor = (current[0] + dx, current[1] + dy)
                if neighbor == goal:
                    if not use_congestion and CACHE_DISTANCE_TABLES:
                        if start not in self._distance_cache:
                            self._distance_cache[start] = {}
                        self._distance_cache[start][goal] = dist + 1
                    return dist + 1

                if self.is_valid(neighbor[0], neighbor[1]) and neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))

        return -1

    def get_distances_to_positions(
        self,
        start: tuple[int, int],
        goals: list[tuple[int, int]],
        use_congestion: bool = False,
    ) -> dict[tuple[int, int], int]:
        """Run BFS once from start and return distances to all goal positions.

        Much more efficient than calling bfs_distance() multiple times.

        Args:
            start: Starting position
            goals: List of target positions
            use_congestion: If True, consider congestion in path cost

        Returns:
            Dict mapping each goal position to its distance (or -1 if unreachable)
        """
        if not goals:
            return {}

        result = {goal: -1 for goal in goals}
        goals_set = set(goals)

        if start in goals_set:
            result[start] = 0
            goals_set.discard(start)
            if not goals_set:
                return result

        if not self.is_valid(start[0], start[1]):
            return result

        # Only filter goals that are outside map bounds
        goals_set = {
            goal
            for goal in goals_set
            if 0 <= goal[0] < self.width and 0 <= goal[1] < self.height
        }

        if not goals_set:
            return result

        visited = {start}
        queue = deque([(start, 0)])

        while queue and goals_set:
            current, dist = queue.popleft()

            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                neighbor = (current[0] + dx, current[1] + dy)
                if neighbor in goals_set:
                    result[neighbor] = dist + 1
                    goals_set.discard(neighbor)
                    if not use_congestion and CACHE_DISTANCE_TABLES:
                        if start not in self._distance_cache:
                            self._distance_cache[start] = {}
                        self._distance_cache[start][neighbor] = dist + 1

                if self.is_valid(neighbor[0], neighbor[1]) and neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))

        return result

    def get_next_step(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
        use_congestion: bool = False,
    ) -> Optional[tuple[int, int]]:
        """Get the next step toward a goal using BFS.

        Args:
            start: Starting position
            goal: Target position
            use_congestion: If True, avoid congested areas

        Returns:
            Position to move to, or None if no path exists
        """
        if start == goal:
            return None

        if not self.is_valid(start[0], start[1]):
            return None

        # BFS from goal to find shortest path
        visited = {goal}
        queue = deque([goal])
        parent = {goal: None}

        while queue:
            current = queue.popleft()

            if current == start:
                if start in parent:
                    return parent[start]
                return None

            # For the first step (the goal itself), neighbors don't have to be "valid" in the moving sense
            # but for all subsequent steps, they must be.
            for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                neighbor = (current[0] + dx, current[1] + dy)
                
                # If we're at the goal, we can move to any valid neighbor
                # If we're not at the goal, we must have come from a valid neighbor
                if neighbor not in visited:
                    if neighbor == start or self.is_valid(neighbor[0], neighbor[1]):
                        visited.add(neighbor)
                        parent[neighbor] = current
                        queue.append(neighbor)

        return None

    def find_path_with_congestion(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
        bot_positions: list[tuple[int, int]],
    ) -> PathResult:
        """Find path considering current bot positions.

        This is used for multi-bot scenarios to help avoid congestion.
        """
        # Update congestion based on bot positions
        self.update_congestion(bot_positions)

        distance = self.bfs_distance(start, goal, use_congestion=True)
        next_step = self.get_next_step(start, goal, use_congestion=True)

        # Get full path for reference
        path = []
        if distance > 0:
            current = start
            for _ in range(distance):
                step = self.get_next_step(current, goal, use_congestion=True)
                if step:
                    path.append(step)
                    current = step
                else:
                    break

        return PathResult(distance=distance, next_step=next_step, path=path)

    def get_distance_table(
        self, positions: list[tuple[int, int]]
    ) -> dict[tuple, dict[tuple, int]]:
        """Compute distances between all pairs of positions.

        Args:
            positions: List of positions to compute distances between

        Returns:
            Dict mapping (from_pos) -> (to_pos) -> distance
        """
        table = {}
        for pos in positions:
            table[pos] = {}
            for other in positions:
                if pos != other:
                    table[pos][other] = self.bfs_distance(pos, other)
                else:
                    table[pos][other] = 0
        return table

    def precompute_distances(self, positions: list[tuple[int, int]]) -> None:
        """Pre-compute and cache all pairwise distances between given positions.

        This is useful to call at initialization for all shelf positions,
        so subsequent distance queries are O(1) lookups.

        Args:
            positions: List of positions to precompute distances between
        """
        if not CACHE_DISTANCE_TABLES:
            return

        for pos in positions:
            if pos not in self._distance_cache:
                self._distance_cache[pos] = {}

            other_positions = [
                other
                for other in positions
                if other != pos and other not in self._distance_cache[pos]
            ]
            if other_positions:
                distances = self.get_distances_to_positions(pos, other_positions)
                for other, dist in distances.items():
                    self._distance_cache[pos][other] = dist

        logger.debug(f"Precomputed distances for {len(positions)} positions")

    def _bfs_distance_uncached(
        self, start: tuple[int, int], goal: tuple[int, int]
    ) -> int:
        """Calculate shortest path distance using BFS without caching.

        Internal method used by precompute_distances to avoid redundant
        cache checks.

        Args:
            start: Starting position
            goal: Target position

        Returns:
            Distance in steps, or -1 if no path exists
        """
        if start == goal:
            return 0

        if not self.is_valid(start[0], start[1]) or not self.is_valid(goal[0], goal[1]):
            return -1

        visited = {start}
        queue = deque([(start, 0)])

        while queue:
            current, dist = queue.popleft()

            for neighbor in self.get_neighbors(*current):
                if neighbor == goal:
                    return dist + 1

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))

        return -1

    def get_congestion_at(self, position: tuple[int, int]) -> float:
        """Get congestion level at a position."""
        return self._congestion_map.get(position, 0.0)

    def is_bottleneck(self, position: tuple[int, int]) -> bool:
        """Check if a position is a potential bottleneck.

        A bottleneck is a position with few escape routes (like a corridor).
        """
        if not self.is_valid(position[0], position[1]):
            return False

        neighbors = self.get_neighbors(*position)

        # A position with only 2 neighbors is likely a corridor
        # Positions with 1 neighbor are dead ends
        return len(neighbors) <= 2
