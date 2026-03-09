"""BFS pathfinding and distance calculations."""

import logging
from collections import deque
from typing import Optional

from config import CACHE_DISTANCE_TABLES

logger = logging.getLogger(__name__)


class Pathfinder:
    """Handles pathfinding using BFS."""
    
    def __init__(self):
        self.width = 0
        self.height = 0
        self.walls: set[tuple[int, int]] = set()
        self._distance_cache: dict[tuple, dict[tuple, int]] = {}
    
    def set_map(self, width: int, height: int, walls: set[tuple[int, int]]) -> None:
        """Set the current map configuration."""
        # Check if map changed
        if self.width == width and self.height == height and self.walls == walls:
            return
        
        self.width = width
        self.height = height
        self.walls = walls
        self._distance_cache = {}
        logger.debug(f"Map set: {width}x{height}, {len(walls)} walls")
    
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
    
    def bfs_distance(self, start: tuple[int, int], goal: tuple[int, int]) -> int:
        """Calculate shortest path distance using BFS.
        
        Returns -1 if no path exists.
        """
        if start == goal:
            return 0
        
        if not self.is_valid(start[0], start[1]) or not self.is_valid(goal[0], goal[1]):
            return -1
        
        # Check cache
        if CACHE_DISTANCE_TABLES and start in self._distance_cache:
            return self._distance_cache[start].get(goal, -1)
        
        visited = {start}
        queue = deque([(start, 0)])
        
        while queue:
            current, dist = queue.popleft()
            
            for neighbor in self.get_neighbors(*current):
                if neighbor == goal:
                    # Cache the result
                    if CACHE_DISTANCE_TABLES:
                        if start not in self._distance_cache:
                            self._distance_cache[start] = {}
                        self._distance_cache[start][goal] = dist + 1
                    return dist + 1
                
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))
        
        return -1  # No path found
    
    def get_next_step(self, start: tuple[int, int], goal: tuple[int, int]) -> Optional[tuple[int, int]]:
        """Get the next step toward a goal using BFS.
        
        Returns the position to move to, or None if no path exists.
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
                return path[0] if path else None
            
            for neighbor in self.get_neighbors(*current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    parent[neighbor] = current
                    queue.append(neighbor)
        
        return None  # No path found
    
    def get_distance_table(self, positions: list[tuple[int, int]]) -> dict[tuple, dict[tuple, int]]:
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
