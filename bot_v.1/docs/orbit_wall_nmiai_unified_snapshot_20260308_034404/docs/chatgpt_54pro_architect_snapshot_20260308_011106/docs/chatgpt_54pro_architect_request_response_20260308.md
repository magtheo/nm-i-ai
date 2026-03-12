# Architect Request Response Pack

This file answers the architect's request using only data that is directly available from the codebase, live artifacts, and explicit user statements.

Explicit owner authorization available in this thread:

- the current code is not secret
- the full current code may be shared with the external architecture model

Official protocol source requested by the user:

- MCP endpoint: `https://mcp-docs.ainm.no/mcp`
- Local canonical snapshot derived from that source: [GROCERY_BOT_PROTOCOL.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/GROCERY_BOT_PROTOCOL.md)

Note:

- Direct fetch of the MCP endpoint was attempted on `2026-03-08` and did not return usable content from this environment
- Therefore the local protocol snapshot is the factual protocol reference currently available in this workspace

## 1. Desired Output Format

Not derivable from code or artifacts.

This requires a product / owner decision.

No factual answer is available yet for:

- architecture spec over current code
- module refactor plan
- patch plan by file
- pseudoclasses / interfaces
- ready code changes

## 2. Current Decision Core

The current one-tick decision path is centered in:

