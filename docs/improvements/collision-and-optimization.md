# Collision Avoidance & Optimization Analysis

## Current Bug Fix

### Problem
All 3 bots were getting stuck for 200+ rounds. The stuck detection was working but the escape logic never triggered.

### Root Cause
Two disconnected tracking systems:

| Tracking | Location | What it tracks | Used for escape? |
|----------|----------|----------------|------------------|
| `_stuck_counts` | `bot.py` | Position unchanged (actual stuck) | **NO** - never passed anywhere |
| `_wait_counts` | `collision.py` | Consecutive "wait" actions | **YES** - but wrong metric |

Bots issued movement commands (`move_up`, `move_left`) which aren't "wait" actions, so `_wait_counts` stayed at 0 and escape logic never triggered.

### Fix Applied
- Pass `_stuck_counts` from `bot.py` to `collision_avoider.resolve_conflicts()`
- Use actual stuck count instead of wait count to trigger escape behavior

---

## Game Constraints

| Constraint | Value |
|------------|-------|
| Rounds | 300 (500 Nightmare) |
| Response time | 2 seconds per round |
| Bot inventory | 3 items max |
| Collision | Bots block each other |
| Orders | Sequential, infinite, preview visible |
| Scoring | +1 per item, +5 bonus per order |

### Difficulty Levels

| Level | Grid | Bots | Item Types | Order Size | Drop Zones |
|-------|------|------|------------|------------|------------|
| Easy | 12×10 | 1 | 4 | 3-4 | 1 |
| Medium | 16×12 | 3 | 8 | 3-5 | 1 |
| Hard | 22×14 | 5 | 12 | 3-5 | 1 |
| Expert | 28×18 | 10 | 16 | 4-6 | 1 |
| Nightmare | 30×18 | 20 | 21 | 4-6 | 3 |

---

## Current Architecture Problems

| Issue | Description |
|-------|-------------|
| **Reactive, not proactive** | Escape only triggers AFTER being stuck for 3+ rounds |
| **No global coordination** | Each bot decides independently based on priority |
| **Priority cascading** | High-priority bot can block multiple low-priority bots indefinitely |
| **No deadlock prediction** | Can't detect circular deadlocks before they form |
| **Limited lookahead** | Only 4 steps ahead, insufficient for complex maps |
| **No preview optimization** | Bots don't pre-pick items for preview orders |

---

## Recommended Improvements

### 1. Preview Order Pre-picking (HIGH IMPACT, LOW EFFORT)

**Problem**: Bots wait until order is active before collecting items. Preview order is visible but ignored.

**Solution**: When bots have inventory space, pre-pick items from the preview order. When it becomes active, items are already collected.

**Implementation**:
```python
# In task assignment, consider preview order items
if bot_has_space and preview_order_exists:
    assign_preview_items_to_idle_bots()
```

**Expected Impact**: 10-30% more items delivered per game.

---

### 2. Space-Time A* (HIGH IMPACT, MEDIUM EFFORT)

**Problem**: Current collision avoidance is reactive - bots get stuck before finding alternatives.

**Solution**: Plan paths in (x, y, t) space where t = time step. Each bot's planned path becomes an obstacle in space-time for other bots.

```
Bot 0 plans: (3,6,t=0) → (3,5,t=1) → (3,4,t=2) → ...
Bot 1 sees Bot 0's path as obstacles and plans around them
```

**Implementation**:
- Modify A* to include time dimension
- Reserve space-time cells when path is planned
- Lower priority bots avoid reserved cells

**Expected Impact**: Eliminates most deadlock situations.

---

### 3. Optimal Task Assignment (MEDIUM IMPACT, MEDIUM EFFORT)

**Problem**: Current greedy assignment assigns closest bot to closest item, which is suboptimal globally.

**Solution**: Use Hungarian algorithm (or similar) to minimize total travel time across all bots.

**Implementation**:
```python
from scipy.optimize import linear_sum_assignment

# Build cost matrix: bots × items
costs = [[distance(bot, item) for item in items] for bot in bots]
bot_indices, item_indices = linear_sum_assignment(costs)
```

**Expected Impact**: 5-15% efficiency improvement.

---

### 4. Smart Drop-off Selection (MEDIUM IMPACT, LOW EFFORT)

**Problem**: Current code only uses primary `drop_off` zone. Nightmare has 3 zones.

**Solution**: Choose nearest drop-off zone based on bot position.

**Implementation**:
```python
def get_nearest_dropoff(bot_pos, drop_off_zones):
    return min(drop_off_zones, key=lambda z: manhattan_distance(bot_pos, z))
```

**Expected Impact**: Significant for Nightmare difficulty.

---

### 5. Deadlock Prevention (MEDIUM IMPACT, MEDIUM EFFORT)

**Problem**: Circular deadlocks (A waits for B, B waits for C, C waits for A) cause permanent stuck states.

**Solution**: Build wait-for graph and detect cycles. When detected, force lowest-priority bot to take alternative action.

**Implementation**:
```python
def detect_deadlock(wait_graph):
    # DFS cycle detection
    # If cycle found, return bot to force-move
```

**Expected Impact**: Eliminates remaining deadlock situations.

---

## Implementation Priority

| Priority | Improvement | Effort | Impact |
|----------|-------------|--------|--------|
| 1 | Preview pre-picking | Low | High |
| 2 | Space-Time A* | Medium | High |
| 3 | Smart drop-off | Low | Medium |
| 4 | Hungarian assignment | Medium | Medium |
| 5 | Deadlock prevention | Medium | Medium |

---

## Alternative Approaches Considered

### Conflict-Based Search (CBS)
Optimal multi-agent pathfinding. Plans paths independently, then resolves conflicts by re-planning with constraints.

**Pros**: Optimal solutions
**Cons**: O(n!) worst case, may timeout for 20 bots

### ORCA (Optimal Reciprocal Collision Avoidance)
Velocity-based approach used in robotics. Bots cooperatively adjust velocities.

**Pros**: O(n) per agent, very fast
**Cons**: Designed for continuous space, needs adaptation for grid

### Traffic Light System
Designate high-traffic areas and implement right-of-way rules.

**Pros**: Simple to implement
**Cons**: Requires map analysis, less flexible

---

## Conclusion

The current priority-based reservation system is fundamentally reactive. For best scores, especially on harder difficulties with many bots, we need:

1. **Proactive collision avoidance** (Space-Time A*)
2. **Preview order optimization** (pre-pick items)
3. **Global optimization** (Hungarian assignment)

Start with preview pre-picking for quick wins, then implement Space-Time A* for robust multi-bot coordination.
