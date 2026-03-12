# Current Focus - Grocery Bot Issues & Improvements

Last updated: 2026-03-12

---

## 🔴 Critical Issues

### 1. Cache Invalidation Bug
**File:** `pathfinding.py` - `invalidate_positions()`

**Problem:** The smart cache invalidation only removes entries where changed positions are start or goal, but not where they're **intermediate path points**.

**Example:**
```
Initial: Position P is blocked (obstacle)
Cache: A → B = 10 (path avoids P)

After remove_obstacle(P):
- Cache entry A → B = 10 is NOT invalidated (neither A nor B is P)
- But actual shortest path might now be 5 (through P)
```

**Impact:** Stale cache entries cause suboptimal routing after obstacles are removed.

**Fix Options:**
1. Full cache invalidation on obstacle changes (simpler)
2. Track which paths go through which positions (complex)
3. Add TTL or periodic cache refresh

---

### 2. MIN_ITEMS_FOR_DROP_OFF Edge Case
**Files:** `config.py`, `tasks.py`

**Problem:** With `MIN_ITEMS_FOR_DROP_OFF = 2`, if only 1 item remains to complete an order, the bot **won't drop off** because:
```python
if active_in_inventory >= MIN_ITEMS_FOR_DROP_OFF:  # 1 >= 2 is False
    zone, score = self._score_move_to_drop_off(...)  # Never executed
```

**Impact:** With `WEIGHT_ORDER_COMPLETION = 10.0`, delaying completion costs significant points.

**Fix:**
```python
# If this drop-off would complete the order, allow it
would_complete = remaining <= active_in_inventory
if active_in_inventory >= MIN_ITEMS_FOR_DROP_OFF or would_complete:
    zone, score = self._score_move_to_drop_off(...)
```

---

### 3. Useless TaskAssigner Cache
**File:** `tasks.py:75`

**Problem:** `self._distance_cache = {}` is cleared every frame in `assign_tasks()`, completely defeating the purpose of caching.

**Impact:** Wasted memory allocation, no performance benefit from caching.

**Fix:** Remove the per-frame cache clear, or remove the cache entirely if not needed.

---

## 🟡 Dead Code to Remove

### 1. `_cached_obstacles` Field
**File:** `pathfinding.py` - lines 32, 45, 87, 380

**Problem:** Set in 3 locations but never read. Complete dead code.

**Fix:** Delete all 4 references.

---

### 2. `_get_distance_to_item()` Method
**File:** `tasks.py` - lines 452-487

**Problem:** Never called anywhere in the codebase.

**Fix:** Delete the entire method (36 lines).

---

### 3. TaskAssigner `_distance_cache` Field
**File:** `tasks.py` - lines 67, 75

**Problem:** Redundant with `Pathfinder._distance_cache`, and cleared every frame anyway.

**Fix:** Remove the field and its initialization.

---

## 🟠 Configuration Issues

### 1. Inconsistent Bundling Bonus Values
**Files:** `tasks.py`, `config.py`

**Problem:** Three different hardcoded values for bundling bonus:
- `ScoringConfig.bundling_bonus = 0.3` (line 27) - used for route bundling
- `0.15` hardcoded in `_score_move_to_item` (line 399)
- `0.05` hardcoded in `_score_pick_active` (line 374)

**Impact:** Changing `ScoringConfig` only affects one code path. Tuning is impossible.

**Fix:** Use `ScoringConfig.bundling_bonus` everywhere:
```python
# In _score_pick_active (line 374)
score *= (1 + ScoringConfig.bundling_bonus * 0.5 * active_in_inventory)

# In _score_move_to_item (line 399)
base_value *= (1 + ScoringConfig.bundling_bonus * 0.5 * active_in_inventory)
```

---

### 2. Aggressive Penalty-to-Bonus Change
**File:** `tasks.py`

**Problem:** The change from 70% penalty to 15% bonus per item is too extreme:

| Items Carried | Old (×0.3) | New (×1.15n) | Change |
|---------------|------------|--------------|--------|
| 1 | 0.30 | 1.15 | +283% |
| 2 | 0.30 | 1.30 | +333% |

**Impact:** Bots may greedily pick up items instead of completing orders.

**Recommendation:** Reduce bundling bonus from 0.3 to 0.15-0.2.

---

## 🔵 Performance Anti-Patterns

### 1. Manhattan Distance Instead of BFS
**File:** `tasks.py:384` - `_score_move_to_item()`

**Problem:** Uses Manhattan distance for scoring, which doesn't account for obstacles.

**Fix:**
```python
distance = self.pathfinder.bfs_distance(bot.position, item.position)
if distance < 0:
    return 0.0  # Unreachable
```

---

### 2. Spatial Index Rebuilt Every Frame
**File:** `tasks.py:76, 125-134`

**Problem:** `_rebuild_spatial_indices()` clears and rebuilds from scratch every frame.

**Fix:** Add incremental update methods to `SpatialIndex`:
```python
def remove_item(self, item):
    cell = self._get_cell(item.position)
    if cell in self.grid and item in self.grid[cell]:
        self.grid[cell].remove(item)

def update_item_position(self, item, old_pos):
    old_cell = self._get_cell(old_pos)
    if old_cell in self.grid and item in self.grid[old_cell]:
        self.grid[old_cell].remove(item)
    self.add_item(item)
```

---

### 3. Inefficient `precompute_distances()`
**File:** `pathfinding.py:369-378`

**Problem:** Does O(n²) BFS calls instead of using batched BFS.

**Fix:**
```python
for pos in positions:
    distances = self.get_distances_to_positions(pos, [p for p in positions if p != pos])
```

---

## 🟢 Minor Issues

### 1. `get_next_step()` Edge Case
**File:** `pathfinding.py:289`

**Problem:** If path has length 1 but start is in parent, returns `parent[start]` which could be goal directly.

---

### 2. Unreachable Drop-off Zones Not Handled
**File:** `tasks.py:494-506`

**Problem:** If all zones return `distance < 0` (unreachable), returns `drop_off_zones[0]` without logging.

**Fix:**
```python
if best_score == float('inf'):
    logger.warning(f"All drop-off zones unreachable from {position}")
    return drop_off_zones[0]
```

---

## 📋 Priority Order for Fixes

| Priority | Issue | Effort |
|----------|-------|--------|
| 1 | Cache invalidation bug | Medium |
| 2 | MIN_ITEMS_FOR_DROP_OFF edge case | Low |
| 3 | Remove dead code (all 3 items) | Low |
| 4 | Unify bundling bonus values | Low |
| 5 | Use BFS distance in scoring | Low |
| 6 | Remove per-frame cache clear | Low |
| 7 | Incremental spatial index | Medium |
| 8 | Optimize precompute_distances | Low |

---

## 🧪 Testing Notes

After fixes, verify:
- [ ] Bots correctly use newly opened paths after obstacle removal
- [ ] Bots drop off when only 1 item needed to complete order
- [ ] No performance regression from cache changes
- [ ] Bundling behavior is consistent and tunable
