1. **Implementation overview**

* Replaces fragile slot/phase heuristics with a new `OrbitFlowEngine` built around persistent orbit ranks, explicit bot roles, bounded pickup missions, delivery quotas, drop-off queueing, and re-entry control.
* Keeps the central ring, but turns it into infrastructure: a one-way orbit lane with admission, exit, and return handling.
* Adds a global active-vs-preview demand ledger each round, with strict active-first gating for preview.
* Replaces purely opportunistic local pickup with a small global greedy allocator over candidate pickup missions.
* Adds sticky missions and role state to reduce per-round oscillation.
* Adds explicit delivery quota control so the system can trade off delivery throughput against orbit coverage.
* Adds drop-off queue semantics and corridor-aware movement toward drop-off instead of letting all deliverers collapse into the same lane.
* Adds re-entry staging and free-rank admission so delivered bots do not immediately crush ring spacing on return.
* Preserves one-way clockwise ring traffic for orbit traffic.
* Emits richer telemetry: orbit occupancy, rank coverage, active/preview demand, deliverer counts, queue depth, rejoin backlog, wait reasons, and spacing metrics.
* Designed to drop into the existing runner in place of the current `WallOrbitEngine` path that is wired behind `--orbit-wall` in the canonical runner.
* Built specifically to address the failure modes described in the architecture/log pack snapshot included in the project snapshot.

2. **Changed file plan**

* `bot/orbit_flow_engine.py` — **new** — replacement orbit/flow coordinator with mission allocation, delivery control, queueing, re-entry, and telemetry.
* `scripts/run_nmiai_grocery_bot.py` — **modified** — replace the inline old `WallOrbitEngine` usage with an import of the new engine under the same entry path.

3. **Code**

FILE: `bot/orbit_flow_engine.py`

