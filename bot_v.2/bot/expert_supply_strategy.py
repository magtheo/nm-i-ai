"""Role/phase expert supply strategy."""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

from .collision import action_for_move, resolve_collisions_with_stats
from .grid import Grid
from .models import BotAction, BotActionCommand, BotInfo, GameState, ItemInfo, RoundActions
from .orders import get_active_order, get_preview_order
from .pathfinding import bfs_shortest_path

ROLE_COURIER = "courier"
ROLE_HARVESTER = "harvester"
ROLE_FLEX = "flex"

STATE_BOOT = "BOOT"
STATE_HARVEST_ACTIVE = "HARVEST_ACTIVE"
STATE_DELIVER_ACTIVE = "DELIVER_ACTIVE"
STATE_HARVEST_PREVIEW = "HARVEST_PREVIEW"
STATE_DELIVER_PREVIEW_READY = "DELIVER_PREVIEW_READY"
STATE_FLEX_HUNT = "FLEX_HUNT"
STATE_QUEUE_DROP = "QUEUE_DROP"

PHASE_BOOT = "boot"
PHASE_ACTIVE = "active"
PHASE_CRITICAL = "critical"
PHASE_TRANSITION = "transition"
PHASE_POST = "post_transition"

CLUSTER_UPPER = "upper"
CLUSTER_LOWER = "lower"
CLUSTER_CENTER = "center"
CLUSTER_DISPATCH = "dispatch"


@dataclass(frozen=True)
class ShelfRef:
    item_id: str
    pos: tuple[int, int]


@dataclass(frozen=True)
class TargetChoice:
    item_id: str
    item_type: str
    pickup_pos: tuple[int, int]
    source: str


