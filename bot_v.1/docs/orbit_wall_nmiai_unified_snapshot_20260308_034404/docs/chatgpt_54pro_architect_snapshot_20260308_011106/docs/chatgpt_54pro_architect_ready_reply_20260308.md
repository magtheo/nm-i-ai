# Ready Reply to the External Architect

Use the message below as-is and attach the files listed in the bundle manifest.

Primary bundle manifest:

- [chatgpt_54pro_architect_bundle_manifest_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_architect_bundle_manifest_20260308.md)

## Message

```text
Below is the full data pack you requested. Treat the attached code as canonical current implementation. The code is not confidential; you can reason directly over the full files.

1. Output format I want

Please produce all of the following in one response:
- architecture spec over the current code
- module-level refactor plan
- concrete patch plan by file
- pseudoclasses / interfaces
- staged migration plan

Do not generate final code patches yet. I want design + migration specificity first.

2. Current decision core

Please design directly on top of these files as the canonical current decision path:
- run_nmiai_grocery_bot.py
- orders.py
- collision.py
- pathfinding.py
- grid.py
- models.py
- GROCERY_BOT_PROTOCOL.md

The full one-tick decision path is in run_nmiai_grocery_bot.py, centered on WallOrbitEngine.

3. Full expert map

Use this as the exact map export:
- expert_map_seed0_export_20260308.json

It contains:
- all blocked cells
- all walkable cells
- drop-off
- spawn
- all item coordinates and types
- current derived ring
- ring-adjacent pickup cells
- heuristic gates
- heuristic delivery corridors

4. Live metrics across many runs

Use these as the comparison tables:
- expert_live_runs_summary_20260308.csv
- expert_live_runs_summary_20260308.json

They contain 28 complete expert live runs and include:
- final score
- delivered items
- completed orders
- idle steps
- avg orbit occupancy
- avg deliverers
- min gap / gap histogram
- active picks
- preview picks
- queue near drop-off proxy
- rounds spent in collapsed orbit proxy
- first delivery round
- first completed order round
- config hash

Important limitation:
- exact code version per historical run was not recorded
- direct return-to-ring failure count was not recorded

5. 3-5 raw replays / round-by-round traces

Use these selected runs:
- run_20260308_001202 = current relatively stable hybrid baseline
- run_20260307_234824 = formation-oriented baseline
- run_20260307_235618 = overactive hybrid degradation
- run_20260308_000913 = ring collapse regression
- run_20260307_233817 = high rotation, low spacing quality

Attach:
- expert_selected_replays_manifest_20260308.json
- selected_enriched_traces_20260308/manifest.json

For each selected run, I am also attaching:
- decision_trace.jsonl
- order_trace.json
- result.json
- ui_replay.html when available
- enriched_round_trace.json

The enriched round traces include:
- all bot positions
- cargo before / after per bot
- active order
- preview order
- picked item IDs this round
- drop-off action bots this round
- chosen action per bot
- current target_type per bot
- slot_idx / phase per bot
- movement target per bot
- wait reason per bot

6. Current internal runtime state

Current persistent engine state includes:
- _loop_points
- _orbit_phase
- _loop_spacing
- _slot_by_bot
- _delivery_mode
- _deliver_route_by_bot
- _phase_hold_ticks

Current per-round runtime / debug state includes:
- last_round_telemetry
- last_assignment_snapshot
- last_pre_collision_actions
- _round_wait_reason_by_bot

Current per-bot conceptual state visible in traces includes:
- target_type
- slot_idx
- phase
- pickup_pos
- drop_off
- source
- item_type
- movement_target
- wait_reason

There is currently no explicit persisted state for:
- returning_to_ring
- token ownership
- mission object
- admission state

7. Refactor constraints

Use these constraints for the design:
- you may change file structure
- you may add new modules
- backward compatibility of helper functions is not required internally
- I want a migration path, not a single-shot rewrite
- a new planner / reservation layer is allowed
- additional state between rounds is allowed
- the current ring is a strong current hypothesis, but it is not sacred if you can justify a better generalization
- the hard protocol budget is the official game budget; there is no stricter custom limit currently imposed, but the design should remain comfortably within practical per-round latency

8. Primary KPI

Primary KPI:
- maximize score

Secondary KPI:
- reduce catastrophic orbit / logistics collapse
- improve robustness so future logic extensions do not immediately cause regressions

So optimize first for score, but do not propose a brittle design that only improves average score while collapsing unpredictably.

9. What has already been tried / observed

Factual observations from the current code and live runs:
- current code uses a capped delivery-selection regime on expert
- active-first pickup logic exists
- preview gating exists
- one-way clockwise ring logic exists
- slot spacing logic exists
- post-collision spacing guards exist

Observed regressions:
- more hybrid activity can still keep score around 12 while destroying formation quality
- ring compression and long gap=1 periods happen repeatedly
- small heuristic changes caused major regressions
- some early runs ended at score 0
- many later runs plateau near score 12, which suggests partial logistics success but poor throughput ceiling

You should rely on the attached run summary and selected traces rather than assume the current control law is close to optimal.

10. Target operating mode

Use this as the target operating hypothesis, not a hard invariant:
- usually keep about 8-9 bots in orbit
- usually keep 1-2 bots delivering
- preview should be supported opportunistically, not by sacrificing active throughput
- prefer hotspot / structured reservation logic over uncontrolled local heuristics
- give a staged migration plan

Most important request:
I do not want another pile of heuristics. I want you to identify the real bottleneck in the current architecture and propose a system that is structurally more stable under expert-map conditions.
```
