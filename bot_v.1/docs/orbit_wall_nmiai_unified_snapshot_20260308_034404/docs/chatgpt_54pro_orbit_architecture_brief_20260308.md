# Grocery Bot Architecture Brief for ChatGPT 5.4 Pro

Use this brief together with the current implementation file:

- `C:\Users\Exempel\Desktop\Seed\bot_v.1\scripts\run_nmiai_grocery_bot.py`
- `C:\Users\Exempel\Desktop\Seed\bot_v.1\orders.py`
- `C:\Users\Exempel\Desktop\Seed\bot_v.1\GROCERY_BOT_PROTOCOL.md`
- `C:\Users\Exempel\Desktop\Seed\bot_v.1\docs\chatgpt_54pro_orbit_log_pack_20260308.md`

## Problem Context

This is the NMiAI 2026 Grocery Bot challenge. The current focus is the `expert` map:

- Grid: `28 x 18`
- Bots: `10`
- Max rounds: `300`
- Drop-off: usually `D0 = [1, 16]`
- Current strategy anchor: a ring around the central wall defined from shelf IDs `72, 73, 112, 113`
- The derived ring has `20` walkable cells around that wall

The current hypothesis is:

- Keep bots circulating around a high-value ring so each bot is frequently adjacent to shelf cells
- Pick products from that ring
- Deliver to drop-off when cargo is valuable for the active order
- Also keep the next order (`preview`) warm, but without sacrificing the active order

The current code partially achieves this, but it is still heuristic-heavy and fragile.

## How the Current System Works

The current logic lives in `WallOrbitEngine` inside `run_nmiai_grocery_bot.py`.

### Ring / formation logic

- The ring is derived from shelf IDs `72, 73, 112, 113`
- Bots in orbit are assigned evenly spread slots on the ring
- Slot assignment tries to preserve clockwise order to reduce crossing
- When formation is considered ready, phase advances and all slots rotate clockwise
- Orbit movement is intended to be one-way / clockwise on the ring

### Order logic

- `compute_needed_items(state)` computes remaining active-order item types after subtracting already delivered items and items already carried by bots
- `compute_preview_items(state)` computes remaining preview-order item types, subtracting what should auto-deliver after active completion
- `should_prefetch_preview(state)` allows preview pickup only when active demand can still fit in free inventory capacity

### Pickup logic

- Pickup is currently opportunistic and local
- A bot can only pick if it is already adjacent to the item
- Priority is:
  1. active-order need
  2. preview-order need

### Delivery logic

- Only a capped subset of bots becomes delivery bots
- Delivery selection is based on active-order matching inventory, inventory size, and distance to drop-off
- Delivery routes are planned through a top or bottom lane
- If a delivery bot is already on the ring, it should continue clockwise until its planned exit gate, then leave the ring

### Collision / safety logic

- One-tick moves are collision-resolved
- There is an extra post-pass guard to reject illegal on-ring motion and to reduce ring compression
- Telemetry and decision traces are recorded for live runs

## What the System Should Really Do

The ring is not the goal. It is only a positioning mechanism.

The actual goal is to maximize score under `300` rounds, with architecture that remains stable when logic gets more complex.

The intended behavior is:

1. Bots should use the ring as a high-throughput staging mechanism around the most useful shelves.
2. Idle or underutilized bots should keep orbiting so they are always close to likely pickups.
3. Bots should pick items for `active` and `preview` simultaneously, but `active` must always have priority in allocation, delivery, and inventory use.
4. Delivery should be coordinated, not ad hoc:
   - only enough bots should leave orbit to keep throughput high
   - others should remain in orbit to preserve pickup coverage
   - after delivery, bots should rejoin the orbit cleanly
5. The system should think in terms of throughput and flow:
   - shelf access
   - ring occupancy
   - delivery lanes
   - drop-off congestion
   - active/preview inventory conversion
6. The architecture must explicitly prevent known failure modes rather than patch them one by one after the fact.

## Known Failure Modes to Design Around

These are real problems observed in live runs and should be treated as first-class architectural constraints:

- Ring formation collapses when bots leave for delivery and later re-enter
- Local slot / phase heuristics are very brittle; small changes create large regressions
- Too many delivery bots reduce shelf coverage and destroy the ring
- Too few delivery bots preserve the ring but starve scoring
- Purely local pickup logic misses globally better assignments
- Preview prefetch can accidentally reduce active throughput
- One-step path heuristics create oscillations and unstable lane behavior
- Collision handling and spacing handling are currently layered on top of the policy rather than designed into it
- The system often gets a stable score around `12`, which is evidence that logistics is partially working but throughput is far from optimal

## What I Need From You

I do not want another pile of local heuristics.

I want a better architecture for this problem. Please propose a design that includes:

1. A clear decomposition of responsibilities
   - global planner
   - order allocator
   - orbit manager
   - delivery manager
   - path / reservation layer
   - telemetry / diagnostics layer
2. State models and invariants
   - what each bot state is
   - what the orbit guarantees
   - when a bot is allowed to leave / re-enter
   - how active vs preview is represented without causing conflicts
3. Decision pipeline per round
4. How assignments should be made
   - whether ring cells, shelf adjacencies, items, and deliveries should all be modeled together
   - how to optimize throughput rather than just nearest actions
5. Pathing / traffic design
   - ring as one-way lane
   - entry / exit gates
   - delivery lane design
   - drop-off approach strategy
   - re-entry policy
6. Failure-resistant formation logic
7. A migration plan from the current `WallOrbitEngine` to the improved architecture
8. Suggested telemetry that would make the new architecture debuggable

## Ready-to-Send Prompt

```text
I am working on an NMiAI 2026 Grocery Bot for the expert map. I want you to help me design a stronger architecture, not just tweak heuristics.

Please read the attached files first:
- run_nmiai_grocery_bot.py
- orders.py
- GROCERY_BOT_PROTOCOL.md
- chatgpt_54pro_orbit_log_pack_20260308.md

Current idea:
- 10 bots use a 20-cell ring around a central wall so they stay near useful shelves
- they pick products from that ring
- some bots deliver to drop-off
- active order has priority, preview order should be prepared without hurting active throughput

But the current implementation is fragile. Small logic changes cause formation collapse, congestion, or reduced throughput. I need a better architecture that explicitly handles:
- ring formation stability
- delivery coordination
- active vs preview prioritization
- re-entry after delivery
- traffic rules / one-way flow
- collision and reservation logic
- maximizing score, not just preserving the ring

Important observed failure modes:
- ring spacing collapses when delivery / return logic interacts with slot logic
- too many delivery bots destroy ring coverage
- too few delivery bots preserve formation but score stays low
- pickup logic is too local and opportunistic
- preview logic can interfere with active logic
- local motion heuristics create oscillation and regressions

What I want from you:
1. Propose a clean architecture with components, data flow, and invariants.
2. Explain how you would model orbit management, pickup allocation, delivery allocation, and path reservations together.
3. Describe the per-round decision pipeline.
4. Describe how to guarantee stable one-way ring behavior even when bots leave and re-enter.
5. Explain how active and preview orders should be jointly planned without allowing preview to damage active throughput.
6. Suggest concrete telemetry / debug signals.
7. Give a staged migration plan from the current code.
8. If helpful, propose core data structures and pseudocode.

Please optimize for robustness and throughput on the expert map under 300 rounds, not for minimal code changes.
```
