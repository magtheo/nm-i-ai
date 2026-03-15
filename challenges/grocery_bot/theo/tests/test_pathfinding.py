"""Tests for pathfinding module."""

import pytest
from challenges.grocery_bot.theo.pathfinding import Pathfinder


class TestPathfinder:
    """Tests for Pathfinder class."""
    
    @pytest.fixture
    def pathfinder(self):
        pf = Pathfinder()
        pf.set_map(10, 10, set())
        return pf
    
    def test_bfs_distance_same_position(self, pathfinder):
        """Test distance to same position is 0."""
        assert pathfinder.bfs_distance((5, 5), (5, 5)) == 0
    
    def test_bfs_distance_adjacent(self, pathfinder):
        """Test distance to adjacent position is 1."""
        assert pathfinder.bfs_distance((5, 5), (5, 6)) == 1
    
    def test_bfs_distance_with_walls(self, pathfinder):
        """Test pathfinding around walls."""
        pathfinder.set_map(10, 10, {(5, 5)})
        # (5,4) to (5,6) should go around (5,5)
        # Path: (5,4) -> (4,4) -> (4,5) -> (4,6) -> (5,6) = 4 steps
        # Or: (5,4) -> (6,4) -> (6,5) -> (6,6) -> (5,6) = 4 steps
        assert pathfinder.bfs_distance((5, 4), (5, 6)) == 4
    
    def test_bfs_distance_no_path(self, pathfinder):
        """Test returns -1 when no path exists."""
        # Surround (5,5) with walls
        walls = {(4, 5), (6, 5), (5, 4), (5, 6)}
        pathfinder.set_map(10, 10, walls)
        assert pathfinder.bfs_distance((5, 5), (0, 0)) == -1
    
    def test_get_next_step(self, pathfinder):
        """Test returns correct next step."""
        step = pathfinder.get_next_step((5, 5), (5, 7))
        assert step in [(5, 6)]
    
    def test_get_neighbors(self, pathfinder):
        """Test returns valid neighbors only."""
        pathfinder.set_map(10, 10, {(5, 6)})
        neighbors = pathfinder.get_neighbors(5, 5)
        assert (5, 4) in neighbors
        assert (4, 5) in neighbors
        assert (6, 5) in neighbors
        assert (5, 6) not in neighbors
    
    def test_is_valid(self, pathfinder):
        """Test validates positions correctly."""
        assert pathfinder.is_valid(0, 0) is True
        assert pathfinder.is_valid(-1, 0) is False
        assert pathfinder.is_valid(0, 10) is False
        
        pathfinder.set_map(10, 10, {(1, 1)})
        assert pathfinder.is_valid(1, 1) is False

    def test_bfs_distance_to_obstacle(self, pathfinder):
        """Test that BFS can find a path to an obstacle (like a shelf)."""
        # (1,1) is an obstacle
        pathfinder.set_obstacles({(1, 1)})
        # Distance from (0,1) to (1,1) should be 1
        assert pathfinder.bfs_distance((0, 1), (1, 1)) == 1
        # Distance from (0,0) to (1,1) should be 2
        assert pathfinder.bfs_distance((0, 0), (1, 1)) == 2

    def test_get_next_step_to_obstacle(self, pathfinder):
        """Test that get_next_step works when goal is an obstacle."""
        pathfinder.set_obstacles({(2, 2)})
        # To get to (2,2) from (0,2), first step should be (1,2)
        step = pathfinder.get_next_step((0, 2), (2, 2))
        assert step == (1, 2)

    def test_get_distances_to_positions_including_obstacles(self, pathfinder):
        """Test batched BFS handles obstacle goals correctly."""
        pathfinder.set_obstacles({(2, 2), (5, 5)})
        goals = [(2, 2), (5, 5), (0, 1)]
        distances = pathfinder.get_distances_to_positions((0, 0), goals)
        
        assert distances[(2, 2)] == 4
        assert distances[(5, 5)] == 10
        assert distances[(0, 1)] == 1
