# Score Optimization Guide

## The Math

**Nightmare maximum theoretical score:**
- 500 rounds
- 20 bots
- Max 3 items per bot = 60 items in transit
- If every bot delivers 3 items every ~10 rounds = 150 deliveries × 500/10 = 7500 items

**Realistic maximum: ~2000-3000 points**
- Top score: 1259 (Maked)
- Good score: 800-1000
- Our estimate: 400-900 (untested)

## Score Breakdown

| Source | Points | Strategy |
|--------|--------|----------|
| Item delivery | +1 each | Maximize items delivered |
| Order completion | +5 each | Complete orders fast |
| **Key insight** | | Order bonus = 5 items worth |

## Optimization Priorities

### 1. Route Bundling (HIGHEST IMPACT)

**Current behavior:**
```
Bot picks item A → delivers → picks item B → delivers
= 2 items in ~20 rounds
```

**Optimized behavior:**
```
Bot picks A → picks B → picks C → delivers all 3
= 3 items in ~15 rounds (50% more efficient)
```

**Implementation:**
- Score item clusters
- Plan multi-pick routes
- Consider drop-off timing

### 2. Order Completion Priority (HIGH IMPACT)

**Current:** We prioritize last item (+5 bonus)

**Improvement:**
- Aggressively complete orders when close
- Pre-position bots for next order
- Unlock preview order faster = more time for its items

### 3. Congestion Avoidance (MEDIUM IMPACT)

**Current:** 4-step lookahead

**Improvement:**
- Identify bottlenecks before they happen
- Spread bots across different aisles
- Stagger drop-off arrivals

### 4. Predictive Positioning (MEDIUM IMPACT)

**Current:** React to current state

**Improvement:**
- Predict where items will be needed
- Position bots near likely pickup locations
- Reduce travel time

### 5. Replay Learning (HIGH IMPACT)

**Daily determinism = same map every day**

```python
# Run 1: Score 400
# Run 2: Score 420 (learned better paths)
# Run 10: Score 600 (tuned heuristics)
# Run 50: Score 900+ (near-optimal)
```

**Implementation:**
- Log all games
- Analyze bottlenecks
- Tune weights based on results

## Expected Score Improvements

| Optimization | Easy | Medium | Hard | Expert | Nightmare |
|--------------|------|--------|------|--------|-----------|
| Current | 120 | 180 | 200 | 250 | 500 |
| + Route bundling | 135 | 210 | 260 | 340 | 750 |
| + Order priority | 140 | 220 | 280 | 380 | 850 |
| + Replay tuning | 145 | 230 | 300 | 420 | 1000 |
| **Target** | **150** | **240** | **320** | **450** | **1200** |

## Quick Wins (Implement Now)

1. **Increase order completion weight**
   - Change `WEIGHT_ORDER_COMPLETION` from 5.0 to 10.0
   
2. **Reduce preview priority**
   - Change `WEIGHT_PREVIEW_ITEM` from 0.5 to 0.3
   
3. **More aggressive drop-off**
   - Drop off when 2+ active items in inventory

## Long-term Wins

1. **Route bundling** (Phase 2)
2. **Replay optimization** (Phase 5)
3. **Heuristic tuning** (run 50+ games per difficulty)