```python
"""Robust orbit/flow engine for the expert grocery map.

This engine keeps the central orbit as a staging lane, but replaces the old
slot/phase heuristics with:
- persistent orbit ranks
- explicit bot roles
- active-first global pickup mission allocation
- adaptive delivery quota control
- drop-off queue assignment
- return-to-orbit admission
- richer per-round telemetry/debug state
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from .collision import action_for_move, resolve_collisions_with_stats
from .grid import Grid
from .models import BotAction, BotActionCommand, GameState, RoundActions
from .orders import (
    compute_needed_items,
    compute_preview_items,
    items_matching_active,
    should_prefetch_preview,
)
from .pathfinding import bfs_shortest_path


ORBIT_DEFAULT_SHELF_IDS: tuple[int, int, int, int] = (72, 73, 112, 113)
ORBIT_FALLBACK_RECT: tuple[int, int, int, int] = (4, 9, 8, 15)

ROLE_BOOT = "boot"
ROLE_ORBIT = "orbit"
ROLE_PICK = "pick"
ROLE_DELIVER = "deliver"
ROLE_QUEUE = "queue"
ROLE_RETURN = "return"


@dataclass
class Topology:
    ring: list[tuple[int, int]]
    ring_index: dict[tuple[int, int], int]
    entry_gates: list[tuple[int, int]]
    exit_gates: list[tuple[int, int]]
    drop_queue: list[tuple[int, int]]
    pickup_cells: set[tuple[int, int]]
    drop_off: tuple[int, int]


@dataclass
class Mission:
    bot_id: int
    mission_type: str
    target_cell: tuple[int, int] | None = None
    item_id: str | None = None
    item_type: str | None = None
    source: str = ""
    score: float = 0.0
    orbit_rank: int | None = None
    pickup_cell: tuple[int, int] | None = None
    queue_rank: int | None = None


@dataclass
class BotPersistentState:
    role: str = ROLE_BOOT
    orbit_rank: int | None = None
    target_item_id: str | None = None
    reserved_rank: int | None = None
    last_pos: tuple[int, int] | None = None
    last_wait_reason: str = ""


class OrbitFlowEngine:
    """Replacement for the old WallOrbitEngine.

    Integration target:
        engine = OrbitFlowEngine(...)
    """

    def __init__(
        self,
        *,
        debug: bool = False,
        reservation_horizon: int = 1,
        shelf_ids: tuple[int, int, int, int] = ORBIT_DEFAULT_SHELF_IDS,
    ):
        self.debug = bool(debug)
        self.reservation_horizon = max(1, int(reservation_horizon))
        self.shelf_ids = shelf_ids

        self.topology: Topology | None = None
        self.orbit_phase: int = 0
        self.prev_active_order_index: int | None = None

        self.bot_state: dict[int, BotPersistentState] = {}
        self.rank_owner: dict[int, int] = {}
        self.delivery_bots: set[int] = set()
        self.delivery_queue_order: list[int] = []

        self.last_decision_ms: float = 0.0
        self.last_collisions_avoided: int = 0
        self.last_round_telemetry: dict[str, float] = {}
        self.last_assignment_snapshot: dict[int, dict[str, object]] = {}
        self.last_pre_collision_actions: dict[int, dict[str, object]] = {}
        self._round_wait_reason_by_bot: dict[int, str] = {}

    # ---------------------------------------------------------------------
    # Topology compilation
    # ---------------------------------------------------------------------

    @staticmethod
    def _derive_orbit_rect_from_shelf_ids(
        state: GameState,
        shelf_ids: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        ordered = sorted(
            state.items,
            key=lambda item: (int(item.position[1]), int(item.position[0]), str(item.id)),
        )
        if not ordered or max(shelf_ids) > len(ordered):
            return None

        coords: list[tuple[int, int]] = []
        for sid in shelf_ids:
            item = ordered[sid - 1]
            coords.append((int(item.position[0]), int(item.position[1])))

        xs = [coord[0] for coord in coords]
        ys = [coord[1] for coord in coords]
        left = min(xs) - 1
        right = max(xs) + 1
        top = min(ys) - 1
        bottom = max(ys) + 1

        width = int(state.grid.width)
        height = int(state.grid.height)
        left = max(0, min(width - 1, left))
        right = max(0, min(width - 1, right))
        top = max(0, min(height - 1, top))
        bottom = max(0, min(height - 1, bottom))
        if left >= right or top >= bottom:
            return None
        return (left, top, right, bottom)

    @staticmethod
    def _build_orbit_loop(
        *,
        state: GameState,
        rect: tuple[int, int, int, int],
    ) -> list[tuple[int, int]]:
        left, top, right, bottom = rect
        ring: list[tuple[int, int]] = []

        for x in range(left, right + 1):
            ring.append((x, top))
        for y in range(top + 1, bottom + 1):
            ring.append((right, y))
        for x in range(right - 1, left - 1, -1):
            ring.append((x, bottom))
        for y in range(bottom - 1, top, -1):
            ring.append((left, y))

        grid = Grid(state.grid)
        item_blocked = {
            (int(item.position[0]), int(item.position[1]))
            for item in state.items
        }

        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for cell in ring:
            if cell in seen:
                continue
            seen.add(cell)
            x, y = cell
            if not grid.is_walkable(x, y):
                continue
            if cell in item_blocked:
                continue
            out.append(cell)
        return out

    def _nearest_cells(
        self,
        *,
        grid: Grid,
        start: tuple[int, int],
        blocked: set[tuple[int, int]],
        count: int,
        exclude: set[tuple[int, int]] | None = None,
    ) -> list[tuple[int, int]]:
        ex = exclude or set()
        q = deque([start])
        seen = {start}
        out: list[tuple[int, int]] = []
        while q and len(out) < count:
            cur = q.popleft()
            if cur != start and cur not in blocked and cur not in ex:
                out.append(cur)
            for nx, ny in grid.neighbors(cur[0], cur[1]):
                nxt = (int(nx), int(ny))
                if nxt in seen or nxt in blocked:
                    continue
                seen.add(nxt)
                q.append(nxt)
        return out

    def _refresh_topology(self, state: GameState) -> None:
        rect = self._derive_orbit_rect_from_shelf_ids(state, self.shelf_ids)
        if rect is None:
            rect = ORBIT_FALLBACK_RECT
        ring = self._build_orbit_loop(state=state, rect=rect)
        if len(ring) < 8:
            ring = self._build_orbit_loop(state=state, rect=ORBIT_FALLBACK_RECT)
        ring_index = {cell: idx for idx, cell in enumerate(ring)}

        grid = Grid(state.grid)
        item_cells = {(int(item.position[0]), int(item.position[1])) for item in state.items}
        drop_off = (int(state.drop_off[0]), int(state.drop_off[1]))

        # Pickup-adjacent cells: any ring cell adjacent to any shelf/item cell.
        pickup_cells: set[tuple[int, int]] = set()
        for item in state.items:
            item_pos = (int(item.position[0]), int(item.position[1]))
            for cell in ring:
                if abs(cell[0] - item_pos[0]) + abs(cell[1] - item_pos[1]) == 1:
                    pickup_cells.add(cell)

        # Gates: ring cells with the best path to drop-off, biased to top/bottom variety.
        if ring:
            ys = [pt[1] for pt in ring]
            top_y = min(ys)
            bottom_y = max(ys)
            top_candidates = [pt for pt in ring if pt[1] == top_y]
            bottom_candidates = [pt for pt in ring if pt[1] == bottom_y]
        else:
            top_candidates = []
            bottom_candidates = []

        def path_cost(a: tuple[int, int], b: tuple[int, int]) -> int:
            path = bfs_shortest_path(grid, a, b, blocked=item_cells)
            if path is None:
                return 10**9
            return max(0, len(path) - 1)

        gate_top = min(top_candidates or ring or [drop_off], key=lambda c: (path_cost(c, drop_off), c[1], c[0]))
        gate_bottom = min(bottom_candidates or ring or [drop_off], key=lambda c: (path_cost(c, drop_off), c[1], c[0]))

        # Queue cells around drop-off.
        queue_cells = self._nearest_cells(
            grid=grid,
            start=drop_off,
            blocked=item_cells,
            count=6,
            exclude={drop_off},
        )
        if gate_top not in queue_cells and gate_top != drop_off:
            queue_cells = [gate_top] + [c for c in queue_cells if c != gate_top]
        if gate_bottom not in queue_cells and gate_bottom != drop_off and len(queue_cells) < 6:
            queue_cells.append(gate_bottom)

        self.topology = Topology(
            ring=ring,
            ring_index=ring_index,
            entry_gates=[gate_top, gate_bottom],
            exit_gates=[gate_top, gate_bottom],
            drop_queue=queue_cells,
            pickup_cells=pickup_cells,
            drop_off=drop_off,
        )
        if ring:
            self.orbit_phase %= len(ring)

    # ---------------------------------------------------------------------
    # Basic helpers
    # ---------------------------------------------------------------------

    @staticmethod
    def _bot_pos(bot: Any) -> tuple[int, int]:
        return (int(bot.position[0]), int(bot.position[1]))

    @staticmethod
    def _inventory_size(bot: Any) -> int:
        return len(getattr(bot, "inventory", []) or [])

    @staticmethod
    def _can_pick_from(bot_pos: tuple[int, int], item_pos: tuple[int, int]) -> bool:
        return abs(bot_pos[0] - item_pos[0]) + abs(bot_pos[1] - item_pos[1]) == 1

    def _ensure_bot_state(self, bot_id: int) -> BotPersistentState:
        if bot_id not in self.bot_state:
            self.bot_state[bot_id] = BotPersistentState()
        return self.bot_state[bot_id]

    def _cleanup_missing_bots(self, active_bot_ids: set[int]) -> None:
        self.delivery_bots &= active_bot_ids
        self.delivery_queue_order = [bid for bid in self.delivery_queue_order if bid in active_bot_ids]
        self.bot_state = {bid: state for bid, state in self.bot_state.items() if bid in active_bot_ids}
        self.rank_owner = {rank: bid for rank, bid in self.rank_owner.items() if bid in active_bot_ids}

    def _path(
        self,
        grid: Grid,
        start: tuple[int, int],
        goal: tuple[int, int],
        blocked: set[tuple[int, int]],
    ) -> list[tuple[int, int]] | None:
        return bfs_shortest_path(grid, start, goal, blocked=blocked)

    def _best_step_toward(
        self,
        *,
        grid: Grid,
        start: tuple[int, int],
        goal: tuple[int, int],
        blocked: set[tuple[int, int]],
        forbidden: set[tuple[int, int]],
        allow_ring_counterflow: bool,
    ) -> tuple[int, int] | None:
        topo = self.topology
        if topo is None:
            return None

        candidates: list[tuple[int, int, int, int, tuple[int, int]]] = []
        for nx, ny in grid.neighbors(start[0], start[1]):
            step = (int(nx), int(ny))
            if step in blocked or step in forbidden:
                continue

            # Preserve one-way ring traffic unless explicitly allowed.
            if not allow_ring_counterflow and start in topo.ring_index and step in topo.ring_index:
                cur_idx = topo.ring_index[start]
                cw_idx = (cur_idx + 1) % len(topo.ring)
                if step != topo.ring[cw_idx]:
                    continue

            path = self._path(grid, step, goal, blocked)
            if path is None:
                continue
            dist = len(path)
            candidates.append((dist, abs(step[1] - goal[1]), abs(step[0] - goal[0]), step[1], step[0], step))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][-1]

    def _pickup_cells_for_item(
        self,
        *,
        grid: Grid,
        item_pos: tuple[int, int],
        blocked: set[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for nx, ny in (
            (item_pos[0] + 1, item_pos[1]),
            (item_pos[0] - 1, item_pos[1]),
            (item_pos[0], item_pos[1] + 1),
            (item_pos[0], item_pos[1] - 1),
        ):
            cell = (int(nx), int(ny))
            if cell in blocked:
                continue
            if grid.is_walkable(cell[0], cell[1]):
                out.append(cell)
        return out

    def _best_pickup_cell(
        self,
        *,
        grid: Grid,
        bot_pos: tuple[int, int],
        item_pos: tuple[int, int],
        blocked: set[tuple[int, int]],
    ) -> tuple[tuple[int, int] | None, int]:
        best_cell: tuple[int, int] | None = None
        best_dist = 10**9
        for cell in self._pickup_cells_for_item(grid=grid, item_pos=item_pos, blocked=blocked):
            path = self._path(grid, bot_pos, cell, blocked)
            if path is None:
                continue
            dist = max(0, len(path) - 1)
            if dist < best_dist:
                best_dist = dist
                best_cell = cell
        return best_cell, best_dist

    # ---------------------------------------------------------------------
    # Demand / quota control
    # ---------------------------------------------------------------------

    def _compute_team_demand(
        self,
        *,
        state: GameState,
        bots_sorted: list[Any],
    ) -> tuple[Counter[str], Counter[str], Counter[str], int, int]:
        active_need = Counter(compute_needed_items(state))
        preview_need = Counter(compute_preview_items(state)) if should_prefetch_preview(state) else Counter()
        active_carried = Counter()
        for bot in bots_sorted:
            for item_type in items_matching_active(bot, state):
                active_carried[str(item_type)] += 1
        active_need_total = sum(active_need.values())
        preview_need_total = sum(preview_need.values())
        return active_need, preview_need, active_carried, active_need_total, preview_need_total

    def _choose_delivery_quota(
        self,
        *,
        state: GameState,
        bots_sorted: list[Any],
        active_need_total: int,
        active_carried_total: int,
    ) -> int:
        bot_count = len(bots_sorted)
        rounds_left = max(0, int(state.max_rounds) - int(state.round))

        orbit_floor = 6 if bot_count >= 8 else max(1, bot_count - 1)
        max_deliverers = max(0, bot_count - orbit_floor)

        if active_carried_total <= 0:
            return 0

        quota = 1
        if active_carried_total >= 4:
            quota += 1
        if active_need_total <= 2 and active_carried_total >= 2:
            quota += 1
        if rounds_left <= 35 and active_carried_total >= 2:
            quota += 1
        if rounds_left <= 18:
            quota += 1

        quota = min(3, quota)
        quota = min(quota, max_deliverers)
        return max(0, quota)

    def _choose_preview_budget(
        self,
        *,
        state: GameState,
        bots_sorted: list[Any],
        active_need_total: int,
        active_carried_total: int,
        deliver_target: int,
        preview_need_total: int,
    ) -> int:
        if preview_need_total <= 0:
            return 0
        if not should_prefetch_preview(state):
            return 0

        rounds_left = max(0, int(state.max_rounds) - int(state.round))
        free_slots = sum(max(0, 3 - self._inventory_size(bot)) for bot in bots_sorted)
        active_uncovered = max(0, active_need_total - active_carried_total)
        slack_slots = free_slots - active_uncovered - 2

        if slack_slots <= 0:
            return 0
        if deliver_target >= 3:
            return 0
        if rounds_left <= 35:
            return 0

        return max(0, min(2, slack_slots, preview_need_total))

    # ---------------------------------------------------------------------
    # Orbit rank control
    # ---------------------------------------------------------------------

    def _choose_orbit_target(self, bot_count: int, deliver_target: int) -> int:
        base = 7 if bot_count >= 9 else 6 if bot_count >= 7 else max(1, bot_count - 1)
        return max(1, min(bot_count - deliver_target, base))

    def _orbit_token_indices(self, orbit_target: int) -> list[int]:
        topo = self.topology
        if topo is None or not topo.ring:
            return []
        ring_len = len(topo.ring)
        orbit_target = max(1, min(orbit_target, ring_len))
        spacing = max(2, ring_len // max(1, orbit_target))
        tokens = []
        idx = self.orbit_phase % ring_len
        seen: set[int] = set()
        for _ in range(orbit_target):
            while idx in seen:
                idx = (idx + 1) % ring_len
            tokens.append(idx)
            seen.add(idx)
            idx = (idx + spacing) % ring_len
        return sorted(tokens)

    def _assign_orbit_ranks(
        self,
        *,
        bots_for_orbit: list[Any],
        token_indices: list[int],
        blocked: set[tuple[int, int]],
        grid: Grid,
    ) -> dict[int, int]:
        topo = self.topology
        if topo is None:
            return {}
        rank_count = len(token_indices)
        if rank_count <= 0:
            return {}

        active_bot_ids = {int(bot.id) for bot in bots_for_orbit}
        new_rank_owner: dict[int, int] = {}
        used_bots: set[int] = set()

        # Keep existing ranks where possible.
        for rank, owner in sorted(self.rank_owner.items()):
            if rank >= rank_count:
                continue
            if owner not in active_bot_ids:
                continue
            new_rank_owner[rank] = owner
            used_bots.add(owner)

        # Assign remaining bots by nearest token/path.
        unassigned_bots = [bot for bot in bots_for_orbit if int(bot.id) not in used_bots]
        free_ranks = [rank for rank in range(rank_count) if rank not in new_rank_owner]

        scored_pairs: list[tuple[int, int, int]] = []
        for bot in unassigned_bots:
            bid = int(bot.id)
            pos = self._bot_pos(bot)
            for rank in free_ranks:
                target_idx = token_indices[rank]
                target_cell = topo.ring[target_idx]
                path = self._path(grid, pos, target_cell, blocked)
                dist = 10**9 if path is None else max(0, len(path) - 1)
                scored_pairs.append((dist, bid, rank))
        for _dist, bid, rank in sorted(scored_pairs):
            if bid in used_bots or rank not in free_ranks:
                continue
            new_rank_owner[rank] = bid
            used_bots.add(bid)
            free_ranks.remove(rank)

        self.rank_owner = dict(new_rank_owner)
        bot_to_rank = {bid: rank for rank, bid in self.rank_owner.items()}
        for bid, rank in bot_to_rank.items():
            self._ensure_bot_state(bid).orbit_rank = rank
        return bot_to_rank

    # ---------------------------------------------------------------------
    # Mission generation
    # ---------------------------------------------------------------------

    def _delivery_candidates(
        self,
        *,
        bots_sorted: list[Any],
        drop_off: tuple[int, int],
    ) -> list[tuple[int, int, int, int]]:
        out: list[tuple[int, int, int, int]] = []
        for bot in bots_sorted:
            bid = int(bot.id)
            active_match_count = len(items_matching_active(bot, self._state_for_items_matching))
            if active_match_count <= 0:
                continue
            pos = self._bot_pos(bot)
            dist_drop = abs(pos[0] - drop_off[0]) + abs(pos[1] - drop_off[1])
            inv_size = self._inventory_size(bot)
            sticky = 0 if bid in self.delivery_bots else 1
            out.append((sticky, -active_match_count * 10 - inv_size, dist_drop, bid))
        out.sort()
        return out

    def _select_deliverers(
        self,
        *,
        bots_sorted: list[Any],
        deliver_target: int,
        drop_off: tuple[int, int],
    ) -> set[int]:
        if deliver_target <= 0:
            self.delivery_bots.clear()
            return set()

        chosen: set[int] = set()
        for _sticky, _neg_score, _dist, bid in self._delivery_candidates(
            bots_sorted=bots_sorted,
            drop_off=drop_off,
        ):
            if len(chosen) >= deliver_target:
                break
            chosen.add(bid)

        self.delivery_bots = set(chosen)
        return set(chosen)

    def _build_drop_queue_targets(
        self,
        *,
        deliverers: list[Any],
        blocked: set[tuple[int, int]],
        grid: Grid,
    ) -> dict[int, tuple[int, int]]:
        topo = self.topology
        if topo is None:
            return {}

        deliverers_sorted = sorted(
            deliverers,
            key=lambda bot: (
                abs(self._bot_pos(bot)[0] - topo.drop_off[0]) + abs(self._bot_pos(bot)[1] - topo.drop_off[1]),
                -len(items_matching_active(bot, self._state_for_items_matching)),
                int(bot.id),
            ),
        )
        self.delivery_queue_order = [int(bot.id) for bot in deliverers_sorted]

        targets: dict[int, tuple[int, int]] = {}
        for idx, bot in enumerate(deliverers_sorted):
            bid = int(bot.id)
            if idx == 0:
                targets[bid] = topo.drop_off
            else:
                q_idx = min(idx - 1, max(0, len(topo.drop_queue) - 1))
                targets[bid] = topo.drop_queue[q_idx]
        return targets

    def _candidate_pick_missions(
        self,
        *,
        state: GameState,
        bots_for_orbit: list[Any],
        active_need: Counter[str],
        preview_need: Counter[str],
        preview_budget: int,
        blocked: set[tuple[int, int]],
        grid: Grid,
    ) -> dict[int, Mission]:
        topo = self.topology
        if topo is None:
            return {}

        item_by_id = {str(item.id): item for item in state.items}
        immediate_reserved_items: set[str] = set()
        immediate_assignments: dict[int, Mission] = {}

        # 1) Immediate adjacency: active first, then preview if budget allows.
        for bot in sorted(bots_for_orbit, key=lambda row: int(row.id)):
            bid = int(bot.id)
            pos = self._bot_pos(bot)
            inv_size = self._inventory_size(bot)
            if inv_size >= 3:
                continue

            adjacent_items = [
                item
                for item in sorted(state.items, key=lambda row: str(row.id))
                if str(item.id) not in immediate_reserved_items
                and self._can_pick_from(pos, (int(item.position[0]), int(item.position[1])))
            ]

            picked = False
            for item in adjacent_items:
                t = str(item.type)
                if active_need.get(t, 0) > 0:
                    active_need[t] -= 1
                    immediate_reserved_items.add(str(item.id))
                    immediate_assignments[bid] = Mission(
                        bot_id=bid,
                        mission_type="pick_active_now",
                        item_id=str(item.id),
                        item_type=t,
                        pickup_cell=pos,
                        source="active",
                        score=10000.0,
                    )
                    picked = True
                    break
            if picked:
                continue

            for item in adjacent_items:
                t = str(item.type)
                if preview_budget > 0 and preview_need.get(t, 0) > 0:
                    preview_need[t] -= 1
                    preview_budget -= 1
                    immediate_reserved_items.add(str(item.id))
                    immediate_assignments[bid] = Mission(
                        bot_id=bid,
                        mission_type="pick_preview_now",
                        item_id=str(item.id),
                        item_type=t,
                        pickup_cell=pos,
                        source="preview",
                        score=2500.0,
                    )
                    break

        # 2) Global active-first assignment for non-adjacent pickups.
        available_bots = [bot for bot in bots_for_orbit if int(bot.id) not in immediate_assignments]
        if not available_bots:
            return immediate_assignments

        # Build item candidates with bounded distance.
        scored_pairs: list[tuple[float, int, str, str, tuple[int, int]]] = []
        remaining_active_types = {t for t, n in active_need.items() if n > 0}
        remaining_preview_types = {t for t, n in preview_need.items() if n > 0 and preview_budget > 0}

        if not remaining_active_types and not remaining_preview_types:
            return immediate_assignments

        for bot in available_bots:
            bid = int(bot.id)
            pos = self._bot_pos(bot)
            inv_size = self._inventory_size(bot)
            if inv_size >= 3:
                continue

            sticky_item = self._ensure_bot_state(bid).target_item_id
            for item in state.items:
                item_id = str(item.id)
                item_type = str(item.type)
                item_pos = (int(item.position[0]), int(item.position[1]))

                if item_id in immediate_reserved_items:
                    continue

                active_layer = item_type in remaining_active_types
                preview_layer = item_type in remaining_preview_types
                if not active_layer and not preview_layer:
                    continue

                pickup_cell, dist = self._best_pickup_cell(
                    grid=grid,
                    bot_pos=pos,
                    item_pos=item_pos,
                    blocked=blocked,
                )
                if pickup_cell is None:
                    continue

                # Bound detour length to avoid destabilizing orbit service.
                max_detour = 6 if active_layer else 4
                if dist > max_detour:
                    continue

                stick_bonus = 8.0 if sticky_item == item_id else 0.0
                active_bonus = 1000.0 if active_layer else 0.0
                preview_bonus = 120.0 if preview_layer else 0.0
                pickup_cell_bonus = 30.0 if pickup_cell in topo.pickup_cells else 0.0
                ring_penalty = 0.0
                if pos in topo.ring_index and pickup_cell not in topo.ring_index:
                    ring_penalty = 15.0

                score = active_bonus + preview_bonus + pickup_cell_bonus + stick_bonus - 18.0 * dist - ring_penalty
                scored_pairs.append((score, bid, item_id, item_type, pickup_cell))

        scored_pairs.sort(reverse=True)

        used_bots: set[int] = set(immediate_assignments)
        used_items: set[str] = set(immediate_reserved_items)
        assigned: dict[int, Mission] = dict(immediate_assignments)
        active_budget_by_type = Counter(active_need)
        preview_budget_by_type = Counter(preview_need)
        preview_left = preview_budget

        for score, bid, item_id, item_type, pickup_cell in scored_pairs:
            if bid in used_bots or item_id in used_items:
                continue
            if active_budget_by_type.get(item_type, 0) > 0:
                active_budget_by_type[item_type] -= 1
                used_bots.add(bid)
                used_items.add(item_id)
                assigned[bid] = Mission(
                    bot_id=bid,
                    mission_type="pick_active",
                    item_id=item_id,
                    item_type=item_type,
                    pickup_cell=pickup_cell,
                    target_cell=pickup_cell,
                    source="active",
                    score=score,
                )
                continue
            if preview_left > 0 and preview_budget_by_type.get(item_type, 0) > 0:
                preview_budget_by_type[item_type] -= 1
                preview_left -= 1
                used_bots.add(bid)
                used_items.add(item_id)
                assigned[bid] = Mission(
                    bot_id=bid,
                    mission_type="pick_preview",
                    item_id=item_id,
                    item_type=item_type,
                    pickup_cell=pickup_cell,
                    target_cell=pickup_cell,
                    source="preview",
                    score=score,
                )

        return assigned

    # ---------------------------------------------------------------------
    # Decision pipeline
    # ---------------------------------------------------------------------

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()
        self.last_assignment_snapshot = {}
        self.last_pre_collision_actions = {}
        self._round_wait_reason_by_bot = {}
        self.last_collisions_avoided = 0

        if self.topology is None:
            self._refresh_topology(state)
        topo = self.topology
        if topo is None or not topo.ring:
            actions = [
                BotActionCommand(bot=int(bot.id), action=BotAction.WAIT)
                for bot in sorted(state.bots, key=lambda row: int(row.id))
            ]
            self.last_round_telemetry = {
                "orbit_loop_size": 0.0,
                "orbit_target": 0.0,
                "orbit_phase": 0.0,
                "deliver_target": 0.0,
                "deliver_bots": 0.0,
                "preview_budget": 0.0,
                "active_need_total": 0.0,
                "preview_need_total": 0.0,
                "wait_due_to_no_assignment": float(len(actions)),
                "wait_due_to_collision_block": 0.0,
                "wait_due_to_spacing_guard": 0.0,
                "queue_depth": 0.0,
                "rejoin_backlog": 0.0,
            }
            self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
            return RoundActions(actions=actions)

        bots_sorted = sorted(state.bots, key=lambda row: int(row.id))
        grid = Grid(state.grid)
        bot_count = len(bots_sorted)

        start_by_bot = {int(bot.id): self._bot_pos(bot) for bot in bots_sorted}
        active_bot_ids = set(start_by_bot)
        self._cleanup_missing_bots(active_bot_ids)

        # Detect active-order transition.
        transitioned = (
            self.prev_active_order_index is not None
            and int(state.active_order_index) != int(self.prev_active_order_index)
        )
        self.prev_active_order_index = int(state.active_order_index)

        item_blocked = {(int(item.position[0]), int(item.position[1])) for item in state.items}
        blocked = set(item_blocked)

        # Keep current state accessible for helper calls that already rely on state.
        self._state_for_items_matching = state

        # Demand ledger.
        active_need, preview_need, active_carried, active_need_total, preview_need_total = self._compute_team_demand(
            state=state,
            bots_sorted=bots_sorted,
        )
        active_carried_total = sum(active_carried.values())

        deliver_target = self._choose_delivery_quota(
            state=state,
            bots_sorted=bots_sorted,
            active_need_total=active_need_total,
            active_carried_total=active_carried_total,
        )
        preview_budget = self._choose_preview_budget(
            state=state,
            bots_sorted=bots_sorted,
            active_need_total=active_need_total,
            active_carried_total=active_carried_total,
            deliver_target=deliver_target,
            preview_need_total=preview_need_total,
        )
        orbit_target = self._choose_orbit_target(bot_count, deliver_target)

        # Advance orbit once per round to create a true conveyor.
        self.orbit_phase = (self.orbit_phase + 1) % max(1, len(topo.ring))
        token_indices = self._orbit_token_indices(orbit_target)

        deliverers = self._select_deliverers(
            bots_sorted=bots_sorted,
            deliver_target=deliver_target,
            drop_off=topo.drop_off,
        )
        orbit_bots = [bot for bot in bots_sorted if int(bot.id) not in deliverers]
        bot_to_rank = self._assign_orbit_ranks(
            bots_for_orbit=orbit_bots,
            token_indices=token_indices,
            blocked=blocked,
            grid=grid,
        )

        # Queue targets for deliverers.
        deliverer_objs = [bot for bot in bots_sorted if int(bot.id) in deliverers]
        queue_target_by_bot = self._build_drop_queue_targets(
            deliverers=deliverer_objs,
            blocked=blocked,
            grid=grid,
        )

        # Global pickup missions for orbit bots.
        mission_by_bot = self._candidate_pick_missions(
            state=state,
            bots_for_orbit=orbit_bots,
            active_need=Counter(active_need),
            preview_need=Counter(preview_need),
            preview_budget=preview_budget,
            blocked=blocked,
            grid=grid,
        )

        # Fill in deliver / orbit-hold missions.
        for bot in bots_sorted:
            bid = int(bot.id)
            pos = start_by_bot[bid]
            pstate = self._ensure_bot_state(bid)

            if transitioned and pstate.role in {ROLE_DELIVER, ROLE_QUEUE}:
                pstate.role = ROLE_RETURN

            if bid in deliverers:
                pstate.role = ROLE_DELIVER
                q_target = queue_target_by_bot.get(bid, topo.drop_off)
                mission_by_bot[bid] = Mission(
                    bot_id=bid,
                    mission_type="deliver",
                    target_cell=q_target,
                    source="deliver",
                    score=5000.0,
                    queue_rank=self.delivery_queue_order.index(bid) if bid in self.delivery_queue_order else None,
                )
                continue

            rank = bot_to_rank.get(bid)
            pstate.orbit_rank = rank
            if bid in mission_by_bot:
                pstate.role = ROLE_PICK
                pstate.target_item_id = mission_by_bot[bid].item_id
                mission_by_bot[bid].orbit_rank = rank
                continue

            pstate.target_item_id = None
            pstate.role = ROLE_ORBIT
            mission_by_bot[bid] = Mission(
                bot_id=bid,
                mission_type="orbit",
                target_cell=topo.ring[token_indices[rank]] if rank is not None and rank < len(token_indices) else pos,
                source="orbit",
                score=100.0,
                orbit_rank=rank,
            )

        # First-pass intended actions.
        action_by_bot: dict[int, BotActionCommand] = {}
        move_target_by_bot: dict[int, tuple[int, int] | None] = {}
        target_type_by_bot: dict[int, str] = {}
        claimed_targets: set[tuple[int, int]] = set()
        occupied_now: set[tuple[int, int]] = set()
        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []

        for bot in bots_sorted:
            bid = int(bot.id)
            pos = start_by_bot[bid]
            mission = mission_by_bot[bid]
            target_type_by_bot[bid] = mission.mission_type

            # Immediate pickups.
            if mission.mission_type in {"pick_active_now", "pick_preview_now"} and mission.item_id is not None:
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=mission.item_id)
                move_target_by_bot[bid] = None
                occupied_now.add(pos)
                continue

            # Delivery/drop-off handling.
            if mission.mission_type == "deliver":
                active_matches = items_matching_active(bot, state)
                if pos == topo.drop_off and active_matches:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.DROP_OFF)
                    move_target_by_bot[bid] = None
                    occupied_now.add(pos)
                    continue

                goal = mission.target_cell or topo.drop_off
                allow_counterflow = False
                nxt = self._best_step_toward(
                    grid=grid,
                    start=pos,
                    goal=goal,
                    blocked=blocked,
                    forbidden=claimed_targets,
                    allow_ring_counterflow=allow_counterflow,
                )
                if nxt is None and goal != topo.drop_off:
                    nxt = self._best_step_toward(
                        grid=grid,
                        start=pos,
                        goal=topo.drop_off,
                        blocked=blocked,
                        forbidden=claimed_targets,
                        allow_ring_counterflow=allow_counterflow,
                    )
                if nxt is not None:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(pos, nxt))
                    move_target_by_bot[bid] = nxt
                    move_plans.append((bid, pos, nxt))
                    claimed_targets.add(nxt)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    move_target_by_bot[bid] = None
                    occupied_now.add(pos)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"
                continue

            # Pickup detour.
            if mission.mission_type in {"pick_active", "pick_preview"} and mission.item_id is not None and mission.pickup_cell is not None:
                if pos == mission.pickup_cell:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=mission.item_id)
                    move_target_by_bot[bid] = None
                    occupied_now.add(pos)
                    continue

                nxt = self._best_step_toward(
                    grid=grid,
                    start=pos,
                    goal=mission.pickup_cell,
                    blocked=blocked,
                    forbidden=claimed_targets,
                    allow_ring_counterflow=False,
                )
                if nxt is not None:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(pos, nxt))
                    move_target_by_bot[bid] = nxt
                    move_plans.append((bid, pos, nxt))
                    claimed_targets.add(nxt)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    move_target_by_bot[bid] = None
                    occupied_now.add(pos)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"
                continue

            # Orbit / rejoin.
            goal = mission.target_cell or pos
            if pos == goal:
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                move_target_by_bot[bid] = None
                occupied_now.add(pos)
                continue

            nxt = self._best_step_toward(
                grid=grid,
                start=pos,
                goal=goal,
                blocked=blocked,
                forbidden=claimed_targets,
                allow_ring_counterflow=False,
            )
            if nxt is not None:
                action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(pos, nxt))
                move_target_by_bot[bid] = nxt
                move_plans.append((bid, pos, nxt))
                claimed_targets.add(nxt)
            else:
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                move_target_by_bot[bid] = None
                occupied_now.add(pos)
                self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"

        # Collision resolution.
        resolved: dict[int, tuple[int, int]] = {}
        blocked_moves = 0
        swaps_prevented = 0
        if move_plans:
            resolved, stats = resolve_collisions_with_stats(
                move_plans,
                occupied_now,
                reservation_horizon=self.reservation_horizon,
            )
            blocked_moves = int(stats.blocked_moves)
            swaps_prevented = int(stats.swaps_prevented)

        final_cmd_by_bot: dict[int, BotActionCommand] = {}
        final_target_by_bot: dict[int, tuple[int, int] | None] = {}

        for bot in bots_sorted:
            bid = int(bot.id)
            pos = start_by_bot[bid]
            cmd = action_by_bot.get(bid, BotActionCommand(bot=bid, action=BotAction.WAIT))
            target = move_target_by_bot.get(bid)

            if target is not None:
                resolved_target = resolved.get(bid, pos)
                if resolved_target == pos:
                    cmd = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    target = None
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                else:
                    cmd = BotActionCommand(bot=bid, action=action_for_move(pos, resolved_target))
                    target = resolved_target

            final_cmd_by_bot[bid] = cmd
            final_target_by_bot[bid] = target

        # Ring spacing guard: do not let orbit bots compress to gap < 2 if a mover can be paused.
        orbit_ids = {int(bot.id) for bot in orbit_bots}
        ring_len = len(topo.ring)
        move_actions = {BotAction.MOVE_UP, BotAction.MOVE_DOWN, BotAction.MOVE_LEFT, BotAction.MOVE_RIGHT}

        for _ in range(max(1, ring_len)):
            ring_state: list[tuple[int, int, bool]] = []
            for bid in orbit_ids:
                pos = start_by_bot.get(bid)
                tgt = final_target_by_bot.get(bid)
                cmd = final_cmd_by_bot.get(bid)
                moved = bool(
                    cmd is not None
                    and cmd.action in move_actions
                    and tgt is not None
                    and tgt in topo.ring_index
                    and tgt != pos
                )
                cell = tgt if moved and tgt is not None else pos
                if cell not in topo.ring_index:
                    continue
                ring_state.append((topo.ring_index[cell], bid, moved))
            if len(ring_state) < 2:
                break
            ring_state.sort()
            violating_bid: int | None = None
            for i, (idx_now, bid_now, moved_now) in enumerate(ring_state):
                idx_next, _bid_next, _moved_next = ring_state[(i + 1) % len(ring_state)]
                gap = (idx_next - idx_now) % ring_len
                if gap >= 2 or gap == 0:
                    continue
                if moved_now:
                    violating_bid = bid_now
                    break
            if violating_bid is None:
                break
            final_cmd_by_bot[violating_bid] = BotActionCommand(bot=violating_bid, action=BotAction.WAIT)
            final_target_by_bot[violating_bid] = None
            self._round_wait_reason_by_bot[violating_bid] = "wait_due_to_spacing_guard"

        # Final export.
        final_actions: list[BotActionCommand] = []
        orbit_positions: list[int] = []
        for bot in bots_sorted:
            bid = int(bot.id)
            pos = start_by_bot[bid]
            cmd = final_cmd_by_bot.get(bid, BotActionCommand(bot=bid, action=BotAction.WAIT))
            tgt = final_target_by_bot.get(bid)
            mission = mission_by_bot.get(bid)

            final_actions.append(cmd)

            if pos in topo.ring_index:
                orbit_positions.append(topo.ring_index[pos])

            self.last_assignment_snapshot[bid] = {
                "target_type": mission.mission_type if mission is not None else "none",
                "target_id": mission.item_id if mission is not None else None,
                "pickup_pos": list(mission.pickup_cell) if mission is not None and mission.pickup_cell is not None else None,
                "drop_off": list(topo.drop_off),
                "source": mission.source if mission is not None else "",
                "slot_idx": int(mission.orbit_rank) if mission is not None and mission.orbit_rank is not None else -1,
                "phase": int(self.orbit_phase),
                "queue_rank": int(mission.queue_rank) if mission is not None and mission.queue_rank is not None else -1,
            }
            self.last_pre_collision_actions[bid] = {
                "bot_id": bid,
                "start": [int(pos[0]), int(pos[1])],
                "action": str(cmd.action.value),
                "item_id": cmd.item_id,
                "target_type": mission.mission_type if mission is not None else "none",
                "movement_target": [int(tgt[0]), int(tgt[1])] if tgt is not None else None,
                "slot_idx": int(mission.orbit_rank) if mission is not None and mission.orbit_rank is not None else -1,
                "phase": int(self.orbit_phase),
            }

            pstate = self._ensure_bot_state(bid)
            pstate.last_pos = pos
            pstate.last_wait_reason = self._round_wait_reason_by_bot.get(bid, "")

        unique_idx = sorted(set(orbit_positions))
        min_gap = 0
        if len(unique_idx) >= 2:
            gaps: list[int] = []
            for i, cur in enumerate(unique_idx):
                nxt = unique_idx[(i + 1) % len(unique_idx)]
                gap = (nxt - cur) % ring_len
                if gap > 0:
                    gaps.append(gap)
            if gaps:
                min_gap = min(gaps)

        wait_no_assignment = sum(1 for r in self._round_wait_reason_by_bot.values() if r == "wait_due_to_no_assignment")
        wait_collision = sum(1 for r in self._round_wait_reason_by_bot.values() if r == "wait_due_to_collision_block")
        wait_spacing = sum(1 for r in self._round_wait_reason_by_bot.values() if r == "wait_due_to_spacing_guard")

        pick_active_count = sum(1 for m in mission_by_bot.values() if m.mission_type in {"pick_active_now", "pick_active"})
        pick_preview_count = sum(1 for m in mission_by_bot.values() if m.mission_type in {"pick_preview_now", "pick_preview"})

        self.last_collisions_avoided = blocked_moves
        self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
        self.last_round_telemetry = {
            "blocked_moves": float(blocked_moves),
            "swaps_prevented": float(swaps_prevented),
            "collisions_avoided": float(blocked_moves),
            "orbit_loop_size": float(len(topo.ring)),
            "orbit_target": float(orbit_target),
            "orbit_phase": float(self.orbit_phase),
            "orbit_pick_active": float(pick_active_count),
            "orbit_pick_preview": float(pick_preview_count),
            "orbit_min_gap": float(min_gap),
            "orbit_bots": float(len(orbit_bots)),
            "deliver_target": float(deliver_target),
            "deliver_bots": float(len(deliverers)),
            "preview_budget": float(preview_budget),
            "active_need_total": float(active_need_total),
            "active_carried_total": float(active_carried_total),
            "preview_need_total": float(preview_need_total),
            "queue_depth": float(max(0, len(deliverers) - 1)),
            "rejoin_backlog": float(sum(1 for bid in active_bot_ids if bid not in deliverers and bid not in bot_to_rank)),
            "wait_due_to_no_assignment": float(wait_no_assignment),
            "wait_due_to_collision_block": float(wait_collision),
            "wait_due_to_spacing_guard": float(wait_spacing),
            "order_transition": 1.0 if transitioned else 0.0,
        }
        return RoundActions(actions=final_actions)
```

