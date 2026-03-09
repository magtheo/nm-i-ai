# Grocery Bot Testing Plan

**Created:** 2026-03-09
**Last Updated:** 2026-03-09
**Status:** In Progress

---

## Overview

This document outlines the testing strategy for the Grocery Bot. Testing is critical to ensure the bot performs reliably across all 5 difficulty levels and handles edge cases gracefully.

---

## Testing Levels

### Level 1: Unit Tests
Test individual components in isolation.

### Level 2: Integration Tests
Test components working together.

### Level 3: System Tests
Test the full bot against the game server.

### Level 4: Performance Tests
Verify timing and resource constraints.

---

## Level 1: Unit Tests

### 1.1 Pathfinding Tests

**File:** `tests/test_pathfinding.py`

| Test Case | Description | Status |
|-----------|-------------|--------|
| `test_bfs_distance_same_position` | Distance to same position is 0 | ✅ Written |
| `test_bfs_distance_adjacent` | Distance to adjacent is 1 | ✅ Written |
| `test_bfs_distance_with_walls` | Pathfinding around walls | ✅ Written |
| `test_bfs_distance_no_path` | Returns -1 when no path exists | ✅ Written |
| `test_get_next_step` | Returns correct next step | ✅ Written |
| `test_get_neighbors` | Returns valid neighbors only | ✅ Written |
| `test_is_valid` | Validates positions correctly | ✅ Written |

**Additional tests needed:**
- [ ] Test distance caching works correctly
- [ ] Test with large maps (30x18 Nightmare size)
- [ ] Test edge positions (corners, boundaries)
- [ ] Test complex maze-like wall configurations

### 1.2 State Parser Tests

**File:** `tests/test_state.py`

| Test Case | Description | Status |
|-----------|-------------|--------|
| `test_bot_from_dict` | Parse bot from JSON | ✅ Written |
| `test_item_from_dict` | Parse item from JSON | ✅ Written |
| `test_order_items_needed` | Calculate needed items | ✅ Written |
| `test_gamestate_from_dict` | Parse full game state | ✅ Written |
| `test_active_order` | Get active order | ✅ Written |
| `test_preview_order` | Get preview order | ✅ Written |
| `test_is_wall` | Wall detection | ✅ Written |
| `test_get_items_by_type` | Filter items by type | ✅ Written |

**Additional tests needed:**
- [ ] Test empty state (no bots, no items)
- [ ] Test multiple drop-off zones
- [ ] Test order completion detection
- [ ] Test inventory modifications don't affect original

### 1.3 Action Generator Tests

**File:** `tests/test_actions.py`

| Test Case | Description | Status |
|-----------|-------------|--------|
| `test_generate_wait_action` | Generate wait action | ✅ Written |
| `test_generate_drop_off_action` | Generate drop_off action | ✅ Written |
| `test_generate_pick_up_action` | Generate pick_up action | ✅ Written |
| `test_generate_move_action` | Generate move actions | ✅ Written |
| `test_pick_up_respects_inventory_limit` | Inventory limit check | ✅ Written |

**Additional tests needed:**
- [ ] Test move direction correctness (up/down/left/right)
- [ ] Test pick_up when not adjacent (should not pick)
- [ ] Test drop_off when not on zone (should not drop)
- [ ] Test action for bot with no task

### 1.4 Task Assignment Tests

**File:** `tests/test_tasks.py` (needs to be created)

| Test Case | Description | Status |
|-----------|-------------|--------|
| Test bot at drop-off with useful items | Should drop off | ❌ Not written |
| Test bot with full inventory | Should go to drop-off | ❌ Not written |
| Test bot with active items | Should prioritize drop-off | ❌ Not written |
| Test adjacent active item | Should pick up | ❌ Not written |
| Test adjacent preview item | Should pick up | ❌ Not written |
| Test nearest item assignment | Should pick nearest | ❌ Not written |
| Test no items needed | Should wait | ❌ Not written |
| Test multiple bots same item | Should not duplicate assign | ❌ Not written |

### 1.5 Collision Avoidance Tests

**File:** `tests/test_collision.py` (needs to be created)

