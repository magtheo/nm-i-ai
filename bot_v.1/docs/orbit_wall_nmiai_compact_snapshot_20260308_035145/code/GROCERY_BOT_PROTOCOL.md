# NM i AI 2026 — Grocery Bot Protocol Specification

> **Source:** MCP docs server `https://mcp-docs.ainm.no/mcp` (nmiai-challenge v3.0.2)
> **Cross-validated with:** Captured game data from Easy map (seed 7001, 2026-02-28)
> **Last updated:** 2026-03-03

---

## 1. Overview

Grocery Bot is a pre-competition warm-up for NM i AI 2026 (Feb 20 – Mar 16, 2026). Players build a bot that connects via WebSocket, navigates a grocery store grid, picks items from shelves, and delivers them to fulfil sequential orders.

- **Platform:** `app.ainm.no` (production) / `dev.ainm.no` (development)
- **Flow:** Pick difficulty → Get WebSocket URL with JWT → Connect bot → Receive `game_state` → Respond with `actions` → Repeat for up to 300 rounds
- **Leaderboard:** Sum of best scores across all 4 difficulty maps

---

## 2. Connection

### WebSocket URL

```
wss://game.ainm.no/ws?token=<jwt_token>
```

> **Note:** Docs reference `game-dev.ainm.no` but production games use `game.ainm.no`. Both have been confirmed working.

### Authentication

The JWT token is obtained from the web UI (`dev.ainm.no` or `app.ainm.no`). Token payload contains:

```json
{
  "team_id": "16bbec4-aa80-4207-ba20-fd96c333aaa6",
  "map_id": "c89da2ec-3ca7-40c9-a3b1-8036fca3d0b7",
  "map_seed": 7001,
  "difficulty": "easy",
  "exp": 1772258254
}
```

- Tokens expire ~12 minutes after generation (check `exp` claim)
- 10-second cooldown between games
- Disconnect = game ends immediately

---

## 3. Difficulty Levels

| Level    | Grid   | Bots | Aisles | Item Types | Items/Order |
|----------|--------|------|--------|------------|-------------|
| Easy     | 12×10  | 1    | 2      | 4          | 3-4         |
| Medium   | 16×12  | 3    | 3      | 8          | 3-5         |
| Hard     | 22×14  | 5    | 4      | 12         | 3-5         |
| Expert   | 28×18  | 10   | 5      | 16         | 4-6         |

---

## 4. Coordinate System

- **Origin:** (0, 0) = **top-left** corner
- **X axis:** increases rightward
- **Y axis:** increases downward
- All positions are `[x, y]` integer arrays

---

## 5. Grid Encoding

The grid is a rectangular area enclosed by border walls. Interior contains:

- **Floor (`.`)** — walkable cells
- **Walls (`#`)** — impassable, including borders and shelf structures
- **Shelves** — walls that contain items; items are ON wall cells, bots pick up by standing on an adjacent walkable cell
- **Drop-off (`D`)** — walkable delivery point (single cell)

### Layout Pattern

Parallel vertical aisles, each 3 cells wide (shelf–walkway–shelf), connected by horizontal corridors at top and bottom.

### Walls Array

The `grid.walls` field is a flat array of `[x, y]` pairs representing ALL impassable cells (border walls + interior walls + shelf cells):

```json
"grid": {
  "width": 12,
  "height": 10,
  "walls": [[0,0], [1,0], [2,0], ..., [2,2], [6,2], ...]
}
```

**Verified:** Easy map has 52 wall cells in a 12×10 grid (120 total cells, 68 walkable).

---

## 6. Message Protocol

### 6.1 Server → Client: `game_state`

Sent every round. Contains complete world state (no fog of war).

