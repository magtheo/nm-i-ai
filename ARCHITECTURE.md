# Grocery Bot Lab — System Architecture

> **Last updated:** 2026-03-12
> **Primary focus:** `bot_v.2` — the active, production-grade bot workspace

---

## 1. Overview

This repository is the canonical workspace for the **NM i AI 2026 Grocery Bot** competition.
The goal is to build and continuously optimize a multi-bot controller that navigates a simulated grocery store, picks items from shelves, and delivers them to fulfil sequential orders — maximizing score across four difficulty levels (Easy, Medium, Hard, Expert).

- **Platform:** `app.ainm.no` (production) / `dev.ainm.no` (development)
- **Protocol:** WebSocket, JSON messages
- **Session:** ≤300 rounds per game; one `game_state` in → one `actions` out per round
- **Scoring:** +1 per item delivered, +5 per completed order
- **Leaderboard:** Sum of best scores across Easy / Medium / Hard / Expert maps

---

## 2. Repository Layout

```
nm-i-ai/
├── bot_v.2/            ★ Active bot workspace (all live work happens here)
│   ├── bot/            Core runtime modules (~14 000 lines of Python)
│   ├── scripts/        Live / sim / analysis tooling
│   ├── tests/          Behaviour & regression tests
│   ├── configs/        Parameter presets for all difficulty levels
│   ├── forge/          Automated offline optimisation loop
│   ├── artifacts/      Optional local run outputs
│   └── .seed_artifacts/Live and smoke run outputs
├── agents/             Role-specific AI agent prompts
│   ├── coding_agent/
│   ├── analysis_agent/
│   ├── planner_agent/
│   └── review_agent/
├── shared/             Cross-agent coordination and shared state
│   ├── shared_state/   Canonical baseline registry (canonical_bot.yaml)
│   ├── curated/        Operational summaries and runbooks
│   └── inbox/          Handoff packages between agents
├── experiments/        Cross-session experiment records
├── bot_v.1/            Legacy archive
└── bot/                Minimal legacy placeholder
```

The canonical **source of truth** is always the repository / lab file structure.
Chat summaries and in-session notes are secondary and non-authoritative.

---

## 3. Difficulty Levels and Game Configuration

| Level     | Grid   | Bots | Aisles | Item Types | Items/Order | Focus              |
|-----------|--------|------|--------|------------|-------------|--------------------|
| Easy      | 12×10  | 1    | 2      | 4          | 3–4         | Supported          |
| Medium    | 16×12  | 3    | 3      | 8          | 3–5         | Supported          |
| Hard      | 22×14  | 5    | 4      | 12         | 3–5         | Supported          |
| Expert    | 28×18  | 10   | 5      | 16         | 4–6         | **Primary focus**  |
| Nightmare | —      | —    | —      | —          | —           | Infra ready, not optimised |

---

## 4. Core Runtime Modules (`bot_v.2/bot/`)

### 4.1 Data Models (`models.py`)

Pydantic v2 data classes for the full game protocol:
`GameState`, `BotInfo`, `ItemInfo`, `OrderInfo`, `GameOver`, `BotActionCommand`.
These are the authoritative in-process types — all modules consume and produce these.

### 4.2 WebSocket Client (`client.py`)

- Async `websockets` connection to `wss://game.ainm.no/ws?token=<jwt>`
- Drives the per-round receive → decide → send loop
- Handles session lifecycle: connect, play, disconnect on `game_over`

### 4.3 Endpoint Helper (`endpoint.py`)

- JWT token management and WebSocket URL assembly
- Reads `AINM_ACCESS_TOKEN` from environment (`.env` / OS)
- Per-difficulty map and seed selection

### 4.4 Decision Engine (`decision_engine.py`)

The **per-round orchestrator** for the 10-bot swarm. On each tick it:

1. Reads `game_state` for current bot positions, held items, active/preview orders, and scores.
2. Identifies the **active order** (must be fulfilled first) and **preview orders** (pre-picking allowed).
3. Computes candidate item utility scores (distance, scarcity, order priority, congestion penalty).
4. Selects an **assignment strategy** (greedy / auction / Hungarian) based on configuration.
5. Routes each bot via the pathfinding module.
6. Applies collision resolution before action rendering.
7. Returns a `BotActionCommand` list for the current round.

Key tunable parameters (100+): active weight, preview weight, dropout completion threshold,
collision risk weight, auction option depth, Hungarian fallback threshold, delivery concurrency min/max.

### 4.5 Assignment Engine (`assignment.py`)

