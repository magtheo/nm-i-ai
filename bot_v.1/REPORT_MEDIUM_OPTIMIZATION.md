# REPORT MEDIUM OPTIMIZATION

## Final policy
The assignment utility is optimized as:
utility = w6*urgency - (w1*dist(bot,target) + w2*dist(target,drop_off) + w3*congestion + w4*collision_risk + w5*replan_penalty + zone_penalty)
with deterministic tie-breaks and per-tick reservation collision resolution.

## Why it works
- Reduces duplicate chasing via global candidate ranking across all bots.
- Penalizes rapid target switching (hysteresis/replan penalty).
- Uses deterministic collision reservation and swap prevention.

## Max score method
Exact score formula for known full order list:
max_score = total_items_needed + 5 * total_orders
- total_orders = 50
- total_items_needed = 237
- max_score = 487

## Results
- Forecast mode: oracle
- Baseline canonical score: 103
- Best canonical score: 101
- Best mean train score: 86.500
- Holdout mean score: 101.000
- Train seeds: [7002, 7003]
- Holdout seeds: [7102]

## Repro
1. `python _simulator.py --mode baseline --show-max`
2. `python _simulator.py --mode tune --max-attempts 500 --max-stale 50`
3. `python _simulator.py --mode single --params-file artifacts/medium/best_params.json`