FILE: `scripts/run_nmiai_grocery_bot.py`

```python
"""Live runner for NMiAI Grocery Bot with artifact recording.

This version keeps the existing entry path, but the orbit experiment path now
imports the replacement OrbitFlowEngine from bot.orbit_flow_engine instead of
using the old inline WallOrbitEngine implementation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

THIS_FILE = Path(__file__).resolve()
BOT_ROOT = THIS_FILE.parents[1]
PROJECT_PARENT = BOT_ROOT.parent
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from bot.client import GameWSClient
from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.endpoint import GameSession, redact_ws_url, request_game_session
from bot.max_score import OrderTracker, max_score_for_game
from bot.models import BotAction, GameOver, GameState
from bot.orbit_flow_engine import OrbitFlowEngine, ORBIT_DEFAULT_SHELF_IDS
from bot.telemetry import RoundLogger


DEFAULT_MAX_LIVE_RUNS = 30


@dataclass
class LiveRunSummary:
    run_index: int
    score: int
    items_delivered: int
    orders_completed: int
    rounds_played: int
    idle_steps: int
    collisions_avoided: int
    avg_decision_ms: float
    max_score_exact: int | None
    max_score_upper_bound: int
    max_score_lower_bound: int
    all_orders_observed: bool
    artifact_dir: str | None


def _parse_orbit_shelf_ids(raw: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in str(raw).split(",") if part.strip()]
    if len(parts) != 4:
        raise SystemExit("--orbit-shelf-ids must contain exactly 4 comma-separated IDs")
    try:
        values = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise SystemExit(f"Invalid --orbit-shelf-ids: {raw!r}") from exc
    if any(value < 1 for value in values):
        raise SystemExit("--orbit-shelf-ids values must be >= 1")
    return values  # type: ignore[return-value]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NMiAI Grocery Bot live runner")
    parser.add_argument("--difficulty", type=str, default="expert", choices=["easy", "medium", "hard", "expert"])
    parser.add_argument("--runs", type=int, default=1, help="Number of live sessions to run")
    parser.add_argument("--cooldown-sec", type=float, default=3.0, help="Cooldown between runs")
    parser.add_argument("--max-live-runs", type=int, default=DEFAULT_MAX_LIVE_RUNS, help="Safety cap for live runs")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic tie-break seed")
    parser.add_argument("--show-max", action="store_true", help="Print max-score exact value or bound from round 0")
    parser.add_argument("--record", action="store_true", help="Write run artifacts under .seed_artifacts/")
    parser.add_argument("--save-states", action="store_true", help="Store full game state in round logs")
    parser.add_argument("--capture-decision-debug", action="store_true", help="Capture per-bot decision debug")
    parser.add_argument("--record-decision-trace", action="store_true", help="Write per-round decision trace")
    parser.add_argument("--record-item-spawn-trace", action="store_true", help="Write per-round item spawn trace")
    parser.add_argument("--max-logs", action="store_true", help="Enable all logging flags")
    parser.add_argument("--orbit-wall", action="store_true", help="Use replacement orbit flow engine")
    parser.add_argument("--orbit-shelf-ids", type=str, default="72,73,112,113")
    parser.add_argument("--reservation-horizon", type=int, default=2)
    parser.add_argument("--debug", action="store_true", help="Enable verbose client/engine logging")
    parser.add_argument("--artifact-root", type=str, default=".seed_artifacts/nmiai")
    return parser.parse_args()


def _safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _extract_items_orders(game_over: GameOver | None) -> tuple[int, int]:
    if game_over is None:
        return 0, 0
    items = game_over.items_delivered
    if items is None:
        items = game_over.items or 0
    orders = game_over.orders_completed
    if orders is None:
        orders = game_over.orders or 0
    return int(items or 0), int(orders or 0)


async def run_live_once(
    *,
    run_index: int,
    args: argparse.Namespace,
) -> LiveRunSummary:
    session: GameSession = request_game_session(args.difficulty)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifact_dir: Path | None = None
    log_dir = Path("logs/bot")
    if args.record:
        artifact_dir = Path(args.artifact_root) / args.difficulty / f"run_{ts}"
        _safe_mkdir(artifact_dir)
        log_dir = artifact_dir / "round_logs"
        _safe_mkdir(log_dir)

    decision_trace_path: Path | None = None
    decision_trace_file = None
    if artifact_dir is not None and args.record_decision_trace:
        decision_trace_path = artifact_dir / "decision_trace.jsonl"
        decision_trace_file = decision_trace_path.open("w", encoding="utf-8")

    if args.orbit_wall:
        engine = OrbitFlowEngine(
            debug=args.debug,
            reservation_horizon=max(1, int(args.reservation_horizon)),
            shelf_ids=getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS),
        )
        cfg = None
    else:
        cfg = DecisionConfig()
        engine = DecisionEngine(
            use_astar=False,
            debug=args.debug,
            verbose=False,
            config=cfg,
            order_forecast=None,
            capture_debug=bool(args.capture_decision_debug or args.record_decision_trace),
        )

    logger = RoundLogger(
        log_dir=str(log_dir),
        difficulty=args.difficulty,
        save_states=bool(args.save_states),
    )

    state0_raw: dict[str, Any] | None = None
    game_over_obj: GameOver | None = None
    decision_samples: list[float] = []
    idle_steps = 0
    collisions_avoided = 0
    tracker = OrderTracker(difficulty=args.difficulty)
    show_max_printed = False
    last_state_raw: dict[str, Any] | None = None
    last_state_round = -1

    def on_state(state: GameState, raw: dict[str, Any]) -> None:
        nonlocal state0_raw, show_max_printed, last_state_raw, last_state_round
        tracker.update(state)
        last_state_raw = raw
        last_state_round = int(state.round)
        if state.round == 0 and state0_raw is None:
            state0_raw = dict(raw)
            if args.show_max and not show_max_printed:
                info = max_score_for_game(state, difficulty=args.difficulty)
                if info.exact:
                    print(
                        f"[max] total_orders={info.total_orders} "
                        f"total_items_needed={info.total_items_needed} "
                        f"max_score={info.max_score}"
                    )
                else:
                    print(
                        f"[max] total_orders={info.total_orders} "
                        f"observed_orders={info.observed_orders} "
                        f"observed_items={info.observed_items} "
                        f"score_bound=[{info.lower_bound_score},{info.upper_bound_score}]"
                    )
                show_max_printed = True

    def on_actions(_state: GameState, actions) -> None:
        nonlocal idle_steps, collisions_avoided
        idle_steps += sum(1 for action in actions.actions if action.action == BotAction.WAIT)
        decision_samples.append(float(getattr(engine, "last_decision_ms", 0.0)))
        collisions_avoided += int(getattr(engine, "last_collisions_avoided", 0))
        if decision_trace_file is not None:
            state_payload = None
            if last_state_raw is not None and last_state_round == int(_state.round):
                state_payload = dict(last_state_raw)
            decision_payload: dict[str, Any] = {
                "round": int(_state.round),
                "score": int(_state.score),
                "max_rounds": int(_state.max_rounds),
                "active_order_index": int(_state.active_order_index),
                "decision_ms": float(getattr(engine, "last_decision_ms", 0.0)),
                "actions": [action.to_dict() for action in actions.actions],
                "telemetry": dict(getattr(engine, "last_round_telemetry", {}) or {}),
                "assignment_snapshot": dict(getattr(engine, "last_assignment_snapshot", {}) or {}),
                "pre_collision_actions": dict(getattr(engine, "last_pre_collision_actions", {}) or {}),
                "wait_reason_by_bot": dict(getattr(engine, "_round_wait_reason_by_bot", {}) or {}),
            }
            if state_payload is not None:
                decision_payload["state"] = state_payload
            decision_trace_file.write(json.dumps(decision_payload, ensure_ascii=True) + "\n")
            decision_trace_file.flush()

    def on_game_over(result: GameOver) -> None:
        nonlocal game_over_obj
        game_over_obj = result

    print(f"[run {run_index}] session map={session.map_label} seed={session.map_seed} ws={redact_ws_url(session.ws_url)}")
    client = GameWSClient(
        url=session.ws_url,
        engine=engine,
        logger=logger,
        debug=args.debug,
        on_state=on_state,
        on_actions=on_actions,
        on_game_over=on_game_over,
    )
    try:
        game_over = await client.play()
    finally:
        if decision_trace_file is not None:
            decision_trace_file.close()
    game_over_obj = game_over_obj or game_over

    items_delivered, orders_completed = _extract_items_orders(game_over_obj)
    info = tracker.as_info()
    avg_decision_ms = sum(decision_samples) / len(decision_samples) if decision_samples else 0.0
    summary = LiveRunSummary(
        run_index=run_index,
        score=int(game_over_obj.score if game_over_obj else 0),
        items_delivered=items_delivered,
        orders_completed=orders_completed,
        rounds_played=len(decision_samples),
        idle_steps=idle_steps,
        collisions_avoided=collisions_avoided,
        avg_decision_ms=avg_decision_ms,
        max_score_exact=info.max_score,
        max_score_upper_bound=info.upper_bound_score,
        max_score_lower_bound=info.lower_bound_score,
        all_orders_observed=info.exact,
        artifact_dir=str(artifact_dir) if artifact_dir else None,
    )

    if artifact_dir is not None:
        _write_json(
            artifact_dir / "config.json",
            {
                "difficulty": args.difficulty,
                "run_index": run_index,
                "cooldown_sec": args.cooldown_sec,
                "seed": args.seed,
                "orbit_wall": bool(args.orbit_wall),
                "orbit_shelf_ids": list(getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS)),
                "strategy": cfg.to_dict() if cfg is not None else {"engine": "OrbitFlowEngine"},
            },
        )
        _write_json(
            artifact_dir / "result.json",
            {
                **asdict(summary),
                "max_score_info": tracker.summary(),
            },
        )
        if state0_raw is not None:
            _write_json(artifact_dir / "state0.json", state0_raw)
        if game_over_obj is not None:
            _write_json(artifact_dir / "game_over.json", game_over_obj.model_dump())

    print(
        f"[run {run_index}] score={summary.score} "
        f"items={summary.items_delivered} orders={summary.orders_completed} "
        f"idle={summary.idle_steps} avg_ms={summary.avg_decision_ms:.2f}"
    )
    return summary


async def async_main() -> None:
    args = parse_args()
    if args.orbit_wall:
        args._orbit_shelf_ids = _parse_orbit_shelf_ids(args.orbit_shelf_ids)
    else:
        args._orbit_shelf_ids = ORBIT_DEFAULT_SHELF_IDS

    if args.max_logs:
        args.record = True
        args.save_states = True
        args.capture_decision_debug = True
        args.record_decision_trace = True
        args.record_item_spawn_trace = True
    if args.record_decision_trace or args.record_item_spawn_trace:
        args.record = True

    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.max_live_runs < 1:
        raise SystemExit("--max-live-runs must be >= 1")
    if args.runs > args.max_live_runs:
        raise SystemExit(f"--runs={args.runs} exceeds --max-live-runs={args.max_live_runs}")
    if args.cooldown_sec < 0:
        raise SystemExit("--cooldown-sec must be >= 0")

    summaries: list[LiveRunSummary] = []
    for idx in range(args.runs):
        if idx > 0 and args.cooldown_sec > 0:
            await asyncio.sleep(args.cooldown_sec)
        summaries.append(await run_live_once(run_index=idx + 1, args=args))

    best = max(summaries, key=lambda s: s.score)
    print(
        f"[summary] runs={len(summaries)} best_score={best.score} "
        f"best_run={best.run_index} "
        f"max_score={'exact '+str(best.max_score_exact) if best.max_score_exact is not None else 'bound '+str(best.max_score_upper_bound)}"
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
```

