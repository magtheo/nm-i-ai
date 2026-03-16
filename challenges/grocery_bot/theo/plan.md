# Fix Plan - Grocery Bot Issues

Created: 2026-03-12
Last updated: 2026-03-12

---

## Wave 1: Dead Code Removal ✅ COMPLETE
**Files:** `pathfinding.py`, `tasks.py`
**Risk:** Low | **Effort:** Low
**Status:** DONE

| Issue | File | Lines | Action | Status |
|-------|------|-------|--------|--------|
| `_cached_obstacles` field | `pathfinding.py` | 32, 45, 87, 380 | Delete all 4 references | ✅ Done |
| `_get_distance_to_item()` method | `tasks.py` | 452-487 | Delete entire method (36 lines) | ✅ Done |
| TaskAssigner `_distance_cache` | `tasks.py` | 67, 75 | Remove field and initialization | ✅ Done |

---

## Wave 2: Quick Fixes (Critical Score Fix) ✅ COMPLETE
**Files:** `config.py`, `tasks.py`, `pathfinding.py`
**Risk:** Low | **Effort:** Low
**Status:** DONE

| Issue | File | Action | Status |
|-------|------|--------|--------|
| **MIN_ITEMS_FOR_DROP_OFF edge case** | `tasks.py` | Add `would_complete` check to allow drop-off when completing order | ✅ Done |
| Unreachable drop-off zones | `tasks.py` | Add warning log when all zones unreachable | ✅ Done |
| `get_next_step()` edge case | `pathfinding.py` | Simplified logic for clarity | ✅ Done |

---

## Wave 3: Configuration Unification ✅ COMPLETE
**Files:** `config.py`, `tasks.py`
**Risk:** Low | **Effort:** Low
**Status:** DONE

| Issue | Action | Status |
|-------|--------|--------|
| Inconsistent bundling bonus | Replace hardcoded `0.15` and `0.05` with `ScoringConfig.bundling_bonus * 0.5` | ✅ Done |
| Aggressive penalty-to-bonus | Reduce `bundling_bonus` from 0.3 to 0.2 | ✅ Done |

---

## Wave 4: Performance Improvements ✅ COMPLETE
**Files:** `tasks.py`, `pathfinding.py`
**Risk:** Medium | **Effort:** Low
**Status:** DONE

| Issue | File | Action | Status |
|-------|------|--------|--------|
| Manhattan distance in scoring | `tasks.py` | Replace with `pathfinder.bfs_distance()` | ✅ Done |
| Inefficient precompute_distances | `pathfinding.py` | Use batched BFS with `get_distances_to_positions()` | ✅ Done |

---

## Wave 5: Complex Refactoring ✅ COMPLETE
**Files:** `pathfinding.py`, `tasks.py`, `utils.py`
**Risk:** Medium | **Effort:** Medium
**Status:** DONE

| Issue | File | Action | Status |
|-------|------|--------|--------|
| Cache invalidation bug | `pathfinding.py` | Full cache clear on obstacle changes | ✅ Done |
| Spatial index rebuilt every frame | `utils.py` | Add `remove_item()` and `update_item_position()` methods | ✅ Done |

---

## Wave 6: Testing & Verification 🔄 IN PROGRESS

- [ ] Run bot and verify score improvement
- [ ] Check logs for proper drop-off behavior
- [ ] Verify no performance regression
- [ ] Confirm bundling is consistent and tunable

---

## Execution Strategy

```
Wave 1 --> Wave 2 --> Wave 3 --> Wave 4 --> Wave 5 --> Wave 6
  ✅         ✅         ✅         ✅         ✅         🔄
```

## Expected Impact

- **Wave 2** should dramatically improve score (fixes order completion) ✅ DONE
- **Wave 4-5** should reduce `tasks` phase from 74% to ~40-50%
- **Wave 1** cleans up codebase, reduces confusion ✅ DONE
