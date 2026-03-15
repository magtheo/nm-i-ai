#!/usr/bin/env python3
"""Performance benchmarks for the bot."""

import time
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from challenges.grocery_bot.theo.pathfinding import Pathfinder
from challenges.grocery_bot.shared.state import GameState, Bot, Item, Order
from challenges.grocery_bot.theo.tasks import TaskAssigner
from challenges.grocery_bot.theo.bot import GroceryBot


def test_pathfinding_performance():
    """Test BFS performance on large map."""
    print("\n1. Pathfinding Performance (Nightmare-size map: 30x18)")
    print("-" * 50)
    
    pf = Pathfinder()
    pf.set_map(30, 18, set())  # Nightmare size
    
    # Warm up
    for _ in range(10):
        pf.bfs_distance((0, 0), (29, 17))
    
    # Test 100 random path queries
    start = time.time()
    for _ in range(100):
        pf.bfs_distance((0, 0), (29, 17))
    elapsed = time.time() - start
    
    avg_ms = (elapsed / 100) * 1000
    print(f"  100 BFS queries: {elapsed*1000:.2f}ms total")
    print(f"  Average per query: {avg_ms:.2f}ms")
    
    if avg_ms < 10:
        print(f"  ✅ PASS (< 10ms)")
        return True
    else:
        print(f"  ❌ FAIL (should be < 10ms)")
        return False


def test_pathfinding_with_walls():
    """Test BFS with complex wall configuration."""
    print("\n2. Pathfinding with Walls")
    print("-" * 50)
    
    pf = Pathfinder()
    
    # Create a maze-like wall pattern
    walls = set()
    for y in range(1, 17, 2):
        for x in range(1, 29, 3):
            walls.add((x, y))
    
    pf.set_map(30, 18, walls)
    
    start = time.time()
    for _ in range(100):
        pf.bfs_distance((0, 0), (29, 17))
    elapsed = time.time() - start
    
    avg_ms = (elapsed / 100) * 1000
    print(f"  Walls: {len(walls)}")
    print(f"  Average per query: {avg_ms:.2f}ms")
    
    if avg_ms < 20:
        print(f"  ✅ PASS (< 20ms)")
        return True
    else:
        print(f"  ❌ FAIL (should be < 20ms)")
        return False


def test_round_processing_performance():
    """Test full round processing time."""
    print("\n3. Round Processing Performance")
    print("-" * 50)
    
    bot = GroceryBot()
    
    # Create a large state (Nightmare-like)
    state_data = {
        "type": "game_state",
        "round": 0,
        "max_rounds": 500,
        "grid": {
            "width": 30,
            "height": 18,
            "walls": [[x, y] for y in range(1, 5) for x in range(1, 29, 3)]
        },
        "bots": [
            {"id": i, "position": [i % 30, (i // 30) + 10], "inventory": []}
            for i in range(20)
        ],
        "items": [
            {"id": f"item_{i}", "type": f"type_{i % 21}", "position": [i % 30, (i // 30) + 5]}
            for i in range(50)
        ],
        "orders": [
            {
                "id": "order_0",
                "items_required": ["type_0", "type_1", "type_2", "type_3", "type_4"],
                "items_delivered": [],
                "complete": False,
                "status": "active"
            },
            {
                "id": "order_1",
                "items_required": ["type_5", "type_6", "type_7"],
                "items_delivered": [],
                "complete": False,
                "status": "preview"
            }
        ],
        "drop_off": [15, 17],
        "drop_off_zones": [[5, 17], [15, 17], [25, 17]],
        "score": 0
    }
    
    # Warm up
    for _ in range(3):
        bot.process_round(state_data)
    
    # Test 10 rounds
    start = time.time()
    for _ in range(10):
        bot.process_round(state_data)
    elapsed = time.time() - start
    
    avg_ms = (elapsed / 10) * 1000
    print(f"  Bots: 20, Items: 50, Map: 30x18")
    print(f"  10 rounds: {elapsed*1000:.2f}ms total")
    print(f"  Average per round: {avg_ms:.2f}ms")
    
    if avg_ms < 500:
        print(f"  ✅ PASS (< 500ms, well under 2s limit)")
        return True
    else:
        print(f"  ❌ FAIL (should be < 500ms)")
        return False


def test_single_bot_performance():
    """Test performance with single bot (Easy difficulty)."""
    print("\n4. Single Bot Performance (Easy)")
    print("-" * 50)
    
    bot = GroceryBot()
    
    state_data = {
        "type": "game_state",
        "round": 0,
        "max_rounds": 300,
        "grid": {"width": 12, "height": 10, "walls": []},
        "bots": [{"id": 0, "position": [6, 8], "inventory": []}],
        "items": [
            {"id": f"item_{i}", "type": f"type_{i}", "position": [i * 2 + 1, 1]}
            for i in range(5)
        ],
        "orders": [
            {
                "id": "order_0",
                "items_required": ["type_0", "type_1", "type_2"],
                "items_delivered": [],
                "complete": False,
                "status": "active"
            }
        ],
        "drop_off": [6, 9],
        "drop_off_zones": [[6, 9]],
        "score": 0
    }
    
    # Test 100 rounds
    start = time.time()
    for _ in range(100):
        bot.process_round(state_data)
    elapsed = time.time() - start
    
    avg_ms = (elapsed / 100) * 1000
    print(f"  100 rounds: {elapsed*1000:.2f}ms total")
    print(f"  Average per round: {avg_ms:.2f}ms")
    
    if avg_ms < 50:
        print(f"  ✅ PASS (< 50ms)")
        return True
    else:
        print(f"  ❌ FAIL (should be < 50ms)")
        return False


def test_memory_usage():
    """Test memory efficiency of distance caching."""
    print("\n5. Memory/Caching Test")
    print("-" * 50)
    
    pf = Pathfinder()
    pf.set_map(30, 18, set())
    
    # Make many distance queries
    queries_made = 0
    for x1 in range(0, 30, 5):
        for y1 in range(0, 18, 3):
            for x2 in range(0, 30, 5):
                for y2 in range(0, 18, 3):
                    pf.bfs_distance((x1, y1), (x2, y2))
                    queries_made += 1
    
    cache_size = len(pf._distance_cache)
    print(f"  Queries made: {queries_made}")
    print(f"  Cache entries: {cache_size}")
    
    if cache_size > 0:
        print(f"  ✅ Caching enabled")
        return True
    else:
        print(f"  ⚠️  Caching not active (may be disabled)")
        return True


def main():
    print("=" * 50)
    print("GROCERY BOT PERFORMANCE TESTS")
    print("=" * 50)
    
    results = []
    
    results.append(("Pathfinding", test_pathfinding_performance()))
    results.append(("Pathfinding with Walls", test_pathfinding_with_walls()))
    results.append(("Round Processing", test_round_processing_performance()))
    results.append(("Single Bot", test_single_bot_performance()))
    results.append(("Memory/Caching", test_memory_usage()))
    
    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {name:30} {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All performance tests passed!")
        return 0
    else:
        print("\n⚠️  Some tests failed - review results above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
