This is very workable, and the structure strongly favors classical search + multi-agent task allocation over anything “fancy AI”.

What matters most is not LLMs. It is:

* fast pathfinding
* good task assignment
* avoiding duplicated work
* planning for active + preview order together
* handling congestion near shelves and drop-off

The scoring model makes that clear:

* +1 per delivered item
* +5 per completed order

So the true objective is not just item throughput. It is **high order-completion cadence** while also using preview information to reduce future dead time.

## What the game is really asking you to solve

At a systems level, this is:

* a deterministic grid world
* full observability
* sequential order fulfillment
* multi-agent pickup-and-delivery with capacity constraints
* short horizon repeated replanning

That means a strong solution is likely:

1. **Precompute distances** on the map
2. **Assign bots to item targets** intelligently each round
3. **Reserve paths / tiles** to reduce collisions
4. **Use preview-order prefetching**
5. **Choose drop-off timing** carefully so active order completes fast

## Key observations from the rules

### 1. Daily determinism is huge

> “Item placement and orders change daily — same day, same game (deterministic).”

This is one of the biggest advantages available.

That means on a given day and difficulty:

* the map instance is fixed
* order stream is fixed
* item placements are fixed

So you can do repeated runs and improve against the exact same environment. That enables:

* caching map-specific distance tables
* replay-based tuning
* offline optimization for each difficulty every day
* learning better heuristics from your own previous runs

This is probably one of the strongest legal competitive edges.

### 2. Full map visibility means planning can be global

You do not need exploration logic. You always know:

* all walls
* all items
* all bots
* all visible orders
* all drop-off zones

So every round should be a global optimization step, not local greedy wandering.

### 3. Preview order is strategically important

Since preview is visible and bots can pre-pick for it, a strong bot should often:

* prioritize finishing current order quickly
* use spare bot capacity to stage items for the preview order
* avoid overcommitting too many bots to preview if it delays the current +5 bonus

### 4. Inventory capacity is only 3

This matters a lot. Capacity 3 means:

* trip batching matters
* assignment should consider bundles, not just single items
* bots near matching clusters should fill efficiently
* carrying useless items is costly unless they are good preview-prep items

### 5. Collisions will kill naive multi-bot solutions

With 10 and especially 20 bots, a greedy “every bot moves toward nearest item” strategy will degrade badly due to:

* blocking in narrow aisles
* competing for same tile
* deadlocks near drop-off
* redundant assignments

You need explicit coordination.

---

# Recommended architecture

## Core loop per round

For each game tick:

1. Parse state
2. Update internal task model
3. Build candidate tasks per bot
4. Assign tasks globally
5. Generate conflict-aware next moves
6. Send one action per bot

## Internal modules

### 1. Map / distance engine

Precompute shortest path distances on the static wall map.

Use BFS from every traversable tile, or more efficiently:

* BFS from every important target tile:

  * each drop-off zone
  * each shelf-adjacent pickup position
  * maybe every traversable tile if grid is still small enough

The grids are tiny enough that full all-pairs shortest-path-by-BFS is feasible.

Why this matters:

* fast evaluation of task cost
* better assignment
* less per-turn recomputation

### 2. Order analyzer

Maintain:

* active order remaining items
* preview order remaining items
* item counts on map by type
* which bots already carry useful items

Important detail: when computing “needed”, account for:

* already delivered active items
* items already in bot inventories
* items already assigned to another bot this round

Otherwise you overassign.

### 3. Task generator

For each bot, produce candidate tasks such as:

* pick active item X
* pick active item Y
* deliver current inventory
* prefetch preview item Z
* reposition toward drop-off
* wait / yield

Each candidate should have a score like:

**utility / estimated cost**

Where utility includes:

* active item delivery value
* order completion acceleration
* preview preparation value
* reduced future travel
* congestion penalty

### 4. Task allocator

Use a global assignment algorithm rather than greedy nearest-neighbor.

A good practical choice:

* build a cost matrix of bots × candidate tasks
* solve with Hungarian algorithm or a simpler greedy + repair heuristic

For larger branching, a simpler but robust method is fine:

* rank tasks by global value
* assign highest-value non-conflicting task
* remove conflicting tasks
* repeat

This may be easier to tune than a pure optimal assignment.

### 5. Path planner with collision avoidance

At minimum, use:

* shortest path from current tile to target tile
* one-step reservation for next moves
* tie-breaking by bot priority

Better:

* cooperative A* or reservation-table planning for 1–3 steps ahead

But the map is small and per-turn time is generous, so even a lightweight reservation scheme may be enough.

A simple strong baseline:

* sort bots by priority
* higher-priority bots reserve their intended next tile
* lower-priority bots re-route or wait
* prevent edge swaps too:

  * bot A moves into B’s tile while B moves into A’s tile

Priority can be based on:

* currently carrying active-order items
* being on a critical completion path
* proximity to drop-off with full useful inventory
* static tie-breaker by bot id

---

# Strategic policy recommendations

## Easy

Single bot, so this is pure routing.

Best approach:

