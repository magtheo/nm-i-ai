# bot_v.3 — Clean Architecture Grocery Bot

> **Version:** 3.0  
> **Based on:** bot_v.1 and bot_v.2 — proven stable core, clean new decision layer

---

## What is bot_v.3?

bot_v.3 is a fresh-start rewrite that preserves every battle-tested component from v.1 and v.2 while replacing the accumulated complexity in the decision and assignment layers with a clean, layered architecture.

**Goals:**
- One clear responsibility per module
- Small, readable config (12 parameters — not 200+)
- Only proven tactics included; dead experiments removed
- Easy to extend without touching stable core code

---

## Project Layout

```
bot_v.3/
├── bot/                    # Core runtime (game protocol + decision engine)
│   ├── models.py           # Pydantic game-state models (unchanged from v.2)
│   ├── grid.py             # Grid utilities (unchanged from v.2)
│   ├── pathfinding.py      # BFS + A* (unchanged from v.2)
│   ├── collision.py        # One-tick reservation resolver (unchanged from v.2)
│   ├── orders.py           # Active/preview demand accounting (unchanged from v.2)
│   ├── endpoint.py         # API session + JWT helpers (unchanged from v.2)
│   ├── telemetry.py        # JSONL round logger (unchanged from v.2)
│   ├── client.py           # WebSocket play loop (unchanged from v.2)
│   └── decision_engine.py  # ★ NEW CLEAN ENGINE — see Architecture section
│
├── forge/                  # Automated offline optimisation loop (unchanged from v.2)
│   ├── protocol.py         # Protocol constants and difficulty specs
│   ├── pathfinding.py      # Standalone A* for forge core
│   ├── core.py             # Immutable strategy loader + WebSocket runner
│   ├── simulator.py        # Protocol-faithful local simulator
│   ├── strategy.py         # ★ Mutation zone — only file Forge evolves
│   └── orchestrator.py     # Codex CLI evolution loop
│
├── scripts/
│   ├── run_bot.py          # Live game runner
│   └── run_simulation.py   # Offline simulation runner
│
├── tests/
│   ├── conftest.py
│   ├── test_decision_engine.py  # Decision engine unit tests
│   └── test_forge_simulator.py  # Forge simulator integration tests
│
├── configs/
│   ├── default.json        # Default engine config
│   └── expert.json         # Expert map starting point
│
├── .env.example            # Token setup
├── requirements.txt
├── pytest.ini
└── README.md
```

---

## Quick Start

### 1. Install dependencies

```bash
cd bot_v.3
pip install -r requirements.txt
```

### 2. Set your access token

```bash
cp .env.example .env
# Edit .env and set AINM_ACCESS_TOKEN=<your token>
```

### 3. Run tests

```bash
python -m pytest -q
```

### 4. Smoke check (offline simulation — no live server needed)

```bash
python -m scripts.run_simulation
```

### 5. Live smoke check (1 run)

```bash
python -m scripts.run_bot --difficulty expert --runs 1 --debug
```

### 6. Live batch (max 3 runs per live budget policy)

```bash
python -m scripts.run_bot --difficulty expert --runs 3 --config configs/expert.json
```

---

## Architecture: The Decision Engine

Every call to `engine.decide(state)` flows through four explicit layers:

```
GameState
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Analyze                                           │
│  compute active demand, preview demand, update stall counters│
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Assign                                            │
│  greedy score-based task assignment:                        │
│    Pass 1 → deliver (bots with active cargo)                │
│    Pass 2 → pick best available item                        │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Route                                             │
│  BFS one-step desired position per bot                      │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Render                                            │
│  collision resolution → protocol BotActionCommands         │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
                         RoundActions
```

### EngineConfig — all 12 parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `active_item_weight` | 10.0 | Reward for picking an active-order item |
| `preview_item_weight` | 3.0 | Reward for picking a preview-order item |
| `dist_weight` | 1.0 | Penalty per BFS step of travel |
| `max_deliverers` | 3 | Max simultaneous delivery bots |
| `enable_preview_picks` | `true` | Allow pre-picking preview items |
| `preview_safety_slots` | 1 | Extra free slots required before preview pick |
| `starvation_rounds` | 5 | Rounds stuck before forced reassignment |
| `decision_timeout_ms` | 50.0 | Soft decision time cap (diagnostic only) |

---

## The Forge System

The Forge is an automated offline strategy evolution loop that mutates only `forge/strategy.py`
without touching the bot core. It works independently of the main engine.

```bash
# Run Forge simulation once
python -m scripts.run_simulation --strategy forge/strategy.py

# Run one Forge evolution iteration (requires Codex CLI)
python -m forge.orchestrator --iterations 1

# Infinite evolution loop
python -m forge.orchestrator --iterations 0
```

**Strategy interface contract** (must not change):
```python
def decide_intents(game_state: dict) -> list[dict]:
    ...
```

---

## Extending the Engine

The layered design makes it easy to add new mechanics without touching proven code.

### Add a new assignment strategy (e.g. Hungarian matching)

1. Implement in a new file `bot/assignment_hungarian.py`
2. Add `assignment_strategy: str = "greedy"` to `EngineConfig`
3. In `DecisionEngine._assign_tasks()`, branch on `self.config.assignment_strategy`

### Add Expert map role specialisation

1. Create `bot/roles.py` with role assignment logic
2. Call it at the start of `_assign_tasks()` to pre-classify bots
3. Route classified bots through role-specific pick logic before the greedy pass

### Add telemetry fields

`last_round_telemetry` is a plain `dict[str, float]`. Add keys in `decide()`:
```python
self.last_round_telemetry["my_metric"] = float(my_value)
```

---

## Live Budget Policy

| Rule | Limit |
|------|-------|
| Smoke check | **1 run** |
| Comparison batch | **≤3 runs** |
| Platform rate limit | **40 runs/hour** |

Always validate in simulation first. Live runs are for confirmation, not exploration.

---

## What Was Removed from v.2

The following v.2 components are intentionally **not** in bot_v.3:

| Removed | Reason |
|---------|--------|
| `decision_engine.py` (500KB, 200+ params) | Replaced by the clean layered engine |
| `assignment.py` (200KB) | Assignment logic merged into clean engine |
| `orbit_flow_engine.py` | Expert-specific, can be added as extension |
| `expert_supply_strategy.py` | Superseded by clean assignment layer |
| `experimental_dispatch_engine.py` | Experimental; not promoted |
| `cooperative_path.py` | Useful but adds complexity; can be re-added |
| `max_score.py` | Score tracking moved to telemetry |
| `configs/expert_coordination_presets/` (50+ JSONs) | Start clean; add presets as you run experiments |

All of these are preserved in `bot_v.2/` and can be ported back if a specific mechanism is proven to help.

---

## Experiment Workflow

```
Hypothesis
    ↓
Narrow patch to one layer of decision_engine.py or forge/strategy.py
    ↓
python -m pytest -q                          # tests pass
    ↓
python -m scripts.run_simulation             # offline baseline
    ↓
python -m scripts.run_bot --runs 1 --debug   # smoke check
    ↓
python -m scripts.run_bot --runs 3           # batch
    ↓
Verdict: promotable | negative_experiment | inconclusive
```