| Test Case | Description | Status |
|-----------|-------------|--------|
| Test no collision | Both bots move freely | ❌ Not written |
| Test same target tile | Lower priority waits | ❌ Not written |
| Test swap prevention | Prevent A↔B swap | ❌ Not written |
| Test multiple conflicts | Chain resolution | ❌ Not written |
| Test drop-off collision | Bots at same drop-off | ❌ Not written |

---

## Level 2: Integration Tests

### 2.1 Full Round Processing

**File:** `tests/test_integration.py` (needs to be created)

| Test Case | Description | Status |
|-----------|-------------|--------|
| Test single bot simple pickup | Bot picks up and delivers one item | ❌ Not written |
| Test multi-bot coordination | Multiple bots don't collide | ❌ Not written |
| Test order completion | Bot completes an order | ❌ Not written |
| Test preview prefetch | Bot prefetches for preview order | ❌ Not written |
| Test inventory management | Bot fills inventory correctly | ❌ Not written |

### 2.2 Mock Game States

Create mock game states for testing:

```
testing/mock_states/
├── easy_start.json          # Easy difficulty, round 0
├── easy_midgame.json        # Easy difficulty, mid-game
├── medium_start.json        # Medium difficulty, round 0
├── medium_multi_bot.json    # Medium with 3 bots
├── hard_congestion.json     # Hard with potential collisions
├── expert_swarm.json        # Expert with 10 bots
└── nightmare_chaos.json     # Nightmare with 20 bots, 3 drop-offs
```

---

## Level 3: System Tests (Against Game Server)

### 3.1 Connection Tests

| Test Case | Description | Status |
|-----------|-------------|--------|
| Test valid token | Connect successfully | ❌ Not tested |
| Test invalid token | Handle error gracefully | ❌ Not tested |
| Test connection timeout | Handle timeout | ❌ Not tested |
| Test disconnect during game | Handle disconnect | ❌ Not tested |
| Test game_over handling | Exit cleanly | ❌ Not tested |

### 3.2 Difficulty Tests

| Difficulty | Test Case | Status | Best Score |
|------------|-----------|--------|------------|
| Easy | Complete game without errors | ❌ Not tested | - |
| Medium | Complete game without errors | ❌ Not tested | - |
| Hard | Complete game without errors | ❌ Not tested | - |
| Expert | Complete game without errors | ❌ Not tested | - |
| Nightmare | Complete game without errors | ❌ Not tested | - |

### 3.3 Game Server Test Script

**File:** `testing/test_server.py`

```python
#!/usr/bin/env python3
"""Test script to run bot against game server."""

import asyncio
import sys
from src.bot import GroceryBot
from src.connection import GameConnection

async def test_difficulty(token: str, difficulty: str):
    """Test bot against a specific difficulty."""
    print(f"Testing {difficulty} difficulty...")
    
    bot = GroceryBot()
    connection = GameConnection(token)
    
    try:
        result = await connection.play_game(bot)
        score = result.get("score", 0)
        items = result.get("items", 0)
        orders = result.get("orders", 0)
        
        print(f"  Score: {score}")
        print(f"  Items: {items}")
        print(f"  Orders: {orders}")
        
        return {"score": score, "items": items, "orders": orders}
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python testing/test_server.py <token>")
        sys.exit(1)
    
    token = sys.argv[1]
    asyncio.run(test_difficulty(token, "easy"))

if __name__ == "__main__":
    main()
```

---

## Level 4: Performance Tests

### 4.1 Timing Tests

| Test Case | Constraint | Status |
|-----------|------------|--------|
| Single round processing | < 100ms | ❌ Not tested |
| Full game (300 rounds) | < 120s total | ❌ Not tested |
| Nightmare (500 rounds) | < 300s total | ❌ Not tested |
| BFS on large map | < 10ms per query | ❌ Not tested |
| 20 bots decision making | < 500ms per round | ❌ Not tested |

### 4.2 Performance Test Script

**File:** `testing/test_performance.py`

