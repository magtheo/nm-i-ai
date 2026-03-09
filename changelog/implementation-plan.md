# Grocery Bot Implementation Plan

**Created:** 2026-03-09
**Last Updated:** 2026-03-09
**Status:** In Progress

---

## Overview

Building a competitive bot for the NM i AI 2026 Grocery Bot Challenge. The bot will connect via WebSocket, parse game state, and make intelligent decisions for multi-agent coordination.

**Goal:** Build a bot that performs well across all 5 difficulty levels (Easy → Nightmare).

**Architecture:** Python with asyncio, websockets, and classical algorithms (BFS, task assignment, collision avoidance).

---

## Phase 1: Strong Baseline

**Goal:** Build a working bot that can complete games and score points.

### 1.1 Project Setup
- [ ] Create project structure with proper folders
- [ ] Set up requirements.txt with dependencies (websockets, etc.)
- [ ] Create configuration file for tokens and settings
- [ ] Set up basic logging infrastructure

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.2 WebSocket Connection & Game Loop
- [ ] Implement WebSocket connection to game server
- [ ] Handle game_state messages
- [ ] Handle game_over messages
- [ ] Implement action sending
- [ ] Add error handling and reconnection logic

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.3 State Parser
- [ ] Parse grid and walls
- [ ] Parse bot positions and inventories
- [ ] Parse items on shelves
- [ ] Parse orders (active and preview)
- [ ] Parse drop-off zones
- [ ] Create internal state representation

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.4 Map/Distance Engine
- [ ] Implement BFS shortest path algorithm
- [ ] Precompute distances from all traversable tiles
- [ ] Cache distance tables per map
- [ ] Implement path reconstruction

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.5 Basic Decision Making
- [ ] Implement simple move_toward() function
- [ ] Implement pick_up logic (adjacent check, inventory check)
- [ ] Implement drop_off logic (position check, order matching)
- [ ] Implement wait action

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.6 Active Order Item Matching
- [ ] Track which items are needed for active order
- [ ] Track which items have been delivered
- [ ] Assign bots to collect needed items
- [ ] Basic greedy assignment (nearest bot to nearest item)

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.7 Preview Order Prefetch
- [ ] Track preview order items
- [ ] Assign idle bots to prefetch preview items
- [ ] Balance between active and preview priorities

**Status:** Not Started
**Started:** 
**Completed:** 

### 1.8 Basic Collision Avoidance
- [ ] Implement one-step tile reservation
- [ ] Prevent two bots from moving to same tile
- [ ] Implement wait/yield for lower priority bots
- [ ] Basic priority system (e.g., bot ID)

**Status:** Not Started
**Started:** 
**Completed:** 

**Phase 1 Milestone:** Bot can complete games on Easy and Medium difficulties with reasonable scores.

---

## Phase 2: Route Bundling

**Goal:** Improve efficiency by having bots plan multi-item collection routes.

### 2.1 Bundle-Aware Pickup
- [ ] Identify clusters of needed items
- [ ] Score bundles by total distance vs. individual trips
- [ ] Assign bots to optimal bundles
- [ ] Plan multi-stop routes (pickup A → pickup B → drop-off)

**Status:** Not Started
**Started:** 
**Completed:** 

### 2.2 Capacity-Aware Planning
- [ ] Consider inventory capacity (max 3) in route planning
- [ ] Decide when to drop off vs. continue collecting
- [ ] Optimize for order completion bonus (+5)

**Status:** Not Started
**Started:** 
**Completed:** 

**Phase 2 Milestone:** Bot shows improved efficiency on Hard difficulty with fewer wasted trips.

---

## Phase 3: Congestion Control

**Goal:** Handle high-density bot scenarios (Expert/Nightmare).

### 3.1 Advanced Collision Avoidance
- [ ] Implement reservation table for 3-5 steps ahead
- [ ] Implement anti-swap rule (prevent A↔B swaps)
- [ ] Add aisle congestion penalties
- [ ] Implement cooperative A* or similar

**Status:** Not Started
**Started:** 
**Completed:** 

### 3.2 Drop-Off Zone Balancing
- [ ] For Nightmare (3 drop-off zones), choose optimal zone
- [ ] Balance load across zones
- [ ] Consider bot positions and inventory

**Status:** Not Started
**Started:** 
**Completed:** 

### 3.3 Priority System Enhancement
- [ ] Priority based on carrying active-order items
- [ ] Priority based on proximity to completion
- [ ] Priority based on critical path analysis