- [run_nmiai_grocery_bot.py#L148](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L148) `WallOrbitEngine`
- [run_nmiai_grocery_bot.py#L470](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L470) active / preview need computation, delivery selection, orbit partition
- [run_nmiai_grocery_bot.py#L561](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L561) opportunistic pickup selection
- [run_nmiai_grocery_bot.py#L596](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L596) delivery routing and delivery action generation
- [run_nmiai_grocery_bot.py#L703](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L703) orbit movement and slot-following
- [run_nmiai_grocery_bot.py#L772](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L772) conflict resolution + final action formation

Supporting decision files:

- [orders.py#L7](/C:/Users/Exempel/Desktop/Seed/bot_v.1/orders.py#L7) `compute_needed_items`
- [orders.py#L34](/C:/Users/Exempel/Desktop/Seed/bot_v.1/orders.py#L34) `compute_preview_items`
- [orders.py#L74](/C:/Users/Exempel/Desktop/Seed/bot_v.1/orders.py#L74) `should_prefetch_preview`
- [orders.py#L97](/C:/Users/Exempel/Desktop/Seed/bot_v.1/orders.py#L97) `items_matching_active`
- [collision.py#L17](/C:/Users/Exempel/Desktop/Seed/bot_v.1/collision.py#L17) reservation-based collision resolution
- [collision.py#L84](/C:/Users/Exempel/Desktop/Seed/bot_v.1/collision.py#L84) `action_for_move`

If the architect wants to treat current code as canonical, the factual instruction is:

`Design directly on top of run_nmiai_grocery_bot.py + orders.py + collision.py + GROCERY_BOT_PROTOCOL.md.`

## 3. Exact Expert Map Topology

Exact exported map file:

- [expert_map_seed0_export_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_map_seed0_export_20260308.json)

This export includes:

- full blocked cells
- full walkable cells
- drop-off
- spawn positions
- all item / shelf coordinates and item types
- current derived ring
- ring index mapping
- ring-adjacent pickup cells
- adjacent items for each ring cell
- current heuristic gates
- current heuristic delivery corridors

Factual map notes from the export:

- difficulty: `expert`
- seed: `0`
- grid: `28 x 18`
- drop-off: `[1, 16]`
- spawn: `[26, 16]`
- current ring size: `20`

## 4. Live Metrics Across Many Runs

Aggregated summary across complete recorded expert runs:

- [expert_live_runs_summary_20260308.csv](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_live_runs_summary_20260308.csv)
- [expert_live_runs_summary_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_live_runs_summary_20260308.json)

Export count:

- `28` complete expert runs

Included metrics per run:

- final score
- delivered items
- completed orders
- idle steps
- collisions avoided
- avg decision time
- avg orbit occupancy
- avg deliverers
- delivery rounds
- hybrid rounds
- first delivery round
- first completed order round
- active picks
- preview picks
- average orbit min-gap
- gap histogram (`gap1`, `gap2`, `gap3plus`)
- collapsed orbit rounds proxy
- on-ring clockwise / counterclockwise / stay motion counts
- spacing-guard waits
- collision waits
- no-assignment waits
- drop-off queue proximity proxy
- config hash

Facts that are NOT recorded directly:

- exact code version for each run
- direct return-to-ring failure counter

For those fields:

- `code_version_recorded = false`
- `return_to_ring_failures_direct = NA_not_recorded`

## 5. 3-5 Raw Replays / Round Traces

Selected manifest with raw trace paths:

- [expert_selected_replays_manifest_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_selected_replays_manifest_20260308.json)
- [selected_enriched_traces_20260308/manifest.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/selected_enriched_traces_20260308/manifest.json)

The selected cases are:

- `run_20260308_001202` current relatively stable hybrid baseline
- `run_20260307_234824` formation-oriented baseline
- `run_20260307_235618` overactive hybrid degradation
- `run_20260308_000913` ring collapse regression
- `run_20260307_233817` high rotation, low spacing quality

Each manifest entry points to:

- `decision_trace.jsonl`
- `order_trace.json`
- `result.json`
- `ui_replay.html` when available
- generated enriched round trace JSON

## 6. Current Internal Runtime State

Persistent engine fields in current implementation:

- [run_nmiai_grocery_bot.py#L161](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L161) `_loop_points`
- [run_nmiai_grocery_bot.py#L162](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L162) `_orbit_phase`
- [run_nmiai_grocery_bot.py#L163](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L163) `_loop_spacing`
- [run_nmiai_grocery_bot.py#L164](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L164) `_slot_by_bot`
- [run_nmiai_grocery_bot.py#L165](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L165) `_delivery_mode`
- [run_nmiai_grocery_bot.py#L166](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L166) `_deliver_route_by_bot`
- [run_nmiai_grocery_bot.py#L167](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L167) `_phase_hold_ticks`

Per-round runtime / debug state:

- [run_nmiai_grocery_bot.py#L171](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L171) `last_round_telemetry`
- [run_nmiai_grocery_bot.py#L172](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L172) `last_assignment_snapshot`
- [run_nmiai_grocery_bot.py#L173](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L173) `last_pre_collision_actions`
- [run_nmiai_grocery_bot.py#L174](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L174) `_round_wait_reason_by_bot`

Ephemeral per-bot fields built during one tick:

- [run_nmiai_grocery_bot.py#L488](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L488) `action_by_bot`
- [run_nmiai_grocery_bot.py#L489](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L489) `movement_target_by_bot`
- [run_nmiai_grocery_bot.py#L490](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L490) `target_type_by_bot`
- [run_nmiai_grocery_bot.py#L491](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py#L491) `slot_idx_by_bot`

Observed per-bot conceptual state from traces:

- `target_type`: `orbit_wall` / `pick_item` / `deliver`
- `slot_idx`
- `phase`
- `pickup_pos`
- `drop_off`
- `source`
- `item_type`
- `movement_target`
- wait reason when blocked

No explicit persisted fields currently exist for:

- `returning_to_ring`
- `bot_role` beyond delivery vs orbit split
- token ownership
- admission state
- mission object

## 7. Refactor Constraints

Only the following are factual:

- current code already stores additional state between rounds inside `WallOrbitEngine`
- the current project is not immutable and already contains custom experimental code paths
- no explicit backward compatibility contract is recorded in the repository

The following constraints are NOT specified in the repo or artifacts:

- whether file structure may be changed
- whether new modules may be added
- whether helper backward compatibility must be preserved
- whether a migration path without major rewrite is required
- whether a new planner / reservation layer is allowed
- whether the ring must remain the architectural base
- whether there is a tighter compute budget than the protocol timeout

Those still require owner input.

## 8. Primary KPI

This is the strongest factual statement available from the user request:

- primary KPI: maximize score

Also explicitly stated by the user:

- the architecture should remain robust as mechanics get more complex
- current recurring problems should be anticipated at design time

So the factual priority ordering appears to be:

1. maximum score
2. robustness against architectural regressions

## 9. What Has Already Been Tried / Observed

Only a partial factual list is available. There is no exhaustive experiment ledger with code-version labeling.

Observed from the current code and live experiment history in this workspace:

- uncapped or weakly constrained delivery behavior was tried and often degraded ring stability
- capped delivery queue behavior exists in current code
- fixed one-way clockwise ring behavior was introduced
- slot spacing target around `2` on a `20`-cell ring for `10` bots was used
- slot assignment preserving clockwise order was introduced
- preview pickup gating via `should_prefetch_preview(state)` exists
- preview-only cargo is currently kept on ring instead of being delivered directly
- post-collision spacing guards were introduced

Observed negative outcomes from runs:

- more hybrid activity can still produce the same final score while hurting ring quality
- ring collapse and prolonged `gap=1` states were repeatedly observed
- local heuristic changes caused large behavioral regressions
- several early runs ended at score `0`
- many later runs converged around score `12`, suggesting partial logistics success but poor throughput ceiling

## 10. Target Operating Mode

Not specified as a hard constraint by recorded data.

The following is factual about current behavior, but not a declared hard target:

- current system often behaves like `8-9` orbit bots with `1-2` deliverers in more stable runs

There is no explicit owner-approved hard target yet for:

- exact orbit count
- exact deliverer count
- designated preview bots
- search budget constraints such as “no full-map A*”
- migration stage count
- file-touch budget

## Minimal Ready Package for the Architect

The currently assembled factual package is:

- [run_nmiai_grocery_bot.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py)
- [orders.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/orders.py)
- [collision.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/collision.py)
- [GROCERY_BOT_PROTOCOL.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/GROCERY_BOT_PROTOCOL.md)
- [expert_map_seed0_export_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_map_seed0_export_20260308.json)
- [expert_live_runs_summary_20260308.csv](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_live_runs_summary_20260308.csv)
- [expert_live_runs_summary_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_live_runs_summary_20260308.json)
- [expert_selected_replays_manifest_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_selected_replays_manifest_20260308.json)
- [chatgpt_54pro_orbit_architecture_brief_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_orbit_architecture_brief_20260308.md)
- [chatgpt_54pro_orbit_log_pack_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_orbit_log_pack_20260308.md)

## Remaining Open Questions That Need User Input

These are still unresolved and cannot be derived from the repository:

- exact desired output format from the architect
- refactor constraints
- exact allowed scope of rewrite
- whether ring-first is mandatory
- target operating mode as a hard constraint