Task assignment policy engine. Computes utility scores for every (bot, item) pair across
active and preview orders. Supports three strategies:

| Strategy  | Mechanism                                  | Typical Use             |
|-----------|--------------------------------------------|-------------------------|
| Greedy    | Fast utility-ranked selection              | Fast path / fallback    |
| Auction   | Option-depth bidding (12–18 options)       | Medium complexity maps  |
| Hungarian | Optimal bipartite matching (18-opt depth)  | Expert map primary      |

Penalises congestion corridors and collision-prone assignments.
Tracks per-bot commitment state to avoid redundant re-assignment.

### 4.6 Orbit Flow Engine (`orbit_flow_engine.py`)

Specialised engine for the **Expert map**.
- Maintains persistent **orbit ranks** for all 10 bots (a staging / circulation ring near shelves).
- Allocates **pick sorties** (targeted shelf runs) to ranked bots.
- Manages the **delivery queue** with configurable min/max concurrency.
- Controls **return-to-orbit** admission after each delivery.

### 4.7 Expert Supply Strategy (`expert_supply_strategy.py`)

Role- and phase-driven supply chain for Expert:

| Phase             | Description                                          |
|-------------------|------------------------------------------------------|
| Boot              | Initial positioning and role assignment              |
| Active harvest    | Prioritised picking for the active order             |
| Delivery          | Bots carrying items route to drop-off                |
| Queue / preview   | Pre-picking for upcoming preview orders              |

Bot roles: `courier`, `harvester`, `flex`.

### 4.8 Pathfinding (`pathfinding.py`)

- **BFS** for shortest-path finding on walkable cells.
- **A\*** for heuristic-guided routing with obstacle awareness.
- Collision-aware: respects reservation tables built each round.

### 4.9 Collision Avoidance (`collision.py`, `cooperative_path.py`)

- Per-round **one-tick reservation** to prevent two bots occupying the same cell.
- `cooperative_path.py` detects conflict components and resolves deadlocks via multi-bot cooperative re-routing.

### 4.10 Supporting Utilities

| Module           | Purpose                                             |
|------------------|-----------------------------------------------------|
| `orders.py`      | Active/preview demand accounting, commit tracking   |
| `grid.py`        | Walkable-cell grid utilities                        |
| `max_score.py`   | Score calculation and per-round tracking            |
| `telemetry.py`   | Round-by-round structured logging and diagnostics   |

### 4.11 Offline Simulator (`_simulator.py`)

Fully deterministic offline game simulator used for all hypothesis testing before live runs:
- Generates maps by `(difficulty, seed)` with exact protocol fidelity.
- Resolves actions in bot-ID order.
- Strict validator: any action outside the allowed set fails immediately.
- Score model mirrors live: `+1 item`, `+5 order`.

---

## 5. Simulation System

### 5.1 Purpose

All algorithmic changes must pass offline simulation **before** any live run.
This avoids burning live-run budget on obviously broken patches and provides
fast, reproducible iteration cycles.

### 5.2 Running Simulations

```bash
# Single-run simulation (from inside bot_v.2/)
python -m scripts.run_simulation --mode single --seed 0

# Forge simulator (isolated strategy module)
python -m scripts.run_forge_simulation \
  --strategy-file forge/strategy.py \
  --output .forge_runs/smoke.json
```

### 5.3 Two Simulation Layers

| Layer               | Module                   | Use Case                                         |
|---------------------|--------------------------|--------------------------------------------------|
| Main simulator      | `bot/_simulator.py`      | Full-stack hypothesis tests using full bot core  |
| Forge simulator     | `forge/simulator.py`     | Strategy mutation loop; isolated from bot core   |

---

## 6. Branch and Experiment System

### 6.1 Branch Lifecycle

Each experiment is a named **branch** tracked in `BRANCH_SCORECARD.md`.

| Status       | Meaning                                              |
|--------------|------------------------------------------------------|
| `active`     | Current main experiment path                         |
| `paused`     | Temporarily inactive                                 |
| `capped`     | Near local ceiling, low expected upside              |
| `deprecated` | No longer a recommended direction                    |

### 6.2 Current Branch Registry (as of 2026-03-12)

