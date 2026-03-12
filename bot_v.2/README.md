# bot_v.2 - Grocery Bot Experiment Lab

`bot_v.2` is a reusable experiment workspace extracted from `bot_v.1`.

Primary value in this package:
- fast live/sim iteration loop
- full run artifacts (`result`, `decision_trace`, `order_trace`, round logs)
- post-run failure analysis
- replay rendering
- run-to-run comparison for hypothesis decisions

This workspace is intentionally strategy-agnostic: it preserves the experimentation system first.

## Quick Start

1. Create token file:
- copy `.env.example` to `.env`
- set `AINM_ACCESS_TOKEN`

2. Live run (max logs):
```bash
python scripts/run_nmiai_grocery_bot.py --difficulty expert --runs 1 --max-logs --record
```

3. Review latest run (analyze + render):
```bash
python scripts/review_run.py --difficulty expert
```

4. Compare last 20 runs:
```bash
python scripts/compare_runs.py --difficulty expert --limit 20
```

5. Offline simulation/autotune harness:
```bash
python scripts/run_simulation.py --help
```

## Main Entrypoints

- Live runner: `scripts/run_nmiai_grocery_bot.py`
- Run review (analysis + replay): `scripts/review_run.py`
- Multi-run comparison: `scripts/compare_runs.py`
- Replay renderer: `scripts/render_live_ui.py`
- Orbit-wall log analyzer: `scripts/orbit_wall_log_analyzer.py`
- Expert offline tuner (kept as experiment utility): `scripts/tune_orbit_wall_expert.py`
- Medium autotune (kept as baseline utility): `scripts/autotune_nmiai_medium.py`
- Simulator wrapper: `scripts/run_simulation.py`

## Artifact Layout

Default live artifacts are written under:
- `.seed_artifacts/nmiai/<difficulty>/run_YYYYMMDD_HHMMSS/`

Key files inside each run:
- `result.json`
- `config.json`
- `decision_trace.jsonl`
- `order_trace.json` (if enabled)
- `item_spawn_trace.json` (if enabled)
- `state0.json`
- `game_over.json`
- `round_logs/*.jsonl`
- `ui_replay.html` (after render)
- `analysis_orbit_wall.json` (after review)

## Folder Structure

- `bot/` core runtime + simulator modules
- `scripts/` live/sim/run-analysis tooling
- `tests/` behavior/regression tests for run/analyzer loop
- `configs/` config outputs and templates
- `artifacts/` optional local outputs for offline tools
- `logs/` local logs

## Required Python Packages

```bash
pip install pydantic requests websockets
```

## What To Read Next

- `START_HERE_FOR_AI.md`
- `AI_EXPERIMENT_WORKFLOW.md`
- `FORGE_AUTOMATION.md` (new isolated automated evolution loop)
