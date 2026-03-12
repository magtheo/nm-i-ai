# Grocery Bot Live Log Pack for ChatGPT 5.4 Pro

This file summarizes representative live runs and points to the raw artifacts that can be attached if needed.

Root artifact directory:

- `C:\Users\Exempel\Desktop\Seed\bot_v.1\.seed_artifacts\nmiai\live_screen_expert_orbit_collect_20260307\expert`

## Raw Files Worth Attaching

For each run below, the most useful files are:

- `result.json`
- `decision_trace.jsonl`
- `order_trace.json`
- `state0.json`
- `ui_replay.html`

## Representative Runs

### 1. Current relatively stable hybrid baseline

- Run: `run_20260308_001202`
- Path: `C:\Users\Exempel\Desktop\Seed\bot_v.1\.seed_artifacts\nmiai\live_screen_expert_orbit_collect_20260307\expert\run_20260308_001202`
- Score: `12`
- Items delivered: `7`
- Orders completed: `1`
- Idle steps: `1374`
- Avg decision time: `1.436 ms`
- Picks: `active=4`, `preview=12`
- Deliver rounds: `85`
- Hybrid rounds (delivery + orbit simultaneously): `85`
- Avg delivery bots per round: `0.387`
- On-ring motion: `cw=1314`, `ccw=0`, `stay=1331`
- Orbit min-gap average: `1.953`
- Orbit gap=1 rounds: `13`
- All 10 bots on loop rounds: `232`
- Collision waits: `87`
- Spacing-guard waits: `17`
- Delivery target y counts: `{15: 31, 16: 15, 9: 67}`

Interpretation:

- This is the best current compromise between logistics and formation stability
- Ring mostly stays directional and mostly preserves one-cell spacing
- Throughput is still low: score is only `12` after `300` rounds
- The architecture is stable enough to study, but not strong enough to scale

### 2. Earlier stable formation-oriented variant

- Run: `run_20260307_234824`
- Path: `C:\Users\Exempel\Desktop\Seed\bot_v.1\.seed_artifacts\nmiai\live_screen_expert_orbit_collect_20260307\expert\run_20260307_234824`
- Score: `12`
- Items delivered: `7`
- Orders completed: `1`
- Idle steps: `1379`
- Picks: `active=4`, `preview=11`
- Deliver rounds: `46`
- Hybrid rounds: `46`
- Avg delivery bots per round: `0.253`
- On-ring motion: `cw=1271`, `ccw=4`, `stay=1318`
- Orbit min-gap average: `1.923`
- Orbit gap=1 rounds: `21`
- All 10 bots on loop rounds: `224`
- Collision waits: `61`
- Delivery target y counts: `{16: 60, 9: 12}`

Interpretation:

- Better ring discipline, weaker delivery activity
- Good evidence that protecting formation alone does not solve throughput

### 3. Mixed-flow run with aggressive hybrid behavior

- Run: `run_20260307_235618`
- Path: `C:\Users\Exempel\Desktop\Seed\bot_v.1\.seed_artifacts\nmiai\live_screen_expert_orbit_collect_20260307\expert\run_20260307_235618`
- Score: `12`
- Items delivered: `7`
- Orders completed: `1`
- Idle steps: `669`
- Picks: `active=4`, `preview=19`
- Deliver rounds: `158`
- Hybrid rounds: `158`
- Avg delivery bots per round: `0.857`
- On-ring motion: `cw=644`, `ccw=68`, `stay=883`
- Orbit min-gap average: `1.369`
- Orbit gap=1 rounds: `202`
- All 10 bots on loop rounds: `26`
- Collision waits: `272`
- Delivery target y counts: `{15: 78, 16: 69, 9: 97}`

Interpretation:

- This shows the failure mode where logistics activity increases but ring quality degrades badly
- More hybrid behavior does not automatically mean better score

### 4. Strong regression showing ring collapse

- Run: `run_20260308_000913`
- Path: `C:\Users\Exempel\Desktop\Seed\bot_v.1\.seed_artifacts\nmiai\live_screen_expert_orbit_collect_20260307\expert\run_20260308_000913`
- Score: `12`
- Items delivered: `7`
- Orders completed: `1`
- Idle steps: `1062`
- Picks: `active=5`, `preview=19`
- Deliver rounds: `167`
- Hybrid rounds: `167`
- Avg delivery bots per round: `0.747`
- On-ring motion: `cw=630`, `ccw=38`, `stay=1100`
- Orbit min-gap average: `1.287`
- Orbit gap=1 rounds: `209`
- All 10 bots on loop rounds: `13`
- Collision waits: `169`
- Spacing-guard waits: `632`
- Delivery target y counts: `{15: 12, 16: 31, 9: 174}`