| Branch | Status | Median Score | Notes |
|---|---|---|---|
| `canonical_active_branch` (Bundle A + patched-empty starvation-relief) | active | 88 | Safety baseline; 9 completions |
| `conversion_guard_rnd_branch` | paused | 29 | Telemetry promoted; actuator frozen |
| `conversion_safe_targeted_retrieval_fork_v1` | paused | 32 | Target-liveness failures |
| `points_mode_fork_v1` | deprecated | 89 | Extra items but completion cadence regressed |
| `cadence_controller_fork_v1` | deprecated | 88 | Close mode delayed 9th completion |
| `pipeline_budget_fork_v1` | deprecated | 70 | Hard budgets suppressed throughput |
| `soft_pipeline_budget_fork_v1` | deprecated | 78 | Soft penalties raised no-assignment |
| `task_pool_fork_v1` | deprecated | 54 | Over-admitted active-close tasks |
| `spatial_logistics_fork_v1` | deprecated | 0 | Near-zero pick-to-drop conversion |

### 6.3 Experiment Workflow

```
Hypothesis
    ↓
Narrow patch definition (touch minimum files)
    ↓
Offline tests: pytest + simulation
    ↓
Live smoke check (exactly 1 run)
    ↓
Live batch (at most 3 runs)
    ↓
Median comparison + Conversion Acceptance Gates
    ↓
Verdict: promotable | negative_experiment | inconclusive
    ↓
Log in EXPERIMENT_JOURNAL.md
```

### 6.4 Mandatory Conversion Acceptance Gates

Every branch batch must pass six gates before promotion is considered:

1. **Target-liveness** — bots have valid assignment targets
2. **Drop conversion floor** — minimum items reaching drop-off
3. **Pickup-to-drop coupling** — items picked → items delivered
4. **Commitment realism** — bots follow their assignments
5. **Throughput lane floor** — sustained item flow throughout game
6. **Delivery-lane guarantee** — no deadlock at drop-off

Gate check:
```bash
python -m scripts.check_conversion_acceptance \
  --artifact-root .seed_artifacts/experiments/_batch \
  --difficulty expert --limit 3
```

---

## 7. Configuration System (`configs/`)

### 7.1 Expert Coordination Presets

40+ JSON parameter files in `configs/expert_coordination_presets/`.
Each preset encodes one experimental configuration of the 100+ decision-engine parameters.

Key presets:

| File | Role | Median Score |
|------|------|---|
| `bundle_a_starvation_relief_empty_only.json` | **Canonical baseline** | 88 |
| `bundle_a_conversion_telemetry_only.json` | Diagnostic telemetry guard | 88 |
| `bundle_a_critical_dispatch_overlay_v*` | Multi-iteration experimental variants | varies |
| `experimental_dispatch_engine_*` | Alternative dispatch mechanisms | varies |

### 7.2 Parameter Scope

Parameters control:
- Assignment strategy selection and fallback thresholds
- Delivery concurrency (min/max bots in delivery phase)
- Preview pre-pick enablement and budget
- Collision risk weighting
- Orbit admission and return policy (Expert only)
- Starvation recovery thresholds

---

## 8. Forge Automation System (`bot_v.2/forge/`)

An isolated, automated optimisation loop that evolves the strategy module without touching the bot core.

### 8.1 Modules

| Module              | Role                                                                 |
|---------------------|----------------------------------------------------------------------|
| `simulator.py`      | Deterministic map generation and protocol-level action resolution    |
| `core.py`           | Immutable runtime shell: `game_state` parsing, A\* navigation, JSON action rendering |
| `strategy.py`       | **Mutation-only zone** with required `decide_intents()` interface    |
| `orchestrator.py`   | Baseline eval → prompt assembly → Codex CLI generation → verify/fix loop → promote or rollback |

### 8.2 Strategy Interface Contract

```python
def decide_intents(game_state: dict[str, Any]) -> list[dict[str, Any]]:
    ...
```

This interface signature must not change. The orchestrator mutates only the function body.

### 8.3 Forge Commands

```bash
# Smoke test
python -m scripts.run_forge_simulation \
  --strategy-file forge/strategy.py --output .forge_runs/smoke.json

# One evolution iteration
python -m scripts.run_forge_evolution --iterations 1

# Infinite loop
python -m scripts.run_forge_evolution --iterations 0
```

---

## 9. Scripts (`bot_v.2/scripts/`)

| Script | Purpose |
|--------|---------|
| `run_nmiai_grocery_bot.py` | Main live bot runner with full artifact recording |
| `run_simulation.py` | Offline simulation harness |
| `review_run.py` | Post-run analysis and artifact inspection |
| `compare_runs.py` | Multi-run median / regression comparison |
| `check_conversion_acceptance.py` | Mandatory acceptance gate validation |
| `render_live_ui.py` | UI replay renderer from recorded artifacts |
| `orbit_wall_log_analyzer.py` | Expert map orbit diagnostics |
| `experiment_expert_coordination.py` | Coordination layer experiment runner |
| `autotune_nmiai_medium.py` | Medium difficulty parameter tuning |
| `tune_orbit_wall_expert.py` | Expert map parameter optimisation |