**Status:** Not Started
**Started:** 
**Completed:** 

**Phase 3 Milestone:** Bot performs well on Expert difficulty with minimal congestion issues.

---

## Phase 4: Global Task Assignment

**Goal:** Replace greedy assignment with optimal global assignment.

### 4.1 Task Scoring Model
- [ ] Implement V_active (active item delivery value)
- [ ] Implement V_completion (order completion bonus)
- [ ] Implement V_preview (prefetch value)
- [ ] Implement V_positioning (future positioning value)
- [ ] Add congestion and conflict risk penalties

**Status:** Not Started
**Started:** 
**Completed:** 

### 4.2 Assignment Algorithm
- [ ] Build cost matrix (bots × tasks)
- [ ] Implement Hungarian algorithm or greedy+repair
- [ ] Handle task conflicts
- [ ] Reassign on each round

**Status:** Not Started
**Started:** 
**Completed:** 

**Phase 4 Milestone:** Bot shows optimal task distribution on all difficulties.

---

## Phase 5: Daily Deterministic Optimization

**Goal:** Exploit deterministic nature of daily maps.

### 5.1 Replay Logging
- [ ] Log all game states and actions
- [ ] Store replay files per day/difficulty
- [ ] Create replay analysis tools

**Status:** Not Started
**Started:** 
**Completed:** 

### 5.2 Map Caching
- [ ] Cache distance tables per map
- [ ] Detect if map has been seen before
- [ ] Load cached data on repeat runs

**Status:** Not Started
**Started:** 
**Completed:** 

### 5.3 Heuristic Tuning
- [ ] Run multiple attempts on same map
- [ ] Compare scores and identify improvements
- [ ] Adjust coefficients based on performance

**Status:** Not Started
**Started:** 
**Completed:** 

**Phase 5 Milestone:** Bot improves scores on repeated runs of the same daily map.

---

## Phase 6: Advanced Features

**Goal:** Polish and optimize for leaderboard competition.

### 6.1 Role Specialization (Nightmare)
- [ ] Implement runner/picker/floater roles
- [ ] Dynamic role assignment based on situation
- [ ] Lane discipline around central routes

**Status:** Not Started
**Started:** 
**Completed:** 

### 6.2 Completion-First Bias
- [ ] Heavily weight last missing item for active order
- [ ] Accelerate order completion when close
- [ ] Unlock next order faster

**Status:** Not Started
**Started:** 
**Completed:** 

### 6.3 Drop-Off Timing Optimization
- [ ] Decide when to drop off vs. collect more
- [ ] Consider order completion potential
- [ ] Avoid premature drop-offs

**Status:** Not Started
**Started:** 
**Completed:** 

**Phase 6 Milestone:** Bot achieves competitive scores on Nightmare difficulty.

---

## Testing Strategy

### Unit Tests
- [ ] Test BFS pathfinding
- [ ] Test state parsing
- [ ] Test action generation
- [ ] Test collision avoidance logic

### Integration Tests
- [ ] Test full game loop on Easy
- [ ] Test on each difficulty level
- [ ] Test edge cases (full inventory, no items, etc.)

### Performance Tests
- [ ] Verify response time < 2 seconds
- [ ] Profile and optimize bottlenecks
- [ ] Test with 20 bots on Nightmare

---

## File Structure (Planned)

```
nm-i-ai/
├── changelog/
│   └── implementation-plan.md
├── nm-docs/
│   ├── grocery-bot.md
│   └── overview.md
├── src/
│   ├── __init__.py
│   ├── bot.py              # Main bot class
│   ├── connection.py       # WebSocket handling
│   ├── state.py            # State parsing and representation
│   ├── pathfinding.py      # BFS and distance calculations
│   ├── tasks.py            # Task generation and assignment
│   ├── actions.py          # Action generation
│   ├── collision.py        # Collision avoidance
│   └── utils.py            # Helper functions
├── tests/
│   ├── test_pathfinding.py
│   ├── test_state.py
│   └── test_actions.py
├── config.py               # Configuration settings
├── requirements.txt        # Dependencies
├── main.py                 # Entry point
└── plan.md                 # Original plan document
```

---

## Current Priority

**Next Task:** Phase 1.1 - Project Setup

Create the basic project structure and set up dependencies so we can start building the bot.

---

## Notes

- Keep runtime lean - 2 second limit per round is generous but don't waste it
- Focus on correctness first, optimization later
- Test early and often on actual game server
- Exploit daily determinism for competitive advantage
