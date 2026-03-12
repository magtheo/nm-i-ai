# Bot Context (for any AI agent)

## Purpose

This file is an operational handoff for any agent entering this repository. Read this first, then run the quick-start commands.

## Quick Start (5 minutes)

1. Run tests:
```bash
pytest -q
```
2. Check current best artifacts:
- `artifacts/medium/best_params.json`
- `artifacts/live_best_medium_current_cycle.json`
3. Reproduce offline current best:
```bash
python _simulator.py --mode single --forecast-mode live --known-orders-mode weighted --params-file artifacts/medium/robust_weighted_best.json
```
4. (Optional) Validate live once:
```bash
python scripts/run_nmiai_grocery_bot.py --difficulty medium --runs 1 --record --record-order-trace --params-file artifacts/medium/robust_weighted_best.json --show-max
```
5. Expert live autotune infrastructure:
```bash
python scripts/autotune_nmiai_expert.py --max-runs 20 --cooldown-sec 4 --plateau-attempts 8 --record
```

## System Architecture

- `endpoint.py`
  - loads `AINM_ACCESS_TOKEN` from `.env` or environment
  - fetches maps and requests game sessions
- `client.py`
  - websocket game loop
  - timeout fallback to `WAIT`
- `decision_engine.py`
  - per-round orchestration
  - handles sticky targets, stall recovery, anti-oscillation, fallback
- `assignment.py`
  - main target selection and utility function
  - supports `greedy` and `auction`
- `collision.py`
  - bot-id-ordered reservation conflict resolution
- `_simulator.py`
  - deterministic local game + autotune + mined dataset usage
- `scripts/run_nmiai_grocery_bot.py`
  - live entrypoint with full CLI and artifact recording

## Critical Constraints

- Never print secrets/tokens.
- Live sessions are expensive; prioritize simulator iterations.
- Keep per-round decisions deterministic and fast.
- After every code change: run tests, then validate with simulation.

## Expert/Extreme (28x18, 10 bots)

- Target mode: `expert` difficulty (`28x18` grid, `10` bots).
- Coordination risks to monitor:
  - corridor contention around narrow lanes and near drop-off
  - deadlocks from mutual blocking chains
  - bot-ID order bias in reservation conflict resolution
- Metrics to track for each attempt:
  - score
  - items per trip (delivery efficiency)
  - collision/blocked rate
  - idle steps
  - p95 decision latency (ms), not only avg/max
- Workflow for expert tuning:
  - heavy simulator search first (`_simulator.py`, mined traces)
  - then minimal live runs for validation/autotune confirmation
  - store expert run artifacts under `.seed_artifacts/nmiai/expert/`
  - write locked live-best to `app/integrations/nmiai_grocery_bot/best_configs/expert.json`
- Never print tokens.

## Current Policy State

Current live-best-config source:
- `artifacts/live_best_medium_current_cycle.json`

Current robust offline best:
- `artifacts/medium/robust_weighted_best.json`

Important observed behavior:
- Same map seed does not guarantee identical live order trace across sessions.
- Therefore, tuning only on a single trace is brittle.

## Simulator Modes You Must Understand

`_simulator.py` supports known-order synthesis modes:
- `known-orders-mode=latest`
  - use latest observed order variant for known indices
  - best for matching the latest collected trace
- `known-orders-mode=weighted`
  - sample known variants by observed frequency
  - best for robust policy search

Recommendation:
- Optimize in `weighted` mode first.
- Sanity-check top candidates in `latest` mode.
- Then run limited live validation.

## Core Metrics to Track

For each candidate:
- score (primary)
- items_delivered
- orders_completed
- idle steps
- blocked/collision stats
- p95 decision ms

Keep records in:
- `artifacts/medium/trials.csv`
- `artifacts/medium/*.json`

## Debugging Playbook

If score collapses:
1. Confirm tests still pass (`pytest -q`).
2. Run baseline simulator (`--mode baseline`) in both `latest` and `weighted`.
3. Compare active/preview order traces from latest live run (`order_trace.json`).
4. Check if candidate overfits one synthesis mode.
5. Roll back to last robust config and mutate around it.

If behavior oscillates:
- inspect `hysteresis_penalty`, `sticky_target_bonus`, `avoid_immediate_backtrack`
- inspect delivery queue pressure (`max_concurrent_deliverers`)

If drop-off jams:
- inspect `force_dropoff_for_full_nonmatching`
- inspect `always_deliver_matching`
- inspect reservation horizon and collision aggressiveness

## Safe Workflow Template

1. Change one mechanic.
2. `pytest -q`.
3. `python _simulator.py --mode single ...`.
4. Short local search (50-200 candidates).
5. Save best candidate JSON under `artifacts/medium/`.
6. One live run with `--params-file`.
7. Update dashboard/tasks/report.

## Files Worth Reading First

1. `decision_engine.py`
2. `assignment.py`
3. `_simulator.py`
4. `scripts/run_nmiai_grocery_bot.py`
5. `tests/test_assignment_policy.py`
6. `tests/test_collision.py`

## Definition of Progress

A change counts as progress only if:
- tests pass
- simulator score improves in target mode(s)
- and live score does not regress in validation run(s)

## Next Agent Checklist

- [ ] Read `DASHBOARD.md`
- [ ] Run `pytest -q`
- [ ] Reproduce current best offline (`weighted` + `latest`)
- [ ] Reproduce one live run with current best params
- [ ] Continue from `TASKS.md` plan