* shortest-path planning
* batch useful items when possible
* exploit preview order pre-picks
* avoid unnecessary drop-offs before inventory is well used

This should be close to a shortest-delivery scheduling problem.

## Medium / Hard

Now coordination becomes the main factor.

Strong pattern:

* one or two bots focus on active order completion
* one bot opportunistically stages preview items
* avoid sending multiple bots into same aisle unless justified

## Expert / Nightmare

This becomes a warehouse swarm problem.

You likely want role specialization:

* **runners**: bots transporting items to drop-off
* **pickers**: bots near shelves gathering targeted items
* **floaters**: bots filling gaps / prefetching preview
* **traffic managers by policy**: lane discipline around central routes

For Nightmare, with 3 drop-off zones, assignment must include **which drop-off to use**. That alone can save a lot of travel.

---

# Important heuristics that will probably outperform naive bots

## 1. Bundle-aware pickup

Do not assign only nearest single item.

Prefer bots that can collect:

* 2–3 relevant items on one trip
* especially if adjacent or in same aisle

A bot with capacity 3 should often be planned as a mini-route:
pickup A → pickup B → pickup C → drop-off

not just:
pickup A → drop-off

unless current order completion is urgent.

## 2. Completion-first bias

Because an order completion gives +5, the system should heavily value the final missing items for the active order.

Example:
if active order needs 1 last item, that item is worth effectively:

* +1 item delivered
* +5 completion
* plus unlocking next order sooner

So the last missing item is extremely valuable.

## 3. Preview prefetch only when spare capacity exists

Prefetching preview is good, but only when it does not materially delay active completion.

A good rule:

* active-order completion dominates
* preview prefetch is secondary
* except when a bot would otherwise be idle or poorly positioned

## 4. Keep useful inventory, avoid junk inventory

Since non-matching items stay in inventory at drop-off, a bot can carry preview items in advance. That is useful.

But inventory is scarce. So only pre-pick preview items that are likely to matter soon and are expensive to fetch later.

## 5. Drop-off timing matters

Do not always drop off immediately when standing on the zone if holding a mix that may be better completed after one more pickup nearby.

But also do not delay if it completes the order.

---

# A strong scoring model for tasks

You can score a candidate task with something like:

[
score = \frac{V_{active} + V_{completion} + V_{preview} + V_{positioning}}{travel_cost + congestion_risk + conflict_risk}
]

Where:

* **V_active** = reward for contributing active items
* **V_completion** = large bonus if this likely completes order
* **V_preview** = smaller value for useful prefetch
* **V_positioning** = value of ending closer to future useful regions

And costs include:

* travel distance
* expected collision risk
* aisle congestion
* opportunity cost of tying up capacity

For the last missing active item, make `V_completion` large.

---

# What I would implement first

## Phase 1 — strong baseline

Build this first:

* BFS shortest paths
* per-round recomputation
* active-order item matching
* preview-order prefetch
* global task assignment
* one-step collision avoidance

This alone could already be quite competitive.

## Phase 2 — route bundling

Improve bots so they plan 2–3 item collection routes rather than single-item goals.

## Phase 3 — congestion control

Add:

* reservation table
* anti-swap rule
* aisle penalties
* drop-off zone balancing in Nightmare

## Phase 4 — daily deterministic optimization

Because the environment is deterministic per day:

* store replays
* evaluate heuristic weights automatically
* run many attempts
* adjust coefficients for each difficulty daily

That is likely where leaderboard gains come from.

---

# Likely legal competitive edge

Based on the rules, these are clearly within fair play if done through your own agent logic:

* repeated runs on the same daily deterministic instance
* caching maps and distance tables
* tuning heuristics from previous runs
* building strong planners
* exploiting preview information
* optimizing drop-off selection

What you should avoid:

* probing rate limit weaknesses
* attacking server behavior
* relying on bugs
* reverse engineering internals beyond observed gameplay behavior

---

# My blunt view on “AI”

For this pre-competition, “AI” is mostly branding. The winning bot is likely to look more like:

* search
* scheduling
* combinatorial optimization
* multi-agent coordination

not an LLM agent.

You could maybe use LLMs offline to help write heuristics or analyze replay logs, but the runtime bot itself should be deterministic, fast, and algorithmic.

---

# Best practical stack

Python is fine here.

Use:

* `asyncio`
* `websockets`
* `collections`
* `heapq`
* maybe `numpy` if useful, but not necessary

Keep the runtime extremely lean. The 2-second turn limit is generous for these map sizes, but avoid unnecessary complexity.

---

# My recommendation for your first serious version

Implement:

* map parser
* BFS pathfinder
* active/preview demand tracker
* task assignment per round
* collision-aware next-step planner
* replay logging

Then use a policy like:

1. deliver last missing active items first
2. otherwise assign bots to best active pickups
3. idle spare capacity prefetches preview
4. full or high-value bots head to nearest valid drop-off
5. use tile reservations to avoid collisions

That should already be much better than the example bot.

If you want, I can next write a **full competitive Python bot architecture** for this challenge, with modules and a solid first implementation strategy.
