# NMIAI Medium Autotune Report

## What Changed

Updated files:

- `endpoint.py`
  - Added API map lookup + session request logic (`GET /games/maps`, `POST /games/request`).
  - Added `.env` token loader for `AINM_ACCESS_TOKEN` and safe URL redaction.
  - Added retry handling for cooldown/rate-limit (`429`).

- `scripts/run_nmiai_grocery_bot.py`
  - Added live harness CLI with:
    - `--difficulty medium`
    - `--runs N`
    - `--cooldown-sec`
    - `--seed`
    - `--record`
    - `--show-max`
  - Added strategy knobs for tuning:
    - `--lookahead-k`
    - `--active-weight`
    - `--preview-weight`
    - `--dropoff-threshold`
    - `--collision-aggressiveness`
  - Writes run artifacts under:
    - `.seed_artifacts/nmiai/medium/run_YYYYMMDD_HHMMSS/`
    - `config.json`, `result.json`, `state0.json`, `game_over.json`, `log.txt`

- `scripts/autotune_nmiai_medium.py`
  - Added bounded live autotune loop with:
    - baseline run
    - candidate search over lookahead/weights/dropoff/collision/seed
    - stop on exact max / upper-bound+all-orders / plateau / max-runs
    - top-5 ranking output
  - Persists best config to:
    - `app/integrations/nmiai_grocery_bot/best_configs/medium.json`

- `max_score.py`
  - Replaced estimate-only logic with `max_score_for_game(state)` and `OrderTracker` bounds.
  - Supports exact max (if all orders visible) or lower/upper score bounds.

- `assignment.py`
  - Added deterministic multi-bot greedy assignment policy with:
    - active + preview lookahead support
    - explicit utility/dist/item/pickup/bot tie-break
    - duplicate-item chase prevention
    - configurable dropoff threshold behavior
    - per-bot BFS distance map usage for speed

- `collision.py`
  - Added reservation-based collision resolver in bot-id order.
  - Added swap prevention and collision stats.

- `decision_engine.py`
  - Added `DecisionConfig` strategy config object.
  - Wired assignment policy config into per-round decisions.
  - Added collision metrics and optional detour mode for blocked moves.
  - Enforced deterministic item iteration order.

- `pathfinding.py`
  - Added `bfs_distance_map()` for per-round/per-bot distance caching.

- `client.py`
  - Added callbacks for state/actions/game_over capture.
  - Added safe websocket URL logging (redacted token).
  - Improved game_over field handling (`items_delivered`, `orders_completed`).

- `models.py`
  - Extended `GameOver` model for protocol-compatible fields.

- Tests added:
  - `tests/test_max_score.py`
  - `tests/test_collision.py`
  - `tests/fixtures/sample_state_round0_medium_redacted.json`
  - `tests/conftest.py`

## How To Run

```bash
python scripts/run_nmiai_grocery_bot.py --difficulty medium --show-max
python scripts/autotune_nmiai_medium.py --max-runs 30
```

## Results

- Baseline Medium score (first live baseline run): **18**
- Best score found during autotune: **129**
- Max score status:
  - Exact max not available from round 0 (server only exposes active/preview orders).
  - Current round-0 bound reported by harness: typically around **[400, 496]** on this Medium day/map.

Primary artifacts:

- Baseline run artifact:
  - `.seed_artifacts/nmiai/medium/run_20260303_030314/`
- Autotune summary artifact:
  - `.seed_artifacts/nmiai/medium/autotune/autotune_20260303_034247.json`
- Best config file:
  - `app/integrations/nmiai_grocery_bot/best_configs/medium.json`

## Known Limitations / Next Steps

- Live run variance was observed across repeated sessions with identical strategy config; best config should be validated with multiple repetitions and averaged score.
- Exact max score remains unknown unless/until the server exposes full order list earlier in-game.
- `dropoff_threshold` logic can produce unstable behavior for aggressive values; prune unsafe ranges in future tuning grids.
- Add regression tests around assignment behavior (active vs preview balance) using captured multi-bot round snapshots.
