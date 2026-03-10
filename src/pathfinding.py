"""BFS pathfinding with congestion awareness."""

import logging
from collections import deque
from typing import Optional
from dataclasses import dataclass

from config import CACHE_DISTANCE_TABLES

logger = logging.getLogger(__name__)


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
        self._distance_cache: dict[tuple, dict[tuple, int]] = {}
        self._congestion_map: dict[tuple[int, int], float] = {}
    
    def set_map(self, width: int, height: int, walls: set[tuple[int, int]]) -> None:
        """Set the current map configuration."""
        if self.width == width and self.height == height and self.walls == walls:
            return
        
        self.width = width
        self.height = height
        self.walls = walls
        self._distance_cache = {}
        self._congestion_map = {}
        logger.debug(f"Map set: {width}x{height}, {len(walls)} walls")
    
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
                self._congestion_map[neighbor] = self._congestion_map.get(neighbor, 0) + 0.3
    
    def is_valid(self, x: int, y: int) -> bool:
        """Check if a position is valid for movement."""
        if x < 0 or x >= self.width:
            return False
        if y < 0 or y >= self.height:
            return False
        return (x, y) not in self.walls
    
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
        use_congestion: bool = False
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
        
        if not self.is_valid(start[0], start[1]) or not self.is_valid(goal[0], goal[1]):
            return -1
        
        # Check cache (only for simple distance queries)
        if not use_congestion and CACHE_DISTANCE_TABLES:
            if start in self._distance_cache:
                if goal in self._distance_cache[start]: return self._distance_cache[start][goal]
        
        visited = {start}
        queue = deque([(start, 0)])
        
        while queue:
            current, dist = queue.popleft()
            
            for neighbor in self.get_neighbors(*current):
                if neighbor == goal:
                    if not use_congestion and CACHE_DISTANCE_TABLES:
                        if start not in self._distance_cache:
                            self._distance_cache[start] = {}
                        self._distance_cache[start][goal] = dist + 1
                    return dist + 1
                
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))
        
        return -1
    
    def get_next_step(
        self, 
        start: tuple[int, int], 
        goal: tuple[int, int],
        use_congestion: bool = False
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
        
        if not self.is_valid(start[0], start[1]) or not self.is_valid(goal[0], goal[1]):
            return None
        
        # BFS from goal to find shortest path
        visited = {goal}
        queue = deque([goal])
        parent = {goal: None}
        
        while queue:
            current = queue.popleft()
            
            if current == start:
                # Reconstruct first step
                path = []
                node = start
                while parent[node] is not None:
                    path.append(node)
                    node = parent[node]
                return path[1] if len(path) > 1 else (parent[start] if start in parent else None)
            
            neighbors = self.get_neighbors(*current)
            
            # Sort neighbors by congestion (less congested first)
            if use_congestion and self._congestion_map:
                neighbors.sort(key=lambda n: self._congestion_map.get(n, 0))
            
            for neighbor in neighbors:
                if neighbor not in visited:
                    visited.add(neighbor)
                    parent[neighbor] = current
                    queue.append(neighbor)
        
        return None
    
    def find_path_with_congestion(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
        bot_positions: list[tuple[int, int]]
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
        self, 
        positions: list[tuple[int, int]]
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
