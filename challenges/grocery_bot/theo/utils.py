"""Shared utility functions and constants."""

MAX_INVENTORY_SIZE = 3


def is_adjacent(pos1: tuple[int, int], pos2: tuple[int, int]) -> bool:
    """Check if two positions are adjacent (Manhattan distance of 1)."""
    return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1]) == 1


class SpatialIndex:
    """Spatial hash grid for O(1) lookup of nearby items"""
    
    def __init__(self, cell_size: int = 5):
        self.cell_size = cell_size
        self.grid: dict[tuple, list] = {}
    
    def _get_cell(self, pos: tuple) -> tuple:
        """Convert position to grid cell"""
        return (pos[0] // self.cell_size, pos[1] // self.cell_size)
    
    def clear(self):
        """Clear all items from the index"""
        self.grid.clear()
    
    def add_item(self, item):
        """Add an item to the spatial index"""
        cell = self._get_cell(item.position)
        if cell not in self.grid:
            self.grid[cell] = []
        self.grid[cell].append(item)
    
    def get_nearby_items(self, pos: tuple, radius_cells: int = 1) -> list:
        """Get all items in cells within radius_cells of the given position"""
        cell = self._get_cell(pos)
        items = []
        for dx in range(-radius_cells, radius_cells + 1):
            for dy in range(-radius_cells, radius_cells + 1):
                neighbor_cell = (cell[0] + dx, cell[1] + dy)
                if neighbor_cell in self.grid:
                    items.extend(self.grid[neighbor_cell])
        return items
    
    def get_items_in_radius(self, pos: tuple, radius: int) -> list:
        """Get all items within Manhattan distance radius of position"""
        nearby = self.get_nearby_items(pos, max(1, radius // self.cell_size))
        return [item for item in nearby 
                if abs(item.position[0] - pos[0]) + abs(item.position[1] - pos[1]) <= radius]