```json
{
  "type": "game_state",
  "round": 0,
  "max_rounds": 300,
  "grid": {
    "width": 12,
    "height": 10,
    "walls": [[0,0], [1,0], [2,0], [3,0], [4,0], [5,0], [6,0], [7,0], [8,0], [9,0], [10,0], [11,0], [0,1], [11,1], ...]
  },
  "bots": [
    {"id": 0, "position": [10, 8], "inventory": []}
  ],
  "items": [
    {"id": "item_0",  "type": "cheese", "position": [3, 2]},
    {"id": "item_1",  "type": "butter", "position": [5, 2]},
    {"id": "item_2",  "type": "yogurt", "position": [3, 3]},
    {"id": "item_3",  "type": "milk",   "position": [5, 3]},
    {"id": "item_15", "type": "butter", "position": [9, 6]}
  ],
  "orders": [
    {"id": "order_0", "items_required": ["yogurt","yogurt","butter","milk"], "items_delivered": [], "complete": false, "status": "active"},
    {"id": "order_1", "items_required": ["yogurt","butter","milk","cheese"], "items_delivered": [], "complete": false, "status": "preview"}
  ],
  "drop_off": [1, 8],
  "score": 0,
  "active_order_index": 0,
  "total_orders": 50
}
```

#### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"game_state"` | Always `"game_state"` |
| `round` | int | Current round number (0-indexed) |
| `max_rounds` | int | Always 300 |
| `grid.width` | int | Grid width in cells |
| `grid.height` | int | Grid height in cells |
| `grid.walls` | `int[][]` | Array of `[x, y]` wall positions |
| `bots[].id` | int | Bot ID (0-indexed, resolves in this order) |
| `bots[].position` | `[x, y]` | Current position |
| `bots[].inventory` | `string[]` | Item type names currently held (max 3) |
| `items[].id` | string | Unique item ID (e.g. `"item_0"`) |
| `items[].type` | string | Item type name (e.g. `"milk"`) |
| `items[].position` | `[x, y]` | Position on shelf (this IS a wall cell) |
| `orders[].id` | string | Unique order ID |
| `orders[].items_required` | `string[]` | Item types needed (may have duplicates) |
| `orders[].items_delivered` | `string[]` | Item types already delivered |
| `orders[].complete` | bool | Whether order is fully delivered |
| `orders[].status` | `"active" \| "preview"` | Active = can deliver; Preview = next up |
| `drop_off` | `[x, y]` | Drop-off cell position (walkable) |
| `score` | int | Current score |
| `active_order_index` | int | Index of the active order in the full order list |
| `total_orders` | int | Total number of orders in the game (Easy: 50) |

### 6.2 Client → Server: `actions`

Must respond within **2 seconds** per round. Provide one action per bot.

```json
{
  "actions": [
    {"bot": 0, "action": "move_left"},
    {"bot": 1, "action": "pick_up", "item_id": "item_3"},
    {"bot": 2, "action": "drop_off"}
  ]
}
```

#### `actions[]` Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `bot` | int | Yes | Bot ID |
| `action` | string | Yes | One of the 7 action types |
| `item_id` | string | Only for `pick_up` | ID of the item to pick up |

### 6.3 Server → Client: `game_over`

Sent when the game ends (all rounds played, timeout, or disconnect).

```json
{
  "type": "game_over",
  "score": 47,
  "rounds_used": 200,
  "items_delivered": 22,
  "orders_completed": 5
}
```

---

## 7. Actions

### 7.1 Movement Actions

| Action | Direction | Position Change |
|--------|-----------|----------------|
| `move_up` | Up | `(x, y-1)` |
| `move_down` | Down | `(x, y+1)` |
| `move_left` | Left | `(x-1, y)` |
| `move_right` | Right | `(x+1, y)` |

**Failure conditions (silent, treated as `wait`):**
- Target cell is a wall, shelf, or out of bounds
- Target cell is occupied by another bot
- **Target cell has an item on it** (items are on shelf/wall cells, not walkable)

### 7.2 `pick_up`