Interpretation:

- Very clear regression
- Ring formation is unstable for long periods
- The policy remains active but the flow quality is poor

### 5. Very active early clockwise orbit but weak spacing quality

- Run: `run_20260307_233817`
- Path: `C:\Users\Exempel\Desktop\Seed\bot_v.1\.seed_artifacts\nmiai\live_screen_expert_orbit_collect_20260307\expert\run_20260307_233817`
- Score: `12`
- Items delivered: `7`
- Orders completed: `1`
- Idle steps: `352`
- Picks: `active=4`, `preview=12`
- Deliver rounds: `49`
- Hybrid rounds: `49`
- Avg delivery bots per round: `0.373`
- On-ring motion: `cw=1889`, `ccw=19`, `stay=424`
- Orbit min-gap average: `1.029`
- Orbit gap=1 rounds: `267`
- All 10 bots on loop rounds: `152`
- Collision waits: `241`
- Delivery target y counts: `{16: 91, 9: 16}`

Interpretation:

- Excellent rotational activity, poor spacing quality
- Good example of why “they are moving a lot” is not enough

## Selected Log Excerpts

### Stable baseline start state

Source:

- `run_20260308_001202/order_trace.json`
- `run_20260308_001202/decision_trace.jsonl`

Round `0`:

- Active order: `["cereal", "butter", "apples", "cheese"]`
- Preview order: `["oats", "butter", "bread", "flour", "onions"]`
- Telemetry:
  - `orbit_loop_size=20`
  - `orbit_spacing_target=2`
  - `orbit_phase=0`
  - `orbit_bots=10`
  - `deliver_bots=0`
  - `blocked_moves=8`
  - `wait_due_to_collision_block=8`

Interpretation:

- Startup congestion is real because all 10 bots spawn at the same location
- Early-round release / formation logic matters a lot

### Active -> preview transition example

Source:

- `run_20260308_001202/order_trace.json`

At round `76`, `active_order_index` changes from `0` to `1`.

New active order:

- `["oats", "butter", "bread", "flour", "onions"]`

New preview order:

- `["onions", "pasta", "bananas", "flour"]`

Interpretation:

- The architecture must explicitly model the active-to-preview handoff
- Remaining inventory can become valuable immediately after transition

### Regression snapshot: ring compression persists

Source:

- `run_20260308_000913/decision_trace.jsonl`

Round `34`:

- `orbit_bots=9`
- `deliver_bots=1`
- `orbit_min_gap=1`
- `orbit_formation_ready=0`
- target types: `deliver=1`, `orbit_wall=9`

Round `40`:

- `orbit_bots=9`
- `deliver_bots=1`
- `orbit_min_gap=1`
- `orbit_formation_ready=0`
- target types: `deliver=1`, `orbit_wall=9`

Round `120`:

- `orbit_bots=9`
- `deliver_bots=1`
- `orbit_min_gap=1`
- `orbit_formation_ready=0`
- target types: `deliver=1`, `orbit_wall=9`

Interpretation:

- This is a key signal that the current slot/phase/orbit policy can get stuck in a bad but persistent local mode

### Mixed-flow snapshot: high hybrid activity, poor quality

Source:

- `run_20260307_235618/decision_trace.jsonl`

Round `120`:

- `orbit_bots=8`
- `deliver_bots=2`
- `orbit_min_gap=1`
- `orbit_formation_ready=0`
- target types: `deliver=2`, `orbit_wall=8`

Interpretation:

- Hybrid behavior exists, but flow quality is still poor
- This is evidence that the architecture needs stronger invariants, not just more hybrid logic

## Telemetry Caveat

There is a current telemetry inconsistency:

- `wait_reason_by_bot` can contain `wait_due_to_spacing_guard`
- but `last_round_telemetry["wait_due_to_spacing_guard"]` is currently written as `0.0`

So any architecture proposal should assume that raw per-bot wait reasons are more trustworthy than that specific aggregate field.

## What These Logs Should Tell You

1. Logistics is partially solved.
2. Formation stability is partially solved.
3. The main bottleneck is architectural cohesion: orbit, pickup, delivery, and preview planning are still too loosely coupled.
4. The present system can achieve a stable low score, but not a high-throughput policy.
