# Orbit-Wall Critical Analysis (2026-03-09)

## Live Budget Used (per request)
- 1 validation run:
  - `live_orbit_wall_patch12_single_20260309/expert/run_20260309_023733` -> score `11`
- 3 comparative runs:
  - `live_orbit_wall_patch12_cmp_20260309/expert/run_20260309_023903` -> score `4`
  - `live_orbit_wall_patch12_cmp_20260309/expert/run_20260309_024026` -> score `12`
  - `live_orbit_wall_patch12_cmp_20260309/expert/run_20260309_024149` -> score `4`

## Key Metrics (Patch12 comparative set)
- Mean score: `6.67`
- Min/Max score: `4 / 12`
- Mean idle steps: `810.67`
- Mean `wait_due_to_spacing_guard`: `461.67`
- Mean `deliver_bots_avg`: `0.46`
- Mean `d0_busy_ratio`: `0.0189`

Interpretation: throughput is still collapsing; D0 is mostly idle and spacing wait dominates.

## Structural Breakers

1. **Spacing guard remains the dominant bottleneck**
- Even best comparative run (`score=12`) has `wait_due_to_spacing_guard=481`.
- This is not a tuning issue; it is flow-control architecture pressure.

2. **Demand ledger is still optimistic for active progress**
- Active demand is reduced by `active_committed`, where commitment includes distance-to-drop heuristic, not guaranteed conversion.
- This can suppress active pickup pressure too early.

3. **Delivery pipeline is underfed**
- `deliver_bots_avg` is below 1 in poor runs; D0 busy ratio stays around 1-3.6%.
- Low courier occupancy + low payload per drop keeps score ceiling very low.

4. **Orbit is still acting as strategy, not infrastructure**
- System still over-invests in ring legality and phase maintenance while D0 utilization remains weak.
- Strong ring discipline with weak active completion is a losing mode.

## What Was Changed in This Iteration
- Added stall-aware deep-scout trigger for unresolved active deficits.
- Added analyzer metrics:
  - `d0_busy_ratio`
  - `items_per_drop_action`
  - `dropoff_underutilized` failure reason.
- Refined spacing guard scope to target pure ORBIT mission bots.
- Rejected and rolled back changes:
  - strict-commit ledger variant (caused stable `score=4` attractor),
  - aggressive raw-need detour override (caused collision/idle explosion).

## Bottom Line
- Current architecture remains unstable and throughput-limited.
- The highest-value next step is **not more quota/detour tuning**.
- The next step should be architectural:
  - demote ring to movement backbone only,
  - move active completion pressure into a strict commitment-aware allocator,
  - gate spacing at admission/planning time instead of post-hoc cancellation.