class ExpertSupplyStrategyEngine:
    def __init__(self, *, debug: bool = False, reservation_horizon: int = 1):
        self.debug = bool(debug)
        self.reservation_horizon = max(1, int(reservation_horizon))
        self._type_to_shelves: dict[str, list[ShelfRef]] = {}
        self._indexed_ids: set[str] = set()
        self._roles: dict[int, str] = {}
        self._cluster_pref_by_bot: dict[int, str] = {}
        self._cluster_mid_y: int = 0
        self._last_active_order_id: str | None = None
        self._last_transition_round = -999999

        self.last_decision_ms = 0.0
        self.last_collisions_avoided = 0
        self.last_round_telemetry: dict[str, float] = {}
        self.last_round_debug: dict[str, Any] = {}
        self.last_assignment_snapshot: dict[int, dict[str, Any]] = {}
        self.last_pre_collision_actions: dict[int, dict[str, Any]] = {}
        self._round_wait_reason_by_bot: dict[int, str] = {}
        self._queue_hold_burst_decay: int = 0
        self._queue_relaxed_rounds: int = 0
        self._queue_relaxed_this_round: bool = False
        self._last_active_progress_order_id: str | None = None
        self._last_active_delivered_total: int = 0
        self._forced_tail_no_progress_rounds: int = 0
        self._active_no_progress_rounds: int = 0

    @staticmethod
    def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def _remaining_counts(order: Any | None) -> Counter[str]:
        if order is None:
            return Counter()
        need = Counter(str(t) for t in order.items_required)
        for t in order.items_delivered:
            t = str(t)
            if need.get(t, 0) > 0:
                need[t] -= 1
        return Counter({k: int(v) for k, v in need.items() if int(v) > 0})

    def _ensure_supply_index(self, state: GameState) -> None:
        ids = {str(item.id) for item in state.items}
        if self._type_to_shelves and ids == self._indexed_ids:
            return
        out: dict[str, list[ShelfRef]] = {}
        all_y: list[int] = []
        for item in state.items:
            all_y.append(int(item.position[1]))
            out.setdefault(str(item.type), []).append(
                ShelfRef(item_id=str(item.id), pos=(int(item.position[0]), int(item.position[1])))
            )
        for t in out:
            out[t].sort(key=lambda r: (r.pos[1], r.pos[0], r.item_id))
        self._indexed_ids = ids
        self._type_to_shelves = out
        if all_y:
            sorted_y = sorted(all_y)
            self._cluster_mid_y = int(sorted_y[len(sorted_y) // 2])
        else:
            self._cluster_mid_y = max(0, int(state.grid.height) // 2)

    def _assign_roles(self, bots: list[BotInfo]) -> None:
        ids = [int(bot.id) for bot in sorted(bots, key=lambda b: int(b.id))]
        n = len(ids)
        if n >= 10:
            courier_n, flex_n = 4, 2
        else:
            flex_n = max(1, min(2, n // 4)) if n > 1 else 0
            courier_n = max(1, min(4, (n - flex_n + 1) // 2)) if n else 0
        harvester_n = max(0, n - courier_n - flex_n)
        roles: dict[int, str] = {}
        cluster_pref: dict[int, str] = {}
        harvester_idx = 0
        for i, bid in enumerate(ids):
            if i < courier_n:
                roles[bid] = ROLE_COURIER
                cluster_pref[bid] = CLUSTER_CENTER
            elif i < courier_n + harvester_n:
                roles[bid] = ROLE_HARVESTER
                cluster_pref[bid] = CLUSTER_UPPER if (harvester_idx % 2 == 0) else CLUSTER_LOWER
                harvester_idx += 1
            else:
                roles[bid] = ROLE_FLEX
                cluster_pref[bid] = CLUSTER_DISPATCH
        self._roles = roles
        self._cluster_pref_by_bot = cluster_pref

    @staticmethod
    def _count_active_committed(
        bots: list[BotInfo],
        active_remaining: Counter[str],
        *,
        drop: tuple[int, int],
        commit_radius: int = 8,
    ) -> Counter[str]:
        if not active_remaining:
            return Counter()
        needed = set(active_remaining)
        out: Counter[str] = Counter()
        for bot in bots:
            inv = [str(t) for t in bot.inventory if str(t) in needed]
            if not inv:
                continue
            pos = (int(bot.position[0]), int(bot.position[1]))
            dist = abs(pos[0] - int(drop[0])) + abs(pos[1] - int(drop[1]))
            # Conservative commitment: count only cargo likely to reach D0 soon.
            if dist > max(0, int(commit_radius)):
                continue
            for t in inv:
                out[t] += 1
        return out

    @staticmethod
    def _near_committed_for_types(
        bots: list[BotInfo],
        target_types: set[str],
        *,
        drop: tuple[int, int],
        commit_radius: int = 8,
    ) -> int:
        if not target_types:
            return 0
        out = 0
        for bot in bots:
            pos = (int(bot.position[0]), int(bot.position[1]))
            dist = abs(pos[0] - int(drop[0])) + abs(pos[1] - int(drop[1]))
            if dist > max(0, int(commit_radius)):
                continue
            for t in bot.inventory:
                if str(t) in target_types:
                    out += 1
        return int(out)

    @staticmethod
    def _corridor_key(*, drop: tuple[int, int], target: tuple[int, int]) -> str:
        dy = int(target[1]) - int(drop[1])
        if dy < -1:
            return "upper"
        if dy > 1:
            return "lower"
        return "center"

    @staticmethod
    def _corridor_occupancy(
        bots: list[BotInfo],
        *,
        drop: tuple[int, int],
        target: tuple[int, int],
    ) -> int:
        x_min = min(int(drop[0]), int(target[0]))
        x_max = max(int(drop[0]), int(target[0]))
        y_mid = int(target[1])
        occ = 0
        for bot in bots:
            pos = (int(bot.position[0]), int(bot.position[1]))
            if x_min <= pos[0] <= x_max and abs(pos[1] - y_mid) <= 1:
                occ += 1
        return int(occ)

    def _cluster_for_pos(self, pos: tuple[int, int]) -> str:
        return CLUSTER_UPPER if int(pos[1]) <= int(self._cluster_mid_y) else CLUSTER_LOWER

    @staticmethod
    def _drop_corridor_occupancy(
        bots: list[BotInfo],
        *,
        drop: tuple[int, int],
        x_span: int = 8,
    ) -> int:
        occ = 0
        for bot in bots:
            pos = (int(bot.position[0]), int(bot.position[1]))
            if pos[0] <= int(drop[0]) + int(x_span) and abs(pos[1] - int(drop[1])) <= 1:
                occ += 1
        return int(occ)

    @staticmethod
    def _active_secured(active_remaining: Counter[str], active_committed: Counter[str]) -> bool:
        return all(int(active_committed.get(t, 0)) >= int(v) for t, v in active_remaining.items())

    @staticmethod
    def _deficit_after_commit(active_remaining: Counter[str], active_committed: Counter[str]) -> Counter[str]:
        return Counter({
            t: int(v) - int(active_committed.get(t, 0))
            for t, v in active_remaining.items()
            if int(v) - int(active_committed.get(t, 0)) > 0
        })

    @staticmethod
    def _item_value(item_type: str, active_remaining: Counter[str], preview_remaining: Counter[str], active_committed: Counter[str]) -> int:
        missing = int(sum(active_remaining.values()))
        distinct = int(sum(1 for v in active_remaining.values() if int(v) > 0))
        need = int(active_remaining.get(item_type, 0))
        committed = int(active_committed.get(item_type, 0))
        if need > committed:
            if missing == 1 and (need - committed) == 1:
                return 100
            if distinct <= 2:
                return 40
            return 10
        unresolved = sum(max(0, int(active_remaining[t]) - int(active_committed.get(t, 0))) for t in active_remaining)
        if unresolved == 0 and int(preview_remaining.get(item_type, 0)) > 0:
            return 3
        return 0

    def _bot_has_closing_item(self, bot: BotInfo, active_remaining: Counter[str], active_committed: Counter[str]) -> bool:
        if not bot.inventory:
            return False
        own = Counter(str(t) for t in bot.inventory if str(t) in active_remaining)
        without = Counter(active_committed)
        for t, c in own.items():
            without[t] = max(0, int(without.get(t, 0)) - int(c))
        return any(int(active_remaining.get(t, 0)) > int(without.get(t, 0)) for t in own)

    def _should_deliver(
        self,
        bot: BotInfo,
        active_remaining: Counter[str],
        active_committed: Counter[str],
        *,
        drop: tuple[int, int] | None = None,
        role: str = ROLE_HARVESTER,
        force_delivery: bool = False,
    ) -> bool:
        if not bot.inventory:
            return False
        active_items = [str(t) for t in bot.inventory if str(t) in active_remaining]
        if not active_items:
            return False
        if force_delivery:
            return True
        missing = int(sum(active_remaining.values()))
        distinct = int(sum(1 for v in active_remaining.values() if int(v) > 0))
        if self._bot_has_closing_item(bot, active_remaining, active_committed):
            return True
        if role == ROLE_COURIER:
            # Couriers should convert nearby/valuable active cargo quickly, but not all at once.
            dist_to_drop = 10**9
            if drop is not None:
                dist_to_drop = self._manhattan((int(bot.position[0]), int(bot.position[1])), drop)
            if dist_to_drop <= 6 or len(active_items) >= 2 or missing <= 3:
                return True
        if missing <= 2 or len(active_items) >= 2 or len(bot.inventory) >= 3:
            return True
        for t in active_items:
            if int(active_remaining.get(t, 0)) > int(active_committed.get(t, 0)) and distinct <= 2:
                return True
        return False

    def _select_delivery_lane_force_bots(
        self,
        *,
        bots: list[BotInfo],
        active_remaining: Counter[str],
        active_committed: Counter[str],
        drop: tuple[int, int],
    ) -> set[int]:
        ranked: list[tuple[int, int, int, int, int]] = []
        for bot in bots:
            bid = int(bot.id)
            active_inv = [str(t) for t in bot.inventory if str(t) in active_remaining]
            if not active_inv:
                continue
            role = self._roles.get(bid, ROLE_HARVESTER)
            role_rank = 2
            if role == ROLE_COURIER:
                role_rank = 0
            elif role == ROLE_FLEX:
                role_rank = 1
            dist = self._manhattan((int(bot.position[0]), int(bot.position[1])), drop)
            closing_rank = 0 if self._bot_has_closing_item(bot, active_remaining, active_committed) else 1
            ranked.append((closing_rank, role_rank, dist, -len(active_inv), bid))
        if not ranked:
            return set()
        ranked.sort()
        return {int(ranked[0][4])}

    def _phase(self, state: GameState, active_remaining: Counter[str], active_secured: bool) -> str:
        round_idx = int(state.round)
        missing = int(sum(active_remaining.values()))
        missing_types = int(sum(1 for v in active_remaining.values() if int(v) > 0))
        if round_idx <= 15:
            return PHASE_BOOT
        if missing > 0 and missing_types <= 2:
            return PHASE_CRITICAL
        if missing > 0 and active_secured:
            return PHASE_TRANSITION
        if round_idx - int(self._last_transition_round) <= 6:
            return PHASE_POST
        return PHASE_ACTIVE

    def _preview_allowed(self, state: GameState, active_secured: bool, deficit_after_commit: Counter[str]) -> bool:
        if active_secured:
            return True
        unresolved = int(sum(deficit_after_commit.values()))
        if unresolved <= 0:
            return True
        if unresolved == 1:
            drop = (int(state.drop_off[0]), int(state.drop_off[1]))
            return any(self._manhattan((int(b.position[0]), int(b.position[1])), drop) <= 4 for b in state.bots if b.inventory)
        return False

    @staticmethod
    def _adjacent_cells(pos: tuple[int, int]) -> tuple[tuple[int, int], ...]:
        x, y = pos
        return ((x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y))

    def _queue_layout(self, grid: Grid, drop: tuple[int, int], blocked: set[tuple[int, int]]) -> tuple[tuple[int, int], list[tuple[int, int]]]:
        dx, dy = drop
        stop = (dx + 1, dy)
        queue = [(dx + 2, dy), (dx + 3, dy), (dx + 4, dy)]

        def ok(cell: tuple[int, int]) -> bool:
            return grid.is_walkable(cell[0], cell[1]) and cell not in blocked and cell != drop

        if not ok(stop):
            stop = next((c for c in self._nearest_cells(grid, drop, blocked, 1, {drop})), drop)
        queue = [c for c in queue if ok(c)]
        if len(queue) < 3:
            extra = self._nearest_cells(grid, stop, blocked, 8, {drop, stop, *queue})
            for c in extra:
                if c not in queue and c != drop and c != stop:
                    queue.append(c)
                if len(queue) >= 3:
                    break
        return stop, queue[:3]

    def _nearest_cells(
        self,
        grid: Grid,
        start: tuple[int, int],
        blocked: set[tuple[int, int]],
        count: int,
        exclude: set[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        q = deque([start])
        seen = {start}
        out: list[tuple[int, int]] = []
        while q and len(out) < count:
            cur = q.popleft()
            if cur != start and cur not in blocked and cur not in exclude:
                out.append(cur)
            for nx, ny in grid.neighbors(cur[0], cur[1]):
                nxt = (int(nx), int(ny))
                if nxt in seen or nxt in blocked:
                    continue
                seen.add(nxt)
                q.append(nxt)
        return out

    def _assign_drop_queue(
        self,
        bots: list[BotInfo],
        drop: tuple[int, int],
        stop: tuple[int, int],
        queue: list[tuple[int, int]],
        active_remaining: Counter[str],
        active_committed: Counter[str],
        *,
        role_by_bot: dict[int, str] | None = None,
        force_delivery_bots: set[int] | None = None,
        corridor_occupancy: int = 999,
        queue_hold_burst_recent: bool = False,
    ) -> dict[int, tuple[int, int]]:
        forced = force_delivery_bots if force_delivery_bots is not None else set()
        ranked: list[tuple[float, int, int]] = []
        for bot in bots:
            bid = int(bot.id)
            role = ROLE_HARVESTER if role_by_bot is None else str(role_by_bot.get(bid, ROLE_HARVESTER))
            if not self._should_deliver(
                bot,
                active_remaining,
                active_committed,
                drop=drop,
                role=role,
                force_delivery=(bid in forced),
            ):
                continue
            pos = (int(bot.position[0]), int(bot.position[1]))
            urgency = float(len(bot.inventory) * 30 - self._manhattan(pos, drop) * 4)
            if self._bot_has_closing_item(bot, active_remaining, active_committed):
                urgency += 120.0
            ranked.append((urgency, bid, self._manhattan(pos, drop)))
        ranked.sort(key=lambda x: (-x[0], x[2], x[1]))

        targets = [drop, stop, *queue]
        drop_occupied = any(
            (int(bot.position[0]), int(bot.position[1])) == drop
            for bot in bots
        )
        near_drop_deliverers = sum(
            1
            for bot in bots
            if self._should_deliver(
                bot,
                active_remaining,
                active_committed,
                drop=drop,
                role=ROLE_HARVESTER if role_by_bot is None else str(role_by_bot.get(int(bot.id), ROLE_HARVESTER)),
                force_delivery=(int(bot.id) in forced),
            )
            and self._manhattan((int(bot.position[0]), int(bot.position[1])), drop) <= 3
        )
        low_pressure = (not drop_occupied) and near_drop_deliverers <= 1
        queue_relaxed = (
            low_pressure
            and int(corridor_occupancy) <= 2
            and not bool(queue_hold_burst_recent)
        )
        max_slots = len(targets)
        if queue_relaxed:
            # Mild deserialization when D0 corridor is clear: do not over-admit queue.
            max_slots = min(max_slots, 3)
        self._queue_relaxed_this_round = bool(queue_relaxed)
        if self._queue_relaxed_this_round:
            self._queue_relaxed_rounds += 1
        out: dict[int, tuple[int, int]] = {}
        for i, row in enumerate(ranked):
            if i >= max_slots:
                break
            bid = int(row[1])
            out[bid] = targets[i] if i < len(targets) else targets[-1]
        return out

    def _step_toward(
        self,
        grid: Grid,
        start: tuple[int, int],
        goal: tuple[int, int],
        blocked: set[tuple[int, int]],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, int]:
        if start == goal:
            return start
        path = bfs_shortest_path(grid, start, goal, blocked=blocked)
        if path and len(path) > 1:
            nxt = (int(path[1][0]), int(path[1][1]))
            if nxt not in forbidden:
                return nxt
        best = start
        best_d = 10**9
        for nx, ny in grid.neighbors(start[0], start[1]):
            nxt = (int(nx), int(ny))
            if nxt in blocked or nxt in forbidden:
                continue
            d = self._manhattan(nxt, goal)
            if d < best_d:
                best_d = d
                best = nxt
        return best

    def _adjacent_items_lookup(self, grid: Grid, items: list[ItemInfo]) -> dict[tuple[int, int], list[ItemInfo]]:
        out: dict[tuple[int, int], list[ItemInfo]] = {}
        for item in items:
            shelf = (int(item.position[0]), int(item.position[1]))
            for cell in self._adjacent_cells(shelf):
                if grid.is_walkable(cell[0], cell[1]):
                    out.setdefault(cell, []).append(item)
        return out

    def _choose_target(
        self,
        bot: BotInfo,
        role: str,
        phase: str,
        grid: Grid,
        drop: tuple[int, int],
        active_remaining: Counter[str],
        preview_remaining: Counter[str],
        active_committed: Counter[str],
        preview_allowed: bool,
        allow_sustain_lane: bool,
        critical_types: set[str],
        cluster_pref: str,
        enforce_cluster: bool,
        reserved_pick: set[tuple[int, int]],
        visible_ids: set[str],
        blocked: set[tuple[int, int]],
    ) -> TargetChoice | None:
        pos = (int(bot.position[0]), int(bot.position[1]))
        deficit = self._deficit_after_commit(active_remaining, active_committed)
        active_types = [t for t, v in deficit.items() if int(v) > 0]
        active_all_types = [t for t, v in active_remaining.items() if int(v) > 0]
        preview_types = [t for t, v in preview_remaining.items() if int(v) > 0]

        type_modes: list[tuple[str, str]] = []
        if phase == PHASE_CRITICAL and critical_types:
            type_modes.extend((t, "critical") for t in sorted(critical_types))
        else:
            type_modes.extend((t, "active_deficit") for t in sorted(active_types))
        if not type_modes and allow_sustain_lane:
            type_modes.extend((t, "active_sustain") for t in sorted(active_all_types))
        if not type_modes and preview_allowed:
            type_modes.extend((t, "preview") for t in sorted(preview_types))
        elif preview_allowed and role in {ROLE_HARVESTER, ROLE_FLEX} and phase in {PHASE_TRANSITION, PHASE_POST}:
            for t in preview_types:
                if all(existing_t != t for existing_t, _existing_mode in type_modes):
                    type_modes.append((t, "preview"))

        best: TargetChoice | None = None
        best_score = -1e18
        for t, mode in type_modes:
            val = self._item_value(t, active_remaining, preview_remaining, active_committed)
            if val <= 0 and mode in {"active_sustain", "active_deficit", "critical"}:
                # Sustain lane keeps active retrieval alive when optimistic commit covers demand.
                val = 6 if role == ROLE_HARVESTER else 4
            if val <= 0:
                continue
            for shelf in self._type_to_shelves.get(t, []):
                if shelf.item_id not in visible_ids:
                    continue
                shelf_cluster = self._cluster_for_pos(shelf.pos)
                if (
                    enforce_cluster
                    and role == ROLE_HARVESTER
                    and cluster_pref in {CLUSTER_UPPER, CLUSTER_LOWER}
                    and shelf_cluster != cluster_pref
                ):
                    continue
                pickup = None
                pickup_d = 10**9
                for cell in self._adjacent_cells(shelf.pos):
                    if not grid.is_walkable(cell[0], cell[1]) or cell in blocked or cell in reserved_pick:
                        continue
                    d = self._manhattan(pos, cell)
                    if d < pickup_d:
                        pickup_d = d
                        pickup = cell
                if pickup is None:
                    continue

                if mode == "preview":
                    src = "preview"
                elif mode == "active_sustain":
                    src = "active_sustain"
                else:
                    src = "active"
                if role == ROLE_COURIER:
                    if src == "preview":
                        continue
                    if phase != PHASE_CRITICAL and self._manhattan(shelf.pos, drop) > 12:
                        continue
                score = float(val * 100 - pickup_d * 8)
                if role == ROLE_HARVESTER and src == "active":
                    score += 20.0
                    if cluster_pref in {CLUSTER_UPPER, CLUSTER_LOWER} and shelf_cluster == cluster_pref:
                        score += 10.0
                    if mode == "active_sustain":
                        score -= 18.0
                if role == ROLE_FLEX and t in critical_types:
                    score += 24.0
                if phase == PHASE_CRITICAL and src == "preview":
                    score -= 80.0
                if mode == "critical":
                    score += 18.0

                if score > best_score:
                    best_score = score
                    best = TargetChoice(item_id=shelf.item_id, item_type=t, pickup_pos=pickup, source=src)
        return best

    def _choose_stage_target(
        self,
        *,
        bot: BotInfo,
        role: str,
        grid: Grid,
        active_remaining: Counter[str],
        cluster_pref: str,
        reserved_pick: set[tuple[int, int]],
        visible_ids: set[str],
        blocked: set[tuple[int, int]],
    ) -> tuple[int, int] | None:
        pos = (int(bot.position[0]), int(bot.position[1]))
        best_cell: tuple[int, int] | None = None
        best_score = -1e18
        for item_type, amount in active_remaining.items():
            if int(amount) <= 0:
                continue
            for shelf in self._type_to_shelves.get(str(item_type), []):
                if shelf.item_id not in visible_ids:
                    continue
                shelf_cluster = self._cluster_for_pos(shelf.pos)
                if (
                    role == ROLE_HARVESTER
                    and cluster_pref in {CLUSTER_UPPER, CLUSTER_LOWER}
                    and shelf_cluster != cluster_pref
                ):
                    continue
                for cell in self._adjacent_cells(shelf.pos):
                    if not grid.is_walkable(cell[0], cell[1]):
                        continue
                    if cell in blocked or cell in reserved_pick:
                        continue
                    d = self._manhattan(pos, cell)
                    score = float(200.0 - d * 8)
                    if role == ROLE_HARVESTER and cluster_pref in {CLUSTER_UPPER, CLUSTER_LOWER} and shelf_cluster == cluster_pref:
                        score += 12.0
                    if score > best_score:
                        best_score = score
                        best_cell = cell
        return best_cell

    def _record(self, bid: int, role: str, phase: str, state_name: str, target: tuple[int, int] | None, item: str | None, item_type: str | None) -> None:
        row: dict[str, Any] = {"role": role, "phase": phase, "state": state_name}
        cluster_pref = self._cluster_pref_by_bot.get(int(bid))
        if cluster_pref is not None:
            row["cluster_pref"] = str(cluster_pref)
        if target is not None:
            row["target"] = [int(target[0]), int(target[1])]
            row["target_cluster"] = self._cluster_for_pos(target)
        if item is not None:
            row["item_id"] = str(item)
        if item_type is not None:
            row["item_type"] = str(item_type)
        self.last_assignment_snapshot[int(bid)] = row

    def _select_critical_hunters(
        self,
        *,
        bots: list[BotInfo],
        critical_types: set[str],
        active_remaining: Counter[str],
        active_committed: Counter[str],
        drop: tuple[int, int],
    ) -> tuple[set[int], dict[int, tuple[int, int]], float, int]:
        if not critical_types:
            return set(), {}, 0.0, 0
        ranked: list[tuple[int, int, int, int, int, tuple[int, int], str]] = []
        for bot in bots:
            bid = int(bot.id)
            role = self._roles.get(bid, ROLE_HARVESTER)
            if self._should_deliver(bot, active_remaining, active_committed, drop=drop, role=role):
                continue
            role_rank = 2
            if role == ROLE_FLEX:
                role_rank = 0
            elif role == ROLE_HARVESTER:
                role_rank = 1
            pos = (int(bot.position[0]), int(bot.position[1]))
            best_dist = 10**9
            best_target: tuple[int, int] | None = None
            for item_type in critical_types:
                for shelf in self._type_to_shelves.get(item_type, []):
                    dist = self._manhattan(pos, shelf.pos)
                    if dist < best_dist:
                        best_dist = dist
                        best_target = shelf.pos
            if best_target is None:
                continue
            corridor_occ = self._corridor_occupancy(bots, drop=drop, target=best_target)
            # Suppress escalation for bots entering already congested local corridors.
            if best_dist <= 4 and corridor_occ >= 4:
                continue
            carry_penalty = 4 if bot.inventory else 0
            corridor_key = self._corridor_key(drop=drop, target=best_target)
            ranked.append(
                (
                    role_rank,
                    int(corridor_occ),
                    best_dist + carry_penalty,
                    len(bot.inventory),
                    bid,
                    best_target,
                    corridor_key,
                )
            )
        if not ranked:
            return set(), {}, 0.0, 0

        ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
        primary = ranked[0]
        selected: list[tuple[int, int, int, int, int, tuple[int, int], str]] = [primary]
        primary_key = primary[6]

        # One hunter by default. Admit second only when lane is disjoint.
        if len(critical_types) >= 2:
            for cand in ranked[1:]:
                key = cand[6]
                if key != primary_key:
                    selected.append(cand)
                    break

        hunter_ids = {int(row[4]) for row in selected}
        target_by_bot = {int(row[4]): row[5] for row in selected}
        corridor_occ = max(int(row[1]) for row in selected)
        overlap = 0.0
        if len(selected) >= 2:
            a = selected[0]
            b = selected[1]
            if a[6] == b[6]:
                overlap = 1.0
            elif self._manhattan(a[5], b[5]) < 6:
                overlap = 0.5
        return hunter_ids, target_by_bot, float(overlap), int(corridor_occ)

    def _idle_lane_clear_step(
        self,
        *,
        grid: Grid,
        pos: tuple[int, int],
        drop: tuple[int, int],
        blocked: set[tuple[int, int]],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, int]:
        # Keep idle bots from parking on the drop-off lane and blocking couriers.
        if pos[1] != drop[1]:
            return pos
        if pos[0] <= drop[0] + 3:
            return pos
        candidates = [
            (pos[0], pos[1] - 1),
            (pos[0], pos[1] + 1),
            (pos[0] + 1, pos[1]),
            (pos[0] - 1, pos[1]),
        ]
        best = pos
        best_score = -10**9
        for c in candidates:
            if not grid.is_walkable(c[0], c[1]):
                continue
            if c in blocked or c in forbidden:
                continue
            # Prefer leaving the lane, and secondarily avoid the drop corridor.
            score = abs(c[1] - drop[1]) * 100 - abs(c[0] - drop[0])
            if score > best_score:
                best_score = score
                best = c
        return best

    def _deadweight_drop_clear_step(
        self,
        *,
        grid: Grid,
        pos: tuple[int, int],
        drop: tuple[int, int],
        blocked: set[tuple[int, int]],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, int]:
        if pos != drop:
            return pos
        candidates = [
            (drop[0], drop[1] - 1),
            (drop[0], drop[1] + 1),
            (drop[0] + 1, drop[1]),
            (drop[0] + 2, drop[1]),
        ]
        best = pos
        best_score = -10**9
        for c in candidates:
            if not grid.is_walkable(c[0], c[1]):
                continue
            if c in blocked or c in forbidden:
                continue
            score = abs(c[1] - drop[1]) * 100 + abs(c[0] - drop[0])
            if score > best_score:
                best_score = score
                best = c
        return best

    def decide(self, state: GameState) -> RoundActions:
        started = time.perf_counter()
        self.last_collisions_avoided = 0
        self.last_assignment_snapshot = {}
        self.last_pre_collision_actions = {}
        self.last_round_debug = {}
        self._round_wait_reason_by_bot = {}
        self._queue_relaxed_this_round = False

        bots = sorted(state.bots, key=lambda b: int(b.id))
        grid = Grid(state.grid)
        drop = (int(state.drop_off[0]), int(state.drop_off[1]))
        item_blocked = {(int(i.position[0]), int(i.position[1])) for i in state.items}
        visible_ids = {str(i.id) for i in state.items}

        self._ensure_supply_index(state)
        self._assign_roles(bots)

        active = get_active_order(state)
        preview = get_preview_order(state)
        active_remaining = self._remaining_counts(active)
        preview_remaining = self._remaining_counts(preview)
        active_committed = self._count_active_committed(
            bots,
            active_remaining,
            drop=drop,
            commit_radius=8,
        )
        deficit_after_commit = self._deficit_after_commit(active_remaining, active_committed)
        active_secured = self._active_secured(active_remaining, active_committed)

        active_id = str(active.id) if active is not None else None
        if self._last_active_order_id is not None and active_id != self._last_active_order_id:
            self._last_transition_round = int(state.round)
        self._last_active_order_id = active_id

        phase = self._phase(state, active_remaining, active_secured)
        preview_allowed = self._preview_allowed(state, active_secured, deficit_after_commit)

        critical_types = {t for t, v in deficit_after_commit.items() if int(v) > 0}
        if len(critical_types) > 2:
            critical_types = set()
        deficit_missing_total = int(sum(deficit_after_commit.values()))
        deficit_missing_types = int(sum(1 for v in deficit_after_commit.values() if int(v) > 0))
        near_committed_deficit = self._near_committed_for_types(
            bots,
            set(deficit_after_commit.keys()),
            drop=drop,
            commit_radius=8,
        )
        active_delivered_total = len(active.items_delivered) if active is not None else 0
        if active_id != self._last_active_progress_order_id:
            self._last_active_progress_order_id = active_id
            self._last_active_delivered_total = int(active_delivered_total)
            self._forced_tail_no_progress_rounds = 0
            self._active_no_progress_rounds = 0
        active_progressed = int(active_delivered_total) > int(self._last_active_delivered_total)
        if int(sum(active_remaining.values())) <= 0:
            self._active_no_progress_rounds = 0
        elif active_progressed:
            self._active_no_progress_rounds = 0
        else:
            self._active_no_progress_rounds += 1

        active_cargo_total = sum(
            1
            for bot in bots
            for item_type in bot.inventory
            if str(item_type) in active_remaining
        )
        force_delivery_bots = self._select_delivery_lane_force_bots(
            bots=bots,
            active_remaining=active_remaining,
            active_committed=active_committed,
            drop=drop,
        ) if int(active_cargo_total) > 0 else set()
        delivery_lane_force_active = bool(force_delivery_bots)

        sustain_active_lane = bool(
            int(sum(active_remaining.values())) > 0
            and int(sum(deficit_after_commit.values())) <= 0
            and int(self._active_no_progress_rounds) >= 2
        )
        if (delivery_lane_force_active and self._active_no_progress_rounds >= 2) or sustain_active_lane:
            preview_allowed = False

        base_force_critical_tail = (
            phase != PHASE_CRITICAL
            and deficit_missing_total > 0
            and deficit_missing_total <= 3
            and deficit_missing_types <= 2
            and near_committed_deficit <= 0
        )
        force_tail_suppressed = False
        force_critical_tail = False
        if base_force_critical_tail:
            if active_progressed:
                self._forced_tail_no_progress_rounds = 0
            else:
                self._forced_tail_no_progress_rounds += 1
            # Avoid long single-type critical-tail lock without delivery progress.
            if deficit_missing_types <= 1 and self._forced_tail_no_progress_rounds >= 24:
                force_tail_suppressed = True
                force_critical_tail = False
            else:
                force_critical_tail = True
        else:
            self._forced_tail_no_progress_rounds = 0

        if force_critical_tail:
            phase = PHASE_CRITICAL
            if not critical_types:
                critical_types = set(deficit_after_commit.keys())
        if force_critical_tail:
            critical_hunter_ids, critical_target_by_bot, critical_target_overlap, critical_corridor_occupancy = (
                self._select_critical_hunters(
                    bots=bots,
                    critical_types=critical_types,
                    active_remaining=active_remaining,
                    active_committed=active_committed,
                    drop=drop,
                )
            )
        else:
            critical_hunter_ids = set()
            critical_target_by_bot: dict[int, tuple[int, int]] = {}
            critical_target_overlap = 0.0
            critical_corridor_occupancy = 0

        drop_corridor_occupancy = self._drop_corridor_occupancy(bots, drop=drop, x_span=8)
        queue_hold_burst_recent = self._queue_hold_burst_decay > 0

        stop, queue = self._queue_layout(grid, drop, item_blocked)
        drop_queue = self._assign_drop_queue(
            bots,
            drop,
            stop,
            queue,
            active_remaining,
            active_committed,
            role_by_bot=self._roles,
            force_delivery_bots=force_delivery_bots,
            corridor_occupancy=drop_corridor_occupancy,
            queue_hold_burst_recent=queue_hold_burst_recent,
        )

        adjacent_lookup = self._adjacent_items_lookup(grid, state.items)
        committed_plan = Counter(active_committed)
        reserved_pick: set[tuple[int, int]] = set()
        reserved_stage: set[tuple[int, int]] = set()
        reserved_next: set[tuple[int, int]] = set()
        cluster_match_targets = 0
        cluster_fallback_targets = 0
        courier_active_targets = 0
        active_stage_targets = 0
        active_sustain_targets = 0
        sustain_budget = 1 if sustain_active_lane else 0
        sustain_assigned = 0

        cmds: dict[int, BotActionCommand] = {}
        occupied_now: set[tuple[int, int]] = set()
        moves: list[tuple[int, tuple[int, int], tuple[int, int]]] = []

        for bot in bots:
            bid = int(bot.id)
            pos = (int(bot.position[0]), int(bot.position[1]))
            role = self._roles.get(bid, ROLE_HARVESTER)
            bot_phase = phase
            if force_critical_tail and critical_hunter_ids and bid not in critical_hunter_ids:
                bot_phase = PHASE_ACTIVE
            inv = list(bot.inventory)
            active_inv = [str(t) for t in inv if str(t) in active_remaining]

            if inv and pos == drop:
                if active_inv:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.DROP_OFF)
                    occupied_now.add(pos)
                    self._record(bid, role, bot_phase, STATE_DELIVER_ACTIVE, drop, None, None)
                    continue
                deadweight_step = self._deadweight_drop_clear_step(
                    grid=grid,
                    pos=pos,
                    drop=drop,
                    blocked=set(item_blocked) | occupied_now,
                    forbidden=reserved_next,
                )
                if deadweight_step != pos:
                    moves.append((bid, pos, deadweight_step))
                    reserved_next.add(deadweight_step)
                    self.last_pre_collision_actions[bid] = {
                        "type": "move",
                        "target": [deadweight_step[0], deadweight_step[1]],
                        "goal": [deadweight_step[0], deadweight_step[1]],
                    }
                    self._record(bid, role, bot_phase, STATE_DELIVER_PREVIEW_READY, deadweight_step, None, None)
                else:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    occupied_now.add(pos)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_deadweight_on_drop"
                    self._record(bid, role, bot_phase, STATE_DELIVER_PREVIEW_READY, drop, None, None)
                continue

            q_target = drop_queue.get(bid)
            if q_target is not None:
                if pos == q_target:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    occupied_now.add(pos)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_drop_queue_slot_hold"
                else:
                    nxt = self._step_toward(grid, pos, q_target, set(item_blocked) | occupied_now, reserved_next)
                    if nxt == pos:
                        cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        occupied_now.add(pos)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_no_queue_path"
                    else:
                        moves.append((bid, pos, nxt))
                        reserved_next.add(nxt)
                        self.last_pre_collision_actions[bid] = {"type": "move", "target": [nxt[0], nxt[1]], "goal": [q_target[0], q_target[1]]}
                self._record(bid, role, bot_phase, STATE_QUEUE_DROP, q_target, None, None)
                continue

            if self._should_deliver(
                bot,
                active_remaining,
                committed_plan,
                drop=drop,
                role=role,
                force_delivery=(bid in force_delivery_bots),
            ):
                nxt = self._step_toward(grid, pos, drop, set(item_blocked) | occupied_now, reserved_next)
                if nxt == pos:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    occupied_now.add(pos)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_delivery_path_block"
                else:
                    moves.append((bid, pos, nxt))
                    reserved_next.add(nxt)
                    self.last_pre_collision_actions[bid] = {"type": "move", "target": [nxt[0], nxt[1]], "goal": [drop[0], drop[1]]}
                self._record(bid, role, bot_phase, STATE_DELIVER_ACTIVE if active_inv else STATE_DELIVER_PREVIEW_READY, drop, None, None)
                continue

            if len(inv) < 3:
                adj = adjacent_lookup.get(pos, [])
                best_adj: ItemInfo | None = None
                best_adj_score = -1e18
                for item in adj:
                    t = str(item.type)
                    v = self._item_value(t, active_remaining, preview_remaining, committed_plan)
                    if v <= 0:
                        continue
                    if v == 3 and not preview_allowed:
                        continue
                    score = float(v * 100)
                    if role == ROLE_FLEX and t in critical_types:
                        score += 16.0
                    if score > best_adj_score:
                        best_adj_score = score
                        best_adj = item
                if best_adj is not None:
                    it = str(best_adj.type)
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=str(best_adj.id))
                    occupied_now.add(pos)
                    self.last_pre_collision_actions[bid] = {"type": "pick_up", "item_id": str(best_adj.id)}
                    if int(active_remaining.get(it, 0)) > int(committed_plan.get(it, 0)):
                        committed_plan[it] += 1
                    self._record(
                        bid,
                        role,
                        bot_phase,
                        STATE_FLEX_HUNT if role == ROLE_FLEX and bot_phase == PHASE_CRITICAL else (
                            STATE_HARVEST_ACTIVE if it in active_remaining else STATE_HARVEST_PREVIEW
                        ),
                        pos,
                        str(best_adj.id),
                        it,
                    )
                    continue

            if phase == PHASE_BOOT and not inv:
                anchors = {
                    ROLE_COURIER: (drop[0] + 5, drop[1]),
                    ROLE_HARVESTER: (drop[0] + 6, drop[1] - 2),
                    ROLE_FLEX: (drop[0] + 4, drop[1] - 1),
                }
                target = anchors.get(role, (drop[0] + 5, drop[1] - 1))
                target = (max(0, min(grid.width - 1, target[0])), max(0, min(grid.height - 1, target[1])))
                nxt = self._step_toward(grid, pos, target, set(item_blocked) | occupied_now, reserved_next)
                if nxt != pos:
                    moves.append((bid, pos, nxt))
                    reserved_next.add(nxt)
                    self.last_pre_collision_actions[bid] = {"type": "move", "target": [nxt[0], nxt[1]], "goal": [target[0], target[1]]}
                else:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    occupied_now.add(pos)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_boot_block"
                self._record(bid, role, bot_phase, STATE_BOOT, target, None, None)
                if bid in cmds:
                    continue

            cluster_pref = self._cluster_pref_by_bot.get(bid, CLUSTER_CENTER)
            enforce_cluster = (
                role == ROLE_HARVESTER
                and bot_phase in {PHASE_ACTIVE, PHASE_TRANSITION, PHASE_POST}
                and cluster_pref in {CLUSTER_UPPER, CLUSTER_LOWER}
            )
            choice = self._choose_target(
                bot,
                role,
                bot_phase,
                grid,
                drop,
                active_remaining,
                preview_remaining,
                committed_plan,
                preview_allowed,
                sustain_active_lane and sustain_assigned < sustain_budget,
                critical_types,
                cluster_pref,
                enforce_cluster,
                reserved_pick,
                visible_ids,
                item_blocked,
            )
            if choice is None and enforce_cluster:
                choice = self._choose_target(
                    bot,
                    role,
                    bot_phase,
                    grid,
                    drop,
                    active_remaining,
                    preview_remaining,
                    committed_plan,
                    preview_allowed,
                    sustain_active_lane and sustain_assigned < sustain_budget,
                    critical_types,
                    cluster_pref,
                    False,
                    reserved_pick,
                    visible_ids,
                    item_blocked,
                )
                if choice is not None:
                    cluster_fallback_targets += 1
            if choice is not None:
                reserved_pick.add(choice.pickup_pos)
                if pos == choice.pickup_pos and len(inv) < 3:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=choice.item_id)
                    occupied_now.add(pos)
                    self.last_pre_collision_actions[bid] = {"type": "pick_up", "item_id": choice.item_id}
                else:
                    nxt = self._step_toward(grid, pos, choice.pickup_pos, set(item_blocked) | occupied_now, reserved_next)
                    if nxt == pos:
                        cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        occupied_now.add(pos)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_no_pick_path"
                    else:
                        moves.append((bid, pos, nxt))
                        reserved_next.add(nxt)
                        self.last_pre_collision_actions[bid] = {"type": "move", "target": [nxt[0], nxt[1]], "goal": [choice.pickup_pos[0], choice.pickup_pos[1]], "item_id": choice.item_id}
                if choice.source in {"active", "active_sustain"} and int(active_remaining.get(choice.item_type, 0)) > int(committed_plan.get(choice.item_type, 0)):
                    committed_plan[choice.item_type] += 1
                if role == ROLE_HARVESTER and cluster_pref in {CLUSTER_UPPER, CLUSTER_LOWER}:
                    if self._cluster_for_pos(choice.pickup_pos) == cluster_pref:
                        cluster_match_targets += 1
                if role == ROLE_COURIER and choice.source in {"active", "active_sustain"}:
                    courier_active_targets += 1
                if choice.source == "active_sustain":
                    active_sustain_targets += 1
                    sustain_assigned += 1
                self._record(
                    bid,
                    role,
                    bot_phase,
                    STATE_FLEX_HUNT if role == ROLE_FLEX and bot_phase == PHASE_CRITICAL else (
                        STATE_HARVEST_ACTIVE if choice.source in {"active", "active_sustain"} else STATE_HARVEST_PREVIEW
                    ),
                    choice.pickup_pos,
                    choice.item_id,
                    choice.item_type,
                )
                continue

            if not inv and int(sum(active_remaining.values())) > 0:
                stage_target = self._choose_stage_target(
                    bot=bot,
                    role=role,
                    grid=grid,
                    active_remaining=active_remaining,
                    cluster_pref=cluster_pref,
                    reserved_pick=(reserved_pick | reserved_stage),
                    visible_ids=visible_ids,
                    blocked=item_blocked,
                )
                if stage_target is not None:
                    active_stage_targets += 1
                    reserved_stage.add(stage_target)
                    if stage_target != pos:
                        nxt = self._step_toward(grid, pos, stage_target, set(item_blocked) | occupied_now, reserved_next)
                        if nxt == pos:
                            cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                            occupied_now.add(pos)
                            self._round_wait_reason_by_bot[bid] = "wait_due_to_stage_path_block"
                        else:
                            moves.append((bid, pos, nxt))
                            reserved_next.add(nxt)
                            self.last_pre_collision_actions[bid] = {
                                "type": "move",
                                "target": [nxt[0], nxt[1]],
                                "goal": [stage_target[0], stage_target[1]],
                            }
                    else:
                        cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        occupied_now.add(pos)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_stage_hold"
                    self._record(bid, role, bot_phase, STATE_HARVEST_ACTIVE, stage_target, None, None)
                    continue

            if not inv:
                clear_step = self._idle_lane_clear_step(
                    grid=grid,
                    pos=pos,
                    drop=drop,
                    blocked=set(item_blocked) | occupied_now,
                    forbidden=reserved_next,
                )
                if clear_step != pos:
                    moves.append((bid, pos, clear_step))
                    reserved_next.add(clear_step)
                    self.last_pre_collision_actions[bid] = {
                        "type": "move",
                        "target": [clear_step[0], clear_step[1]],
                        "goal": [clear_step[0], clear_step[1]],
                    }
                    self._record(bid, role, bot_phase, STATE_HARVEST_ACTIVE, clear_step, None, None)
                    continue

            cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
            occupied_now.add(pos)
            self._round_wait_reason_by_bot[bid] = "wait_due_to_no_target"
            self._record(bid, role, bot_phase, STATE_HARVEST_ACTIVE, None, None, None)

        if moves:
            resolved, stats = resolve_collisions_with_stats(moves, occupied_now, reservation_horizon=self.reservation_horizon)
            self.last_collisions_avoided = int(stats.blocked_moves)
            for bid, cur, _desired in moves:
                target = resolved.get(bid, cur)
                if target == cur:
                    cmds[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    self._round_wait_reason_by_bot.setdefault(bid, "wait_due_to_collision_block")
                else:
                    cmds[bid] = BotActionCommand(bot=bid, action=action_for_move(cur, target))

        actions = [cmds.get(int(bot.id), BotActionCommand(bot=int(bot.id), action=BotAction.WAIT)) for bot in bots]
        idle_steps = sum(1 for a in actions if a.action == BotAction.WAIT)
        queue_hold_waits = sum(
            1
            for reason in self._round_wait_reason_by_bot.values()
            if str(reason) == "wait_due_to_drop_queue_slot_hold"
        )
        if queue_hold_waits >= 2:
            self._queue_hold_burst_decay = 3
        else:
            self._queue_hold_burst_decay = max(0, int(self._queue_hold_burst_decay) - 1)
        collision_waits_round = sum(
            1
            for reason in self._round_wait_reason_by_bot.values()
            if str(reason) == "wait_due_to_collision_block"
        )
        collision_waits_during_critical = (
            collision_waits_round if (phase == PHASE_CRITICAL or force_critical_tail) else 0
        )

        self.last_round_telemetry = {
            "phase_boot": 1.0 if phase == PHASE_BOOT else 0.0,
            "phase_active": 1.0 if phase == PHASE_ACTIVE else 0.0,
            "phase_critical": 1.0 if phase == PHASE_CRITICAL else 0.0,
            "phase_transition": 1.0 if phase == PHASE_TRANSITION else 0.0,
            "phase_post_transition": 1.0 if phase == PHASE_POST else 0.0,
            "active_missing_total": float(sum(active_remaining.values())),
            "deficit_missing_total": float(deficit_missing_total),
            "active_secured": 1.0 if active_secured else 0.0,
            "preview_allowed": 1.0 if preview_allowed else 0.0,
            "critical_type_count": float(len(critical_types)),
            "critical_tail_force": 1.0 if force_critical_tail else 0.0,
            "forced_tail_no_progress_rounds": float(self._forced_tail_no_progress_rounds),
            "forced_tail_suppressed": 1.0 if force_tail_suppressed else 0.0,
            "critical_hunters": float(len(critical_hunter_ids)),
            "critical_hunter_count": float(len(critical_hunter_ids)),
            "critical_target_overlap": float(critical_target_overlap),
            "critical_corridor_occupancy": float(critical_corridor_occupancy),
            "near_committed_deficit": float(near_committed_deficit),
            "active_cargo_total": float(active_cargo_total),
            "active_no_progress_rounds": float(self._active_no_progress_rounds),
            "delivery_lane_force": 1.0 if delivery_lane_force_active else 0.0,
            "delivery_lane_forced_bots": float(len(force_delivery_bots)),
            "sustain_active_lane": 1.0 if sustain_active_lane else 0.0,
            "drop_queue_count": float(len(drop_queue)),
            "drop_corridor_occupancy": float(drop_corridor_occupancy),
            "queue_relaxed_rounds": float(self._queue_relaxed_rounds),
            "queue_hold_waits": float(queue_hold_waits),
            "collision_waits_during_critical_mode": float(collision_waits_during_critical),
            "idle_steps": float(idle_steps),
            "cluster_match_targets": float(cluster_match_targets),
            "cluster_fallback_targets": float(cluster_fallback_targets),
            "courier_active_targets": float(courier_active_targets),
            "active_stage_targets": float(active_stage_targets),
            "active_sustain_targets": float(active_sustain_targets),
        }
        self.last_round_debug = {
            "phase": phase,
            "active_remaining": dict(active_remaining),
            "preview_remaining": dict(preview_remaining),
            "active_committed": dict(active_committed),
            "deficit_after_commit": dict(deficit_after_commit),
            "force_critical_tail": bool(force_critical_tail),
            "force_tail_suppressed": bool(force_tail_suppressed),
            "forced_tail_no_progress_rounds": int(self._forced_tail_no_progress_rounds),
            "critical_hunter_ids": sorted(int(bid) for bid in critical_hunter_ids),
            "critical_target_by_bot": {
                str(int(bot_id)): [int(target[0]), int(target[1])]
                for bot_id, target in critical_target_by_bot.items()
            },
            "critical_target_overlap": float(critical_target_overlap),
            "critical_corridor_occupancy": int(critical_corridor_occupancy),
            "cluster_mid_y": int(self._cluster_mid_y),
            "cluster_pref_by_bot": {
                str(int(bot_id)): str(cluster)
                for bot_id, cluster in sorted(self._cluster_pref_by_bot.items(), key=lambda kv: kv[0])
            },
            "cluster_match_targets": int(cluster_match_targets),
            "cluster_fallback_targets": int(cluster_fallback_targets),
            "courier_active_targets": int(courier_active_targets),
            "active_stage_targets": int(active_stage_targets),
            "active_sustain_targets": int(active_sustain_targets),
            "active_cargo_total": int(active_cargo_total),
            "active_no_progress_rounds": int(self._active_no_progress_rounds),
            "delivery_lane_force": bool(delivery_lane_force_active),
            "delivery_lane_forced_bots": sorted(int(bid) for bid in force_delivery_bots),
            "sustain_active_lane": bool(sustain_active_lane),
            "near_committed_deficit": int(near_committed_deficit),
            "drop_corridor_occupancy": int(drop_corridor_occupancy),
            "queue_hold_burst_recent": bool(queue_hold_burst_recent),
            "queue_relaxed_this_round": bool(self._queue_relaxed_this_round),
            "queue_relaxed_rounds": int(self._queue_relaxed_rounds),
            "drop_queue": {str(k): [v[0], v[1]] for k, v in drop_queue.items()},
            "roles": {str(k): v for k, v in self._roles.items()},
        }

        self.last_decision_ms = float((time.perf_counter() - started) * 1000.0)
        self._last_active_delivered_total = int(active_delivered_total)
        return RoundActions(actions=actions)