### 9.1 Standard Live Run Command

```bash
python -m scripts.run_nmiai_grocery_bot \
  --difficulty expert \
  --legacy-expert-decision-engine \
  --params-file configs/expert_coordination_presets/bundle_a_starvation_relief_empty_only.json \
  --runs 3 \
  --cooldown-sec 1 \
  --record \
  --record-order-trace \
  --record-decision-trace \
  --artifact-root .seed_artifacts/experiments/_batch
```

---

## 10. Tests (`bot_v.2/tests/`)

| Test File | Coverage Area |
|-----------|---------------|
| `test_assignment_*.py` | Assignment strategy variants (Hungarian, auction, pipeline budget) |
| `test_decision_engine_*.py` | Decision engine components (coordination, conversion guard, cadence control) |
| `test_forge_simulator.py` | Forge simulator fidelity validation |
| `test_orbit_wall_conveyor.py` | Expert orbit flow engine |
| `test_collision.py` | Collision avoidance resolution |

Run all tests:
```bash
cd bot_v.2 && python -m pytest -q
```

---

## 11. AI Agent Coordination (`agents/`, `shared/`)

### 11.1 Role-Specific Agents

| Agent | Role |
|-------|------|
| `coding_agent/` | Implements patches; reads canonical bot root |
| `analysis_agent/` | Interprets traces and artifacts; writes verdicts |
| `planner_agent/` | Selects next hypothesis; updates BRANCH_SCORECARD |
| `review_agent/` | Reviews patches for correctness and regression risk |

### 11.2 Shared Coordination Files

| File | Purpose |
|------|---------|
| `shared/shared_state/canonical_bot.yaml` | Source of truth for active bot selection |
| `shared/curated/CURRENT_STATE.md` | Live operational snapshot |
| `shared/curated/PROJECT_PINNED_SUMMARY.md` | Core model, focus, known ceilings |
| `shared/curated/AGENT_CONTEXT.md` | AI agent context and responsibilities |
| `shared/curated/baseline_policy_expert.md` | Expert baseline policy |

### 11.3 Roles of External Tools

| Tool | Role |
|------|------|
| Codex CLI | Strategy mutation generation inside Forge loop |
| ChatGPT Business Project | Continuity and coordination layer (**not** canonical storage) |
| Custom GPTs (role-based) | Coding, analysis, planning, review — scoped per task |

---

## 12. Live Budget Policy

| Rule | Limit |
|------|-------|
| Smoke check | **1 run** |
| Comparison batch | **≤3 runs** |
| Platform rate limit | **40 runs/hour** |

Live runs are for hypothesis **confirmation**, not exploration.
All speculative testing must use the offline simulator first.

---

## 13. Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.x |
| Data validation | Pydantic ≥2.0.0 |
| WebSocket protocol | `websockets` ≥12.0 |
| HTTP client | `requests` ≥2.31.0 |
| Pathfinding | Custom BFS / A\* implementations |
| Assignment | Custom Greedy / Auction / Hungarian implementations |
| Testing | `pytest` |
| Config format | JSON (parameter presets) |
| Artifact format | JSONL (decision trace), JSON (order trace, results) |
| Automation | Codex CLI (Forge evolution loop) |
| Version control | Git |

---

## 14. Key Design Decisions

### 14.1 Active-First Order Prioritisation

```
Active order (must complete) → Preview order (pre-pick allowed) → Fallback strategies
```

Bots are never allowed to starve the active order in favour of speculative pre-picking.

### 14.2 Conversion Gate Discipline

Score spikes that break conversion gates are rejected regardless of raw score.
This prevents accepting brittle improvements that collapse under variation.

### 14.3 Offline-First Validation

Every non-trivial algorithmic change is validated in simulation before spending live-run budget.
This compresses iteration cycles and preserves the 40-run/hour platform budget for meaningful comparisons.

### 14.4 Minimal Patch Discipline

Each experiment touches the minimum number of files.
Infrastructure changes are never mixed with algorithmic changes in the same patch.

### 14.5 Median-Based KPI

Single-run score spikes are not sufficient for promotion.
All decisions use **median score** across 3 runs as the primary KPI.