```python
#!/usr/bin/env python3
"""Performance benchmarks for the bot."""

import time
import sys
sys.path.insert(0, '.')

from src.pathfinding import Pathfinder
from src.state import GameState, Bot, Item, Order
from src.tasks import TaskAssigner
from src.bot import GroceryBot

def test_pathfinding_performance():
    """Test BFS performance on large map."""
    pf = Pathfinder()
    pf.set_map(30, 18, set())  # Nightmare size
    
    # Test 100 random path queries
    start = time.time()
    for _ in range(100):
        pf.bfs_distance((0, 0), (29, 17))
    elapsed = time.time() - start
    
    avg_ms = (elapsed / 100) * 1000
    print(f"BFS average: {avg_ms:.2f}ms")
    
    if avg_ms < 10:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL (should be < 10ms)")
    
    return avg_ms < 10

def test_round_processing_performance():
    """Test full round processing time."""
    bot = GroceryBot()
    
    # Create a large state (Nightmare-like)
    state_data = {
        "round": 0,
        "max_rounds": 500,
        "grid": {"width": 30, "height": 18, "walls": []},
        "bots": [{"id": i, "position": [i % 30, i // 30], "inventory": []} for i in range(20)],
        "items": [{"id": f"item_{i}", "type": f"type_{i % 21}", "position": [i % 30, (i // 30) + 5]} for i in range(50)],
        "orders": [
            {"id": "order_0", "items_required": ["type_0", "type_1", "type_2"], "items_delivered": [], "complete": False, "status": "active"},
            {"id": "order_1", "items_required": ["type_3", "type_4"], "items_delivered": [], "complete": False, "status": "preview"}
        ],
        "drop_off": [15, 17],
        "drop_off_zones": [[5, 17], [15, 17], [25, 17]],
        "score": 0
    }
    
    # Test 10 rounds
    start = time.time()
    for _ in range(10):
        bot.process_round(state_data)
    elapsed = time.time() - start
    
    avg_ms = (elapsed / 10) * 1000
    print(f"Round processing average: {avg_ms:.2f}ms")
    
    if avg_ms < 500:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL (should be < 500ms)")
    
    return avg_ms < 500

def main():
    print("=== Performance Tests ===\n")
    
    print("1. Pathfinding Performance")
    test_pathfinding_performance()
    
    print("\n2. Round Processing Performance")
    test_round_processing_performance()

if __name__ == "__main__":
    main()
```

---

## Test Execution Checklist

### Before Each Commit
- [ ] Run all unit tests
- [ ] Verify no import errors
- [ ] Check code formatting

### Before Server Testing
- [ ] All unit tests pass
- [ ] Performance tests pass
- [ ] Integration tests pass (with mock states)

### Daily/Regular
- [ ] Test against all 5 difficulty levels
- [ ] Record scores in leaderboard
- [ ] Check for new edge cases

---

## Bug Tracking Template

When a bug is found, document it:

```markdown
## Bug: [Short Description]

**Date Found:** YYYY-MM-DD
**Severity:** Critical / High / Medium / Low
**Difficulty:** Easy / Medium / Hard / Expert / Nightmare

### Description
[What happened]

### Expected Behavior
[What should have happened]

### Steps to Reproduce
1. [Step 1]
2. [Step 2]
3. [Step 3]

### Root Cause
[Why it happened - fill in after investigation]

### Fix
[How it was fixed - fill in after fix]

### Status
- [ ] Investigated
- [ ] Fixed
- [ ] Tested
- [ ] Verified on server
```

---

## Test Data Directory

```
testing/
├── testing-plan.md          # This file
├── mock_states/             # Mock game states for testing
│   ├── easy_start.json
│   ├── medium_multi_bot.json
│   └── ...
├── test_server.py           # Server test script
├── test_performance.py      # Performance benchmarks
├── bugs/                    # Bug reports
│   └── ...
└── results/                 # Test results
    └── ...
```

---

## Current Priority

1. Create `tests/test_tasks.py` with task assignment tests
2. Create `tests/test_collision.py` with collision avoidance tests
3. Create mock game states in `testing/mock_states/`
4. Run performance tests to verify timing constraints
5. Test against actual game server with real token

---

## Notes

- Tests should be runnable without a game server token
- Mock states should cover edge cases
- Performance tests should warn before exceeding limits
- Keep bug reports for future reference and learning
