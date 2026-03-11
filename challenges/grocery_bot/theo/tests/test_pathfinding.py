"""Tests for pathfinding module."""

import pytest
from challenges.grocery_bot.theo.pathfinding import Pathfinder


class TestPathfinder:
    """Tests for Pathfinder class."""
    
    def test_bfs_distance_same_position(self):
        """Test distance to same position is 0."""
        pf = Pathfinder()
        pf.set_map(10, 10, set())
        
        assert pf.bfs_distance((5, 5), (5, 5)) == 0
    
    def test_bfs_distance_adjacent(self):
        """Test distance to adjacent position is 1."""
        pf = Pathfinder()
        pf.set_map(10, 10, set())
        
        assert pf.bfs_distance((5, 5), (5, 6)) == 1
        assert pf.bfs_distance((5, 5), (6, 5)) == 1
    
    def test_bfs_distance_with_walls(self):
        """Test pathfinding around walls."""
        pf = Pathfinder()
        # Create a wall between (5,5) and (5,6)
        pf.set_map(10, 10, {(5, 5)})
        
        # Can't go through wall
        assert pf.bfs_distance((5, 5), (5, 6)) == -1
        
        # Can go around
        assert pf.bfs_distance((4, 5), (6, 5)) == 4  # Go around wall
    
    def test_bfs_distance_no_path(self):
        """Test that -1 is returned when no path exists."""
        pf = Pathfinder()
        # Create a ring of walls around target
        walls = {(5, 4), (5, 6), (4, 5), (6, 5)}
        pf.set_map(10, 10, walls)
        
        # Actually this doesn't block - let's make a proper enclosure
        walls = {
            (4, 4), (5, 4), (6, 4),
            (4, 5),         (6, 5),
            (4, 6), (5, 6), (6, 6)
        }
        pf.set_map(10, 10, walls)
        
        assert pf.bfs_distance((5, 5), (8, 8)) == -1
    
    def test_get_next_step(self):
        """Test getting the next step toward a goal."""
        pf = Pathfinder()
        pf.set_map(10, 10, set())
        
        # Should move toward goal
        next_step = pf.get_next_step((0, 0), (5, 5))
        assert next_step in [(1, 0), (0, 1)]
    
    def test_get_neighbors(self):
        """Test getting valid neighbors."""
        pf = Pathfinder()
        pf.set_map(10, 10, {(5, 4)})  # Wall above (5,5)
        
        neighbors = pf.get_neighbors(5, 5)
        
        assert (5, 4) not in neighbors  # Wall
        assert (5, 6) in neighbors  # Down
        assert (4, 5) in neighbors  # Left
        assert (6, 5) in neighbors  # Right
        assert len(neighbors) == 3
    
    def test_is_valid(self):
        """Test position validity check."""
        pf = Pathfinder()
        pf.set_map(10, 10, {(5, 5)})
        
        assert pf.is_valid(5, 5) is False  # Wall
        assert pf.is_valid(0, 0) is True
        assert pf.is_valid(9, 9) is True
        assert pf.is_valid(-1, 0) is False  # Out of bounds
        assert pf.is_valid(10, 0) is False  # Out of bounds