- Bot must be **adjacent** (Manhattan distance = 1) to the item's shelf cell
- Bot inventory must not be full (< 3 items)
- `item_id` must match an item on the map
- Can pick ANY item regardless of current order
- Bad picks (non-matching items) waste inventory slots

### 7.3 `drop_off`

- Bot must be **standing on** the drop-off cell
- Bot must have items in inventory
- Only items matching the **active** order are consumed (+1 point each)
- Non-matching items **stay in inventory** (not consumed)
- If order completes: +5 bonus, preview becomes active, new preview appears
- **Auto-delivery:** When an order completes, remaining bot inventory matching the NEW active order is automatically delivered

### 7.4 `wait`

- Bot does nothing this round
- Invalid actions are treated as `wait`

---

## 8. Action Resolution Order

Actions resolve in **bot ID order** (bot 0 first, then bot 1, etc.). This means:
- If bot 0 moves to cell C and bot 1 also moves to cell C, bot 0 succeeds and bot 1 is blocked
- Spawn tile is exempt from collision at game start

---

## 9. Scoring

```
score = (items_delivered × 1) + (orders_completed × 5)
```

| Component | Points |
|-----------|--------|
| Each item delivered | +1 |
| Each order completed | +5 bonus |

### Leaderboard

- Sum of best scores across all 4 difficulty maps
- Play as many times as desired (10s cooldown between games)
- Deterministic within a day — same algorithm = same score

### Example Scores

| Scenario | Items | Orders | Score |
|----------|-------|--------|-------|
| 3 items, 0 complete | 3 | 0 | 3 |
| 4 items, 1 complete | 4 | 1 | 9 |
| 15 items, 3 complete | 15 | 3 | 30 |
| 50 items, 10 complete | 50 | 10 | 100 |

---

## 10. Timing & Limits

| Constraint | Value |
|------------|-------|
| Max rounds per game | 300 |
| Wall-clock time limit | 120 seconds |
| Per-round response timeout | 2 seconds |
| Timeout consequence | All bots wait that round |
| Cooldown between games | 10 seconds |
| Bot inventory capacity | 3 items |
| Max WebSocket message size | Not specified (1MB safe) |

---

## 11. Gotchas & Implementation Notes

### 11.1 Items Are On Wall Cells

**Critical:** Item positions are on shelf (wall) cells. They are **not walkable**. The bot must stand on an adjacent walkable cell to pick up. Pathfinding must treat item positions as impassable.

### 11.2 Daily Rotation

Maps are deterministic within a day. Items and orders change daily at midnight UTC. Grid structure stays the same. Same algorithm = same score within a day.

### 11.3 Order Transition Auto-Delivery

When an active order completes, remaining inventory that matches the NEW active order is automatically delivered. This means surplus items of the right type can score without explicit drop-off.

### 11.4 Drop-off Keeps Non-Matching Items

`drop_off` does NOT empty the entire inventory. Only items matching the active order are consumed. Non-matching items remain in inventory and must be delivered when their order becomes active.

### 11.5 Duplicate Items in Orders

Orders can require duplicates (e.g., `["yogurt", "yogurt", "butter", "milk"]`). Track count, not just type.

### 11.6 Preview Items Strategy

Preview items are visible but can't be delivered until the preview order becomes active. Pre-picking preview items saves time but costs inventory slots.

### 11.7 Collision Resolution

No two bots on the same cell (except spawn at start). Actions resolve by bot ID order. Lower-ID bots get priority on contested cells.

### 11.8 Connection URLs

- Production: `wss://game.ainm.no/ws?token=<jwt>`
- Development: `wss://game-dev.ainm.no/ws?token=<jwt>`
- Health check: `https://game.ainm.no/health` → `{"status":"ok"}`

---

## 12. Sample Messages

### 12.1 Full `game_state` (Easy, Round 0)