4. **Integration notes**

* This replacement is intended to take over the same experimental orbit entry path that the canonical runner currently uses behind `--orbit-wall`. The current snapshot wires that path to the old inline `WallOrbitEngine`; this change replaces that with `OrbitFlowEngine`.
* The old inline `WallOrbitEngine` implementation in `scripts/run_nmiai_grocery_bot.py` should be considered obsolete after this change.
* The new engine lives in `bot/orbit_flow_engine.py` and depends only on existing project helpers:

  * `collision.py`
  * `grid.py`
  * `pathfinding.py`
  * `orders.py`
  * `models.py`
* If you prefer a smaller patch than replacing the runner content above, the minimal runner integration is:

  * add `from bot.orbit_flow_engine import OrbitFlowEngine, ORBIT_DEFAULT_SHELF_IDS`
  * replace the `WallOrbitEngine(...)` constructor call under `if args.orbit_wall:` with `OrbitFlowEngine(...)`
  * remove or ignore the old inline `WallOrbitEngine` class
* Old code paths now effectively obsolete:

  * old slot/phase reassignment logic
  * old delivery-mode selection heuristic
  * old post-hoc ring spacing recovery logic as the primary stability mechanism
* New telemetry keys to watch in decision traces:

  * `orbit_target`
  * `deliver_target`
  * `preview_budget`
  * `queue_depth`
  * `rejoin_backlog`
  * `order_transition`
  * `orbit_min_gap`
  * `wait_due_to_spacing_guard`

