# Architect Bundle Manifest

This is the recommended attachment set for the external architecture model.

## 1. Core Decision Code

Attach these full files as the canonical current implementation:

- [run_nmiai_grocery_bot.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/scripts/run_nmiai_grocery_bot.py)
- [orders.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/orders.py)
- [collision.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/collision.py)
- [pathfinding.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/pathfinding.py)
- [grid.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/grid.py)
- [models.py](/C:/Users/Exempel/Desktop/Seed/bot_v.1/models.py)
- [GROCERY_BOT_PROTOCOL.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/GROCERY_BOT_PROTOCOL.md)

Why these files:

- `run_nmiai_grocery_bot.py` contains the full current tick-to-tick policy for `WallOrbitEngine`
- `orders.py` contains active / preview planning primitives
- `collision.py` contains actual move conflict resolution
- `pathfinding.py` and `grid.py` contain movement primitives used by the policy
- `models.py` contains the message and action schemas
- `GROCERY_BOT_PROTOCOL.md` is the local canonical protocol snapshot

## 2. Architecture Context

Attach:

- [chatgpt_54pro_orbit_architecture_brief_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_orbit_architecture_brief_20260308.md)
- [chatgpt_54pro_architect_request_response_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_architect_request_response_20260308.md)

Why:

- the brief explains the current hypothesis, target behavior, and architectural goal
- the request-response pack maps directly to the architect's 10 requested categories

## 3. Map / Environment Data

Attach:

- [expert_map_seed0_export_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_map_seed0_export_20260308.json)

This file contains:

- full blocked / walkable topology
- drop-off
- spawn
- item coordinates and types
- current derived ring
- ring-adjacent pickup cells
- current heuristic gates and corridors

## 4. Run Metrics

Attach:

- [expert_live_runs_summary_20260308.csv](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_live_runs_summary_20260308.csv)
- [expert_live_runs_summary_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_live_runs_summary_20260308.json)

These files provide the cross-run comparison layer over complete expert live artifacts.

## 5. Raw Replay / Trace Cases

Attach:

- [expert_selected_replays_manifest_20260308.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/expert_selected_replays_manifest_20260308.json)
- [chatgpt_54pro_orbit_log_pack_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_orbit_log_pack_20260308.md)
- [selected_enriched_traces_20260308/manifest.json](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/selected_enriched_traces_20260308/manifest.json)

Then attach the raw artifact files for the 5 selected runs listed in the manifest:

- `decision_trace.jsonl`
- `order_trace.json`
- `result.json`
- `ui_replay.html` when available

Also attach the enriched per-round exports generated from those runs:

- `run_20260308_001202_enriched_round_trace.json`
- `run_20260307_234824_enriched_round_trace.json`
- `run_20260307_235618_enriched_round_trace.json`
- `run_20260308_000913_enriched_round_trace.json`
- `run_20260307_233817_enriched_round_trace.json`

The selected traces already cover:

- relatively stable hybrid baseline
- formation-oriented baseline
- overactive hybrid degradation
- ring collapse regression
- high-rotation / low-spacing-quality case

## 6. Trace Field Availability

The selected `decision_trace.jsonl` files already contain, per round:

- full world state
- all bot positions
- bot inventories
- active order index
- chosen actions per bot
- telemetry
- assignment snapshot
- pre-collision action snapshot
- wait reasons

The selected `order_trace.json` files contain:

- active order
- preview order
- score progression
- active order transitions

The generated enriched round traces add, per round:

- merged active / preview order snapshot
- per-bot inventory before / after
- per-bot chosen action
- per-bot target type
- per-bot slot index / phase
- per-bot movement target
- per-bot wait reason
- picked item IDs this round
- drop-off action bots this round
- active delivered delta into next round

## 7. Important Known Gaps

These are still not directly recorded:

- exact code version per historical run
- direct return-to-ring failure counter
- owner-selected refactor constraints
- owner-selected desired output format from the architect

Those gaps are already explicitly called out in:

- [chatgpt_54pro_architect_request_response_20260308.md](/C:/Users/Exempel/Desktop/Seed/bot_v.1/docs/chatgpt_54pro_architect_request_response_20260308.md)