```json
{
  "type": "game_state",
  "round": 0,
  "max_rounds": 300,
  "grid": {
    "width": 12,
    "height": 10,
    "walls": [
      [0,0],[1,0],[2,0],[3,0],[4,0],[5,0],[6,0],[7,0],[8,0],[9,0],[10,0],[11,0],
      [0,1],[11,1],
      [0,2],[2,2],[6,2],[10,2],[11,2],
      [0,3],[2,3],[6,3],[10,3],[11,3],
      [0,4],[2,4],[6,4],[10,4],[11,4],
      [0,5],[11,5],
      [0,6],[2,6],[6,6],[10,6],[11,6],
      [0,7],[11,7],
      [0,8],[11,8],
      [0,9],[1,9],[2,9],[3,9],[4,9],[5,9],[6,9],[7,9],[8,9],[9,9],[10,9],[11,9]
    ]
  },
  "bots": [
    {"id": 0, "position": [10, 8], "inventory": []}
  ],
  "items": [
    {"id": "item_0",  "type": "cheese", "position": [3, 2]},
    {"id": "item_1",  "type": "butter", "position": [5, 2]},
    {"id": "item_2",  "type": "yogurt", "position": [3, 3]},
    {"id": "item_3",  "type": "milk",   "position": [5, 3]},
    {"id": "item_4",  "type": "cheese", "position": [3, 4]},
    {"id": "item_5",  "type": "butter", "position": [5, 4]},
    {"id": "item_6",  "type": "yogurt", "position": [7, 2]},
    {"id": "item_7",  "type": "milk",   "position": [9, 2]},
    {"id": "item_8",  "type": "cheese", "position": [7, 3]},
    {"id": "item_9",  "type": "butter", "position": [9, 3]},
    {"id": "item_10", "type": "yogurt", "position": [7, 4]},
    {"id": "item_11", "type": "milk",   "position": [9, 4]},
    {"id": "item_12", "type": "butter", "position": [3, 6]},
    {"id": "item_13", "type": "yogurt", "position": [5, 6]},
    {"id": "item_14", "type": "milk",   "position": [7, 6]},
    {"id": "item_15", "type": "butter", "position": [9, 6]}
  ],
  "orders": [
    {"id": "order_0", "items_required": ["yogurt","yogurt","butter","milk"], "items_delivered": [], "complete": false, "status": "active"},
    {"id": "order_1", "items_required": ["yogurt","butter","milk","cheese"], "items_delivered": [], "complete": false, "status": "preview"}
  ],
  "drop_off": [1, 8],
  "score": 0,
  "active_order_index": 0,
  "total_orders": 50
}
```

### 12.2 Full `actions` Response

```json
{
  "actions": [
    {"bot": 0, "action": "move_up"}
  ]
}
```

### 12.3 Multi-Bot `actions` Response (Medium+)

```json
{
  "actions": [
    {"bot": 0, "action": "move_left"},
    {"bot": 1, "action": "pick_up", "item_id": "item_7"},
    {"bot": 2, "action": "drop_off"}
  ]
}
```

### 12.4 `game_over`

```json
{
  "type": "game_over",
  "score": 10,
  "rounds_used": 300,
  "items_delivered": 5,
  "orders_completed": 1
}
```

---

## 13. ASCII Grid Visualization (Easy Map, Seed 7001)

```
# # # # # # # # # # # #    y=0  (border)
# . . . . . . . . . . #    y=1
# . # i i # . # i i # #    y=2  (shelves with items)
# . # i i # . # i i # #    y=3
# . # i i # . # i i # #    y=4
# . . . . . . . . . . #    y=5  (corridor)
# . # i i # . # i i # #    y=6  (shelves with items)
# . . . . . . . . . . #    y=7
# . . . . . . . . . . #    y=8  (D=drop-off at 1,8; B=bot at 10,8)
# # # # # # # # # # # #    y=9  (border)
```

Legend: `#` = wall, `.` = walkable, `i` = item on shelf, `D` = drop-off, `B` = bot spawn