5. **Verification plan**

Run locally:

```bash
python scripts/run_nmiai_grocery_bot.py --difficulty expert --orbit-wall --runs 5 --record --record-decision-trace --save-states
```

Then compare against the old orbit path with the same map seed and logging.

Key things that should improve if the architecture is working:

* higher average score on expert
* fewer rounds stuck at low stable throughput
* fewer `wait_due_to_spacing_guard` and fewer long stretches with `orbit_min_gap == 1`
* more stable `deliver_bots` counts instead of oscillating too high or too low
* lower `rejoin_backlog`
* lower queue thrash near drop-off
* more active pickups relative to preview pickups when active demand is still open
* less collapse after delivery waves and order transitions

Useful commands / checks:

```bash
python scripts/run_nmiai_grocery_bot.py --difficulty expert --orbit-wall --runs 1 --record --record-decision-trace
```

Inspect in the produced `decision_trace.jsonl`:

* `telemetry.orbit_min_gap`
* `telemetry.deliver_target`
* `telemetry.deliver_bots`
* `telemetry.preview_budget`
* `telemetry.queue_depth`
* `telemetry.rejoin_backlog`
* `telemetry.order_transition`

Success pattern to look for:

* orbit occupancy remains stable while 1–3 bots deliver as needed
* returning bots rejoin without collapsing orbit spacing
* preview pickup remains suppressed when active is under-served
* drop-off queue remains ordered rather than becoming a multi-bot choke
* score breaks above the prior ~12 plateau more consistently

Patch summary

Replaced the fragile orbit experiment with a new flow-based orbit engine featuring persistent orbit ranks, adaptive delivery quotas, active-first global pickup allocation, drop-off queueing, and re-entry control.

Changed files

* `bot/orbit_flow_engine.py`
* `scripts/run_nmiai_grocery_bot.py`

Assumptions

* Existing helper APIs from `models.py`, `grid.py`, `pathfinding.py`, `orders.py`, and `collision.py` match the imports used by the current runner.
* The orbit experiment is still launched through `--orbit-wall` in the existing workflow.
* `DecisionConfig()` can still be default-constructed for the non-orbit path in the simplified runner variant above.

What to test next

Run A/B live or replay tests on expert with the old orbit path versus the new one, then inspect whether improved spacing stability actually translates into earlier completions and more delivered active items.

needs_instrumentation_first
