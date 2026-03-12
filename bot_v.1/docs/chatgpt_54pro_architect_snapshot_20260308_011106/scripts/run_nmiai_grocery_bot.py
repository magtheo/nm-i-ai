"""Live runner for NMiAI Grocery Bot with artifact recording."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
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
from bot.collision import action_for_move, resolve_collisions_with_stats
from bot.grid import Grid
from bot.models import BotAction, BotActionCommand, GameOver, GameState, RoundActions
from bot.pathfinding import bfs_shortest_path
from bot.orders import (
    compute_needed_items,
    compute_preview_items,
    items_matching_active,
    should_prefetch_preview,
)
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


ORBIT_DEFAULT_SHELF_IDS: tuple[int, int, int, int] = (72, 73, 112, 113)
ORBIT_FALLBACK_RECT: tuple[int, int, int, int] = (4, 9, 8, 15)


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


def _derive_orbit_rect_from_shelf_ids(
    state: GameState,
    shelf_ids: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    ordered = sorted(
        state.items,
        key=lambda item: (int(item.position[1]), int(item.position[0]), str(item.id)),
    )
    if max(shelf_ids) > len(ordered):
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


class WallOrbitEngine:
    """Experimental engine: clockwise ring orbit with spaced slots and opportunistic pickups."""

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
        self._loop_points: list[tuple[int, int]] = []
        self._orbit_phase: int = 0
        self._loop_spacing: int = 1
        self._slot_by_bot: dict[int, int] = {}
        self._delivery_mode: set[int] = set()
        self._deliver_route_by_bot: dict[int, list[tuple[int, int]]] = {}
        self._phase_hold_ticks: int = 0

        self.last_decision_ms: float = 0.0
        self.last_collisions_avoided: int = 0
        self.last_round_telemetry: dict[str, float] = {}
        self.last_assignment_snapshot: dict[int, dict[str, object]] = {}
        self.last_pre_collision_actions: dict[int, dict[str, object]] = {}
        self._round_wait_reason_by_bot: dict[int, str] = {}

    def _refresh_loop_points(self, state: GameState) -> None:
        rect = _derive_orbit_rect_from_shelf_ids(state, self.shelf_ids)
        if rect is None:
            rect = ORBIT_FALLBACK_RECT
        loop = _build_orbit_loop(state=state, rect=rect)
        if len(loop) < 4:
            loop = _build_orbit_loop(state=state, rect=ORBIT_FALLBACK_RECT)
        self._loop_points = loop
        if self._loop_points:
            self._orbit_phase %= len(self._loop_points)

    @staticmethod
    def _can_pick_from(bot_pos: tuple[int, int], item_pos: tuple[int, int]) -> bool:
        return abs(bot_pos[0] - item_pos[0]) + abs(bot_pos[1] - item_pos[1]) == 1

    def _slot_indices(self, bot_count: int) -> list[int]:
        if not self._loop_points:
            return []
        if bot_count <= 0:
            return []
        loop_len = len(self._loop_points)
        slots_count = min(int(bot_count), loop_len)
        if slots_count <= 0:
            return []
        # Evenly spread slots over current orbit population.
        return [
            (self._orbit_phase + (i * loop_len) // slots_count) % loop_len
            for i in range(slots_count)
        ]

    def _advance_phase(self, *, loop_len: int, steps: int = 1) -> None:
        if loop_len <= 0:
            return
        delta = int(steps) % loop_len
        if delta == 0:
            return
        self._orbit_phase = (self._orbit_phase + delta) % loop_len
        # Keep bot->slot mappings phase-relative to preserve clockwise ring spacing.
        self._slot_by_bot = {
            bid: (int(slot_idx) + delta) % loop_len
            for bid, slot_idx in self._slot_by_bot.items()
        }

    def _reassign_slots(
        self,
        *,
        bots_orbit: list[Any],
        slot_indices: list[int],
        loop_index_by_cell: dict[tuple[int, int], int],
    ) -> None:
        active_bot_ids = {int(bot.id) for bot in bots_orbit}
        valid_slots = set(slot_indices)
        loop_len = len(self._loop_points)

        # Keep stable mappings where possible.
        self._slot_by_bot = {
            bid: sidx
            for bid, sidx in self._slot_by_bot.items()
            if bid in active_bot_ids and sidx in valid_slots
        }

        bots_on_ring: list[tuple[int, int, tuple[int, int]]] = []
        bots_off_ring: list[tuple[int, tuple[int, int]]] = []
        for bot in bots_orbit:
            bid = int(bot.id)
            bpos = (int(bot.position[0]), int(bot.position[1]))
            if bpos in loop_index_by_cell:
                bots_on_ring.append((bid, int(loop_index_by_cell[bpos]), bpos))
            else:
                bots_off_ring.append((bid, bpos))

        # Assign slots to on-ring bots preserving clockwise order to avoid crossings.
        if slot_indices and bots_on_ring and loop_len > 0:
            ordered_bots = sorted(bots_on_ring, key=lambda row: (row[1], row[0]))
            best_total: int | None = None
            best_shift = 0
            for shift in range(len(slot_indices)):
                total = 0
                for i, (_bid, bidx, _bpos) in enumerate(ordered_bots):
                    sidx = int(slot_indices[(i + shift) % len(slot_indices)])
                    total += (sidx - bidx) % loop_len
                if best_total is None or total < best_total:
                    best_total = total
                    best_shift = shift
            for i, (bid, _bidx, _bpos) in enumerate(ordered_bots):
                self._slot_by_bot[bid] = int(slot_indices[(i + best_shift) % len(slot_indices)])

        used_slots = set(self._slot_by_bot.values())
        free_slots = [int(s) for s in slot_indices if int(s) not in used_slots]

        for bid, bpos in bots_off_ring:
            if bid in self._slot_by_bot:
                continue
            if not free_slots:
                break
            if bpos in loop_index_by_cell:
                bidx = loop_index_by_cell[bpos]
            else:
                bidx = -1
            if bidx >= 0:
                choice = min(free_slots, key=lambda s: ((s - bidx) % len(self._loop_points), s))
            else:
                choice = min(
                    free_slots,
                    key=lambda s: (
                        abs(bpos[0] - self._loop_points[s][0]) + abs(bpos[1] - self._loop_points[s][1]),
                        s,
                    ),
                )
            self._slot_by_bot[bid] = choice
            free_slots.remove(choice)

    def _pick_adjacent_item(
        self,
        *,
        bot_pos: tuple[int, int],
        bot_inventory_size: int,
        state: GameState,
        active_need: Counter[str],
        preview_need: Counter[str],
        reserved_item_ids: set[str],
    ) -> tuple[str, str, str] | None:
        if bot_inventory_size >= 3:
            return None
        adjacent_items = [
            item
            for item in sorted(state.items, key=lambda row: str(row.id))
            if str(item.id) not in reserved_item_ids
            and self._can_pick_from(bot_pos, (int(item.position[0]), int(item.position[1])))
        ]
        for item in adjacent_items:
            t = str(item.type)
            if active_need.get(t, 0) > 0:
                active_need[t] -= 1
                reserved_item_ids.add(str(item.id))
                return (str(item.id), t, "active")
        for item in adjacent_items:
            t = str(item.type)
            if preview_need.get(t, 0) > 0:
                preview_need[t] -= 1
                reserved_item_ids.add(str(item.id))
                return (str(item.id), t, "preview")
        return None

    def _best_step_toward(
        self,
        *,
        start: tuple[int, int],
        goal: tuple[int, int],
        grid: Grid,
        blocked: set[tuple[int, int]],
        forbidden: set[tuple[int, int]],
        prefer_axis: str,
    ) -> tuple[int, int] | None:
        candidates: list[tuple[int, int, int, int, tuple[int, int]]] = []
        for nx, ny in grid.neighbors(start[0], start[1]):
            step = (int(nx), int(ny))
            if step in blocked or step in forbidden:
                continue
            path = bfs_shortest_path(grid, step, goal, blocked=blocked)
            if path is None:
                continue
            dist = len(path)
            moved_x = int(step[0] != start[0])
            moved_y = int(step[1] != start[1])
            if prefer_axis == "x":
                pref_penalty = 0 if moved_x else 1
            elif prefer_axis == "y":
                pref_penalty = 0 if moved_y else 1
            else:
                pref_penalty = 0
            # Stable tie-breaker by row/col.
            candidates.append((dist, pref_penalty, step[1], step[0], step))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][4]

    def _route_cost(
        self,
        *,
        start: tuple[int, int],
        goal: tuple[int, int],
        waypoints: list[tuple[int, int]],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> int:
        cur = start
        total = 0
        for target in [*waypoints, goal]:
            path = bfs_shortest_path(grid, cur, target, blocked=blocked)
            if path is None:
                return 999999
            total += max(0, len(path) - 1)
            cur = target
        return total

    def _plan_delivery_route(
        self,
        *,
        start: tuple[int, int],
        drop_off: tuple[int, int],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        x_drop = int(drop_off[0])
        y_drop = int(drop_off[1])
        if self._loop_points:
            xs = [int(pt[0]) for pt in self._loop_points]
            ys = [int(pt[1]) for pt in self._loop_points]
            loop_top = min(ys)
            loop_bottom = max(ys)
            top_candidates = [pt for pt in self._loop_points if int(pt[1]) == loop_top]
            bottom_candidates = [pt for pt in self._loop_points if int(pt[1]) == loop_bottom]
            gate_top = min(top_candidates, key=lambda pt: (int(pt[0]), int(pt[1]))) if top_candidates else self._loop_points[0]
            gate_bottom = min(bottom_candidates, key=lambda pt: (int(pt[0]), int(pt[1]))) if bottom_candidates else self._loop_points[0]
        else:
            gate_top = start
            gate_bottom = start
            loop_top = int(start[1])
            loop_bottom = int(start[1])

        top_lane_y = int(loop_top)
        bottom_lane_y = int(loop_bottom + 1)
        if not grid.is_walkable(int(gate_bottom[0]), bottom_lane_y):
            bottom_lane_y = int(loop_bottom)

        top_route = [
            (int(gate_top[0]), int(gate_top[1])),
            (x_drop, top_lane_y),
            (x_drop, y_drop),
        ]
        bot_route = [
            (int(gate_bottom[0]), int(gate_bottom[1])),
            (int(gate_bottom[0]), bottom_lane_y),
            (x_drop, bottom_lane_y),
            (x_drop, y_drop),
        ]

        def _sanitize(route: list[tuple[int, int]]) -> list[tuple[int, int]]:
            out: list[tuple[int, int]] = []
            for pt in route:
                x, y = int(pt[0]), int(pt[1])
                if not grid.is_walkable(x, y):
                    continue
                if (x, y) in blocked:
                    continue
                out.append((x, y))
            return out

        top_clean = _sanitize(top_route)
        bot_clean = _sanitize(bot_route)
        top_cost = self._route_cost(start=start, goal=drop_off, waypoints=top_clean, grid=grid, blocked=blocked)
        bot_cost = self._route_cost(start=start, goal=drop_off, waypoints=bot_clean, grid=grid, blocked=blocked)
        route = top_clean if top_cost <= bot_cost else bot_clean
        return [pt for pt in route if pt != start]

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()
        self.last_assignment_snapshot = {}
        self.last_pre_collision_actions = {}
        self._round_wait_reason_by_bot = {}
        self.last_collisions_avoided = 0

        if not self._loop_points:
            self._refresh_loop_points(state)
        if not self._loop_points:
            actions = [
                BotActionCommand(bot=int(bot.id), action=BotAction.WAIT)
                for bot in sorted(state.bots, key=lambda row: int(row.id))
            ]
            self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
            self.last_round_telemetry = {
                "blocked_moves": 0.0,
                "swaps_prevented": 0.0,
                "collisions_avoided": 0.0,
                "orbit_loop_size": 0.0,
                "orbit_spacing_target": 0.0,
                "orbit_phase": float(self._orbit_phase),
                "orbit_formation_ready": 0.0,
                "orbit_pick_active": 0.0,
                "orbit_pick_preview": 0.0,
                "orbit_min_gap": 0.0,
                "wait_due_to_no_assignment": float(len(actions)),
                "wait_due_to_collision_block": 0.0,
                "wait_due_to_spacing_guard": 0.0,
            }
            return RoundActions(actions=actions)

        bots_sorted = sorted(state.bots, key=lambda row: int(row.id))
        loop_len = len(self._loop_points)
        bot_count = len(bots_sorted)
        if bot_count == 0:
            return RoundActions(actions=[])
        grid = Grid(state.grid)
        item_blocked = {
            (int(item.position[0]), int(item.position[1]))
            for item in state.items
        }
        loop_index_by_cell = {cell: idx for idx, cell in enumerate(self._loop_points)}

        active_needed_items = compute_needed_items(state)
        active_need = Counter(active_needed_items)
        preview_need = Counter(compute_preview_items(state)) if should_prefetch_preview(state) else Counter()
        reserved_item_ids: set[str] = set()
        active_remaining_total = len(active_needed_items)
        drop_off = (int(state.drop_off[0]), int(state.drop_off[1]))

        start_by_bot: dict[int, tuple[int, int]] = {
            int(bot.id): (int(bot.position[0]), int(bot.position[1]))
            for bot in bots_sorted
        }
        action_by_bot: dict[int, BotActionCommand] = {}
        movement_target_by_bot: dict[int, tuple[int, int] | None] = {}
        target_type_by_bot: dict[int, str] = {}
        slot_idx_by_bot: dict[int, int] = {}

        pick_active_count = 0
        pick_preview_count = 0
        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        occupied_now: set[tuple[int, int]] = set()
        orbit_bots: list[Any] = []
        claimed_targets: set[tuple[int, int]] = set()

        # Delivery mode management.
        active_bot_ids = {int(bot.id) for bot in bots_sorted}
        self._delivery_mode = {bid for bid in self._delivery_mode if bid in active_bot_ids}
        self._deliver_route_by_bot = {bid: route for bid, route in self._deliver_route_by_bot.items() if bid in active_bot_ids}

        delivery_cap = max(1, min(3, bot_count // 4))
        delivery_candidates: list[tuple[int, int, int, int, int]] = []
        for bot in bots_sorted:
            bid = int(bot.id)
            active_matches = items_matching_active(bot, state)
            active_match_count = len(active_matches)
            if active_match_count <= 0:
                continue
            inv_size = len(bot.inventory)
            should_queue_delivery = (
                inv_size >= 3
                or active_match_count >= 2
                or active_remaining_total == 0
            )
            if not should_queue_delivery:
                continue
            start = start_by_bot[bid]
            dist_drop = abs(int(start[0]) - int(drop_off[0])) + abs(int(start[1]) - int(drop_off[1]))
            sticky = 0 if bid in self._delivery_mode else 1
            # sticky, then more active items, fuller inventory, then shorter path.
            delivery_candidates.append((sticky, -active_match_count, -inv_size, dist_drop, bid))

        selected_delivery = {
            bid for *_rest, bid in sorted(delivery_candidates)[:delivery_cap]
        }
        self._delivery_mode = selected_delivery
        self._deliver_route_by_bot = {
            bid: route
            for bid, route in self._deliver_route_by_bot.items()
            if bid in self._delivery_mode
        }

        for bot in bots_sorted:
            bid = int(bot.id)
            # Prioritize active order delivery; preview-only inventory stays on ring.
            if bid not in self._delivery_mode:
                self._delivery_mode.discard(bid)
                self._deliver_route_by_bot.pop(bid, None)

        for bot in bots_sorted:
            bid = int(bot.id)
            if bid in self._delivery_mode:
                continue
            orbit_bots.append(bot)
        orbit_bot_ids = {int(bot.id) for bot in orbit_bots}

        orbit_count = len(orbit_bots)
        spacing = 2 if orbit_count > 0 and loop_len >= orbit_count * 2 else max(1, loop_len // max(1, orbit_count))
        self._loop_spacing = spacing
        slot_indices = self._slot_indices(orbit_count)
        self._reassign_slots(
            bots_orbit=orbit_bots,
            slot_indices=slot_indices,
            loop_index_by_cell=loop_index_by_cell,
        )

        # 1) Opportunistic pickup from ring-adjacent cells.
        for bot in orbit_bots:
            bid = int(bot.id)
            start = start_by_bot[bid]
            pick_choice = self._pick_adjacent_item(
                bot_pos=start,
                bot_inventory_size=len(bot.inventory),
                state=state,
                active_need=active_need,
                preview_need=preview_need,
                reserved_item_ids=reserved_item_ids,
            )
            if pick_choice is not None:
                item_id, item_type, source = pick_choice
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=item_id)
                movement_target_by_bot[bid] = None
                target_type_by_bot[bid] = "pick_item"
                occupied_now.add(start)
                if source == "active":
                    pick_active_count += 1
                else:
                    pick_preview_count += 1
                slot_idx_by_bot[bid] = int(self._slot_by_bot.get(bid, 0))
                self.last_assignment_snapshot[bid] = {
                    "target_type": "pick_item",
                    "target_id": item_id,
                    "pickup_pos": [int(start[0]), int(start[1])],
                    "drop_off": None,
                    "source": source,
                    "item_type": item_type,
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "phase": self._orbit_phase,
                }
                continue

        # 2) Delivery routes.
        for bot in bots_sorted:
            bid = int(bot.id)
            if bid not in self._delivery_mode:
                continue
            start = start_by_bot[bid]
            target_type_by_bot[bid] = "deliver"
            slot_idx_by_bot[bid] = int(self._slot_by_bot.get(bid, 0))
            if start == drop_off:
                if items_matching_active(bot, state):
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.DROP_OFF)
                    occupied_now.add(start)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    occupied_now.add(start)
                    self._delivery_mode.discard(bid)
                self._deliver_route_by_bot.pop(bid, None)
                movement_target_by_bot[bid] = None
                self.last_assignment_snapshot[bid] = {
                    "target_type": "deliver",
                    "target_id": None,
                    "pickup_pos": None,
                    "drop_off": [int(drop_off[0]), int(drop_off[1])],
                    "source": "deliver",
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "phase": self._orbit_phase,
                }
                continue

            if bid not in self._deliver_route_by_bot:
                self._deliver_route_by_bot[bid] = self._plan_delivery_route(
                    start=start,
                    drop_off=drop_off,
                    grid=grid,
                    blocked=set(item_blocked),
                )
            route = self._deliver_route_by_bot.get(bid, [])
            while route and route[0] == start:
                route.pop(0)
            self._deliver_route_by_bot[bid] = route
            delivery_target = route[0] if route else drop_off

            # Traffic rule: on-ring delivery bots keep clockwise flow until their planned exit gate.
            if start in loop_index_by_cell and route and route[0] in loop_index_by_cell:
                cur_idx = int(loop_index_by_cell[start])
                gate_idx = int(loop_index_by_cell[route[0]])
                if cur_idx != gate_idx:
                    nxt = self._loop_points[(cur_idx + 1) % loop_len]
                    if nxt not in claimed_targets:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                        movement_target_by_bot[bid] = nxt
                        move_plans.append((bid, start, nxt))
                        claimed_targets.add(nxt)
                    else:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                    self.last_assignment_snapshot[bid] = {
                        "target_type": "deliver",
                        "target_id": None,
                        "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                        "drop_off": [int(drop_off[0]), int(drop_off[1])],
                        "source": "deliver",
                        "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                        "phase": self._orbit_phase,
                    }
                    continue

            path = bfs_shortest_path(grid, start, delivery_target, blocked=set(item_blocked))
            if path is not None and len(path) >= 2:
                preferred = self._best_step_toward(
                    start=start,
                    goal=delivery_target,
                    grid=grid,
                    blocked=set(item_blocked),
                    forbidden=claimed_targets,
                    prefer_axis="y" if delivery_target[1] in (9, 16) else "x",
                )
                nxt = preferred if preferred is not None else (int(path[1][0]), int(path[1][1]))
                action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                movement_target_by_bot[bid] = nxt
                move_plans.append((bid, start, nxt))
                claimed_targets.add(nxt)
            else:
                fallback = bfs_shortest_path(grid, start, drop_off, blocked=set(item_blocked))
                if fallback is not None and len(fallback) >= 2:
                    nxt = (int(fallback[1][0]), int(fallback[1][1]))
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                    movement_target_by_bot[bid] = nxt
                    move_plans.append((bid, start, nxt))
                    claimed_targets.add(nxt)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target_by_bot[bid] = None
                    occupied_now.add(start)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"
            self.last_assignment_snapshot[bid] = {
                "target_type": "deliver",
                "target_id": None,
                "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                "drop_off": [int(drop_off[0]), int(drop_off[1])],
                "source": "deliver",
                "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                "phase": self._orbit_phase,
            }

        # 3) Orbit movement (clockwise only on ring).
        for bot in orbit_bots:
            bid = int(bot.id)
            if bid in action_by_bot:
                continue
            start = start_by_bot[bid]
            slot_idx = self._slot_by_bot.get(bid)
            if slot_idx is None or slot_idx not in loop_index_by_cell.values():
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                movement_target_by_bot[bid] = None
                occupied_now.add(start)
                self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"
                continue
            slot_idx_by_bot[bid] = int(slot_idx)
            goal = self._loop_points[slot_idx]
            target_type_by_bot[bid] = "orbit_wall"

            if start in loop_index_by_cell:
                cur_idx = loop_index_by_cell[start]
                if cur_idx == slot_idx:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target_by_bot[bid] = None
                    occupied_now.add(start)
                else:
                    nxt_idx = (cur_idx + 1) % loop_len
                    nxt = self._loop_points[nxt_idx]
                    if nxt in claimed_targets:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                    else:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                        movement_target_by_bot[bid] = nxt
                        move_plans.append((bid, start, nxt))
                        claimed_targets.add(nxt)
            else:
                path = bfs_shortest_path(grid, start, goal, blocked=set(item_blocked))
                if path is not None and len(path) >= 2:
                    prefer_axis = "x" if (slot_idx % 2 == 0) else "y"
                    preferred = self._best_step_toward(
                        start=start,
                        goal=goal,
                        grid=grid,
                        blocked=set(item_blocked),
                        forbidden=claimed_targets,
                        prefer_axis=prefer_axis,
                    )
                    nxt = preferred if preferred is not None else (int(path[1][0]), int(path[1][1]))
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                    movement_target_by_bot[bid] = nxt
                    move_plans.append((bid, start, nxt))
                    claimed_targets.add(nxt)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target_by_bot[bid] = None
                    occupied_now.add(start)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"

            self.last_assignment_snapshot[bid] = {
                "target_type": "orbit_wall",
                "target_id": None,
                "pickup_pos": [int(goal[0]), int(goal[1])],
                "drop_off": None,
                "source": "orbit_wall",
                "slot_idx": int(slot_idx),
                "phase": self._orbit_phase,
            }

        # 4) Resolve one-tick move collisions.
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
            start = start_by_bot[bid]
            cmd = action_by_bot.get(bid)
            movement_target = movement_target_by_bot.get(bid)

            if movement_target is not None:
                resolved_target = resolved.get(bid, start)
                if resolved_target == start:
                    cmd = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target = None
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                else:
                    cmd = BotActionCommand(bot=bid, action=action_for_move(start, resolved_target))
                    movement_target = resolved_target

            if (
                movement_target is not None
                and start in loop_index_by_cell
                and movement_target in loop_index_by_cell
            ):
                cur_idx = int(loop_index_by_cell[start])
                cw_target = self._loop_points[(cur_idx + 1) % loop_len]
                if movement_target != cw_target:
                    cmd = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target = None
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_spacing_guard"

            if cmd is None:
                cmd = BotActionCommand(bot=bid, action=BotAction.WAIT)
                movement_target = None
                self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"
            final_cmd_by_bot[bid] = cmd
            final_target_by_bot[bid] = movement_target

        # Enforce one-cell gap on ring for orbit bots after collision resolution.
        move_actions = {BotAction.MOVE_UP, BotAction.MOVE_DOWN, BotAction.MOVE_LEFT, BotAction.MOVE_RIGHT}
        for _ in range(max(1, loop_len)):
            ring_state: list[tuple[int, int, bool]] = []
            for bid in orbit_bot_ids:
                start = start_by_bot.get(bid)
                if start not in loop_index_by_cell:
                    continue
                cmd = final_cmd_by_bot.get(bid)
                tgt = final_target_by_bot.get(bid)
                moved = bool(
                    cmd is not None
                    and cmd.action in move_actions
                    and tgt is not None
                    and tgt in loop_index_by_cell
                    and tgt != start
                )
                idx_now = int(loop_index_by_cell[tgt]) if moved else int(loop_index_by_cell[start])
                ring_state.append((idx_now, bid, moved))
            if len(ring_state) < 2:
                break
            ring_state.sort(key=lambda row: row[0])
            violating_bid: int | None = None
            for i, (cur_idx, cur_bid, cur_moved) in enumerate(ring_state):
                nxt_idx, _nxt_bid, _nxt_moved = ring_state[(i + 1) % len(ring_state)]
                gap = (nxt_idx - cur_idx) % loop_len
                if gap >= 2 or gap == 0:
                    continue
                if cur_moved:
                    violating_bid = int(cur_bid)
                    break
            if violating_bid is None:
                break
            final_cmd_by_bot[violating_bid] = BotActionCommand(bot=violating_bid, action=BotAction.WAIT)
            final_target_by_bot[violating_bid] = None
            self._round_wait_reason_by_bot[violating_bid] = "wait_due_to_spacing_guard"

        final_actions: list[BotActionCommand] = []
        for bot in bots_sorted:
            bid = int(bot.id)
            start = start_by_bot[bid]
            cmd = final_cmd_by_bot.get(bid, BotActionCommand(bot=bid, action=BotAction.WAIT))
            movement_target = final_target_by_bot.get(bid)
            final_actions.append(cmd)
            self.last_pre_collision_actions[bid] = {
                "bot_id": bid,
                "start": [int(start[0]), int(start[1])],
                "action": str(cmd.action.value),
                "item_id": cmd.item_id,
                "target_type": target_type_by_bot.get(bid, "orbit_wall"),
                "movement_target": [int(movement_target[0]), int(movement_target[1])] if movement_target is not None else None,
                "slot_idx": int(slot_idx_by_bot.get(bid, 0)),
                "phase": self._orbit_phase,
            }

        occupied_idx = [
            loop_index_by_cell[start_by_bot[bid]]
            for bid in orbit_bot_ids
            if start_by_bot.get(bid) in loop_index_by_cell
        ]
        unique_idx = sorted(set(occupied_idx))

        min_gap = 0
        if len(unique_idx) >= 2:
            cyclic_gaps = []
            for i, cur in enumerate(unique_idx):
                nxt = unique_idx[(i + 1) % len(unique_idx)]
                gap = (nxt - cur) % loop_len
                if gap == 0:
                    continue
                cyclic_gaps.append(gap)
            if cyclic_gaps:
                min_gap = min(cyclic_gaps)

        orbit_on_ring = [
            bid for bid in orbit_bot_ids
            if start_by_bot.get(bid) in loop_index_by_cell
        ]
        slot_targets = {self._slot_by_bot[bid] for bid in orbit_bot_ids if bid in self._slot_by_bot}
        orbit_slots_occupied = {
            loop_index_by_cell[start_by_bot[bid]]
            for bid in orbit_on_ring
        }
        formation_ready = bool(orbit_bot_ids) and orbit_slots_occupied == slot_targets

        any_pick = (pick_active_count + pick_preview_count) > 0
        if not any_pick:
            if formation_ready:
                self._advance_phase(loop_len=loop_len, steps=1)
                self._phase_hold_ticks = 0
            elif len(orbit_on_ring) == len(orbit_bot_ids):
                # Hold phase while bots settle into spaced slots.
                self._phase_hold_ticks = 0
            else:
                self._phase_hold_ticks += 1
                if self._phase_hold_ticks >= 3:
                    self._advance_phase(loop_len=loop_len, steps=1)
                    self._phase_hold_ticks = 0

        wait_no_assignment = sum(
            1
            for reason in self._round_wait_reason_by_bot.values()
            if reason == "wait_due_to_no_assignment"
        )
        wait_collision = sum(
            1
            for reason in self._round_wait_reason_by_bot.values()
            if reason == "wait_due_to_collision_block"
        )

        self.last_collisions_avoided = blocked_moves
        self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
        self.last_round_telemetry = {
            "blocked_moves": float(blocked_moves),
            "swaps_prevented": float(swaps_prevented),
            "collisions_avoided": float(blocked_moves),
            "orbit_loop_size": float(loop_len),
            "orbit_spacing_target": float(spacing),
            "orbit_phase": float(self._orbit_phase),
            "orbit_formation_ready": 1.0 if formation_ready else 0.0,
            "orbit_pick_active": float(pick_active_count),
            "orbit_pick_preview": float(pick_preview_count),
            "orbit_min_gap": float(min_gap),
            "orbit_bots": float(len(orbit_bot_ids)),
            "deliver_bots": float(len(self._delivery_mode)),
            "wait_due_to_no_assignment": float(wait_no_assignment),
            "wait_due_to_collision_block": float(wait_collision),
            "wait_due_to_spacing_guard": 0.0,
        }
        return RoundActions(actions=final_actions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NMiAI Grocery Bot live runner")
    parser.add_argument("--difficulty", type=str, default="expert", choices=["easy", "medium", "hard", "expert"])
    parser.add_argument("--runs", type=int, default=1, help="Number of live sessions to run")
    parser.add_argument("--cooldown-sec", type=float, default=3.0, help="Cooldown between runs")
    parser.add_argument("--max-live-runs", type=int, default=DEFAULT_MAX_LIVE_RUNS, help="Safety cap for live runs")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic tie-break seed (0 keeps strict deterministic ordering)")
    parser.add_argument("--show-max", action="store_true", help="Print max-score exact value or bound from round 0")
    parser.add_argument("--record", action="store_true", help="Write run artifacts under .seed_artifacts/")
    parser.add_argument(
        "--record-order-trace",
        action="store_true",
        help="Record compact per-round order snapshots to artifact",
    )
    parser.add_argument(
        "--save-states",
        action="store_true",
        help="Store full game state in round logs (large files)",
    )
    parser.add_argument(
        "--capture-decision-debug",
        action="store_true",
        help="Capture per-bot assignment/path debug from DecisionEngine",
    )
    parser.add_argument(
        "--record-decision-trace",
        action="store_true",
        help="Write per-round decision trace with state, telemetry, and bot debug to artifact",
    )
    parser.add_argument(
        "--record-item-spawn-trace",
        action="store_true",
        help="Write per-round item spawn/despawn trace to artifact",
    )
    parser.add_argument(
        "--max-logs",
        action="store_true",
        help="Enable all high-volume logging flags (record, states, order trace, decision trace, item trace)",
    )
    parser.add_argument(
        "--orbit-wall",
        action="store_true",
        help="Experimental: ignore scoring and move bots in a loop around the wall near shelf ids 72/73..112/113",
    )
    parser.add_argument(
        "--orbit-shelf-ids",
        type=str,
        default="72,73,112,113",
        help="Comma-separated shelf IDs (top-left, top-right, bottom-left, bottom-right) used to derive orbit wall",
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose client/engine logging")
    parser.add_argument(
        "--params-file",
        type=str,
        default="",
        help="Optional JSON file with DecisionConfig params to override CLI strategy flags",
    )
    parser.add_argument("--use-astar", action="store_true", help="Use A* for movement pathfinding")
    parser.add_argument("--lookahead-k", type=int, default=2, help="Order lookahead depth")
    parser.add_argument("--active-weight", type=float, default=10.0, help="Active-order utility weight")
    parser.add_argument("--preview-weight", type=float, default=4.0, help="Preview-order utility weight")
    parser.add_argument(
        "--zone-penalty",
        type=float,
        default=0.0,
        help="Extra distance penalty to keep each bot near its lane/zone",
    )
    parser.add_argument(
        "--dropoff-threshold",
        type=float,
        default=0.67,
        help="Fraction of active order completion that triggers delivery priority",
    )
    parser.add_argument(
        "--collision-aggressiveness",
        type=str,
        default="wait",
        choices=["wait", "detour"],
        help="Collision fallback behavior for blocked bots",
    )
    parser.add_argument("--dist-weight", type=float, default=1.0, help="Distance penalty weight bot->pickup")
    parser.add_argument("--dropoff-dist-weight", type=float, default=0.35, help="Distance penalty weight pickup->dropoff")
    parser.add_argument("--congestion-weight", type=float, default=1.0, help="Congestion penalty weight")
    parser.add_argument("--collision-risk-weight", type=float, default=1.0, help="Collision-risk penalty weight")
    parser.add_argument("--replan-penalty-weight", type=float, default=1.0, help="Target switch (replan) penalty weight")
    parser.add_argument(
        "--carry-home-bias-weight",
        type=float,
        default=0.0,
        help="Penalty for continuing to pick while already carrying active-matching items far from drop-off",
    )
    parser.add_argument("--urgency-weight", type=float, default=1.0, help="Urgency multiplier for target utility")
    parser.add_argument(
        "--trip-chain-bonus-weight",
        type=float,
        default=0.0,
        help="Bonus for pickups that enable a short second active-item pickup before drop-off",
    )
    parser.add_argument("--future-depth-decay", type=float, default=1.0, help="Future-order utility decay by depth")
    parser.add_argument(
        "--future-count-weight",
        type=float,
        default=0.0,
        help="Additional utility per outstanding future demand count for a prefetch item type",
    )
    parser.add_argument("--future-prefetch-bonus", type=int, default=0, help="Extra prefetch slot budget in oracle mode")
    parser.add_argument(
        "--future-priority-mode",
        type=str,
        default="depth",
        choices=["depth", "flat"],
        help="Future-order rank priority mode",
    )
    parser.add_argument(
        "--prefetch-min-completion",
        type=float,
        default=0.0,
        help="Minimum active-order completion ratio before prefetch is allowed",
    )
    parser.add_argument(
        "--prefetch-spare-slots",
        type=int,
        default=0,
        help="Global spare inventory slots kept free while active order is incomplete",
    )
    parser.add_argument(
        "--prefetch-nonmatching-cap",
        type=int,
        default=3,
        help="Max non-matching items a bot may hold while still prefetching",
    )
    parser.add_argument(
        "--strict-active-priority",
        dest="strict_active_priority",
        action="store_true",
        default=False,
        help="If active targets are reachable, avoid preview prefetch assignments",
    )
    parser.add_argument(
        "--disable-strict-active-priority",
        dest="strict_active_priority",
        action="store_false",
        help="Allow preview prefetch even while active targets are reachable",
    )
    parser.add_argument(
        "--strict-active-release-completion",
        type=float,
        default=1.0,
        help="When strict-active mode is on, allow prefetch once active completion reaches this ratio",
    )
    parser.add_argument(
        "--force-dropoff-for-full-nonmatching",
        dest="force_dropoff_for_full_nonmatching",
        action="store_true",
        default=False,
        help="If bot inventory is full with non-matching items, force it to stage at drop-off",
    )
    parser.add_argument(
        "--disable-force-dropoff-for-full-nonmatching",
        dest="force_dropoff_for_full_nonmatching",
        action="store_false",
        help="Disable drop-off staging for full non-matching inventory",
    )
    parser.add_argument(
        "--always-deliver-matching",
        dest="always_deliver_matching",
        action="store_true",
        default=False,
        help="Always route bots with active-matching items to drop-off",
    )
    parser.add_argument(
        "--disable-always-deliver-matching",
        dest="always_deliver_matching",
        action="store_false",
        help="Allow bots to keep collecting before delivering matching items",
    )
    parser.add_argument(
        "--avoid-dropoff-block-when-matching",
        dest="avoid_dropoff_block_when_matching",
        action="store_true",
        default=True,
        help="Keep full non-matching bots away from drop-off if teammates can deliver active items",
    )
    parser.add_argument(
        "--disable-avoid-dropoff-block-when-matching",
        dest="avoid_dropoff_block_when_matching",
        action="store_false",
        help="Allow full non-matching bots to stage at drop-off even if teammates have matching items",
    )
    parser.add_argument(
        "--max-concurrent-deliverers",
        type=int,
        default=2,
        help="Limit bots simultaneously assigned to deliver each tick (<=0 means unlimited)",
    )
    parser.add_argument(
        "--adaptive-deliver-queue",
        dest="adaptive_deliver_queue",
        action="store_true",
        default=False,
        help="Adapt deliver queue size to active-order progress and endgame pressure",
    )
    parser.add_argument(
        "--disable-adaptive-deliver-queue",
        dest="adaptive_deliver_queue",
        action="store_false",
        help="Use fixed max-concurrent-deliverers without adaptive queue sizing",
    )
    parser.add_argument("--deliver-queue-min", type=int, default=1, help="Minimum adaptive delivery queue size")
    parser.add_argument("--deliver-queue-max", type=int, default=3, help="Maximum adaptive delivery queue size")
    parser.add_argument(
        "--assignment-strategy",
        type=str,
        default="greedy",
        choices=["greedy", "auction"],
        help="Item assignment strategy",
    )
    parser.add_argument("--reservation-horizon", type=int, default=2, help="Reservation horizon for collision handling")
    parser.add_argument("--hysteresis-penalty", type=float, default=2.0, help="Anti-oscillation hysteresis penalty")
    parser.add_argument(
        "--sticky-target-bonus",
        type=float,
        default=0.0,
        help="Utility bonus for keeping the same target item id as previous tick",
    )
    parser.add_argument(
        "--early-deliver-matching-count",
        type=int,
        default=0,
        help="If >0, allow early deliver when bot has at least this many active-matching items",
    )
    parser.add_argument(
        "--early-deliver-inventory-threshold",
        type=int,
        default=2,
        help="Minimum inventory size required for early-deliver rule",
    )
    parser.add_argument(
        "--endgame-disable-prefetch-rounds",
        type=int,
        default=0,
        help="Rounds-to-end threshold where preview prefetch is disabled",
    )
    parser.add_argument(
        "--endgame-force-deliver-rounds",
        type=int,
        default=0,
        help="Rounds-to-end threshold where bots with matching items prioritize delivery",
    )
    parser.add_argument(
        "--endgame-strict-active",
        dest="endgame_strict_active",
        action="store_true",
        default=False,
        help="Force strict active-order priority in endgame window",
    )
    parser.add_argument(
        "--disable-endgame-strict-active",
        dest="endgame_strict_active",
        action="store_false",
        help="Disable strict active-order priority in endgame window",
    )
    parser.add_argument(
        "--avoid-immediate-backtrack",
        dest="avoid_immediate_backtrack",
        action="store_true",
        default=True,
        help="Avoid immediate A-B-A reversals when an alternative step is available",
    )
    parser.add_argument(
        "--disable-avoid-immediate-backtrack",
        dest="avoid_immediate_backtrack",
        action="store_false",
        help="Disable immediate backtrack guard",
    )
    parser.add_argument(
        "--backtrack-slack",
        type=int,
        default=1,
        help="Allowed extra distance when selecting non-backtracking alternative step",
    )
    parser.add_argument(
        "--wait-on-backtrack-conflict",
        dest="wait_on_backtrack_conflict",
        action="store_true",
        default=False,
        help="If no good non-backtracking move exists, wait instead of reversing",
    )
    parser.add_argument(
        "--disable-wait-on-backtrack-conflict",
        dest="wait_on_backtrack_conflict",
        action="store_false",
        help="Allow reverse step when no good alternative exists",
    )
    parser.add_argument(
        "--pickup-fail-blacklist-threshold",
        type=int,
        default=2,
        help="Number of failed pick_up attempts before item id is temporarily blacklisted",
    )
    parser.add_argument(
        "--pickup-fail-blacklist-rounds",
        type=int,
        default=40,
        help="Temporary blacklist duration (rounds) for repeatedly failing item ids",
    )
    parser.add_argument(
        "--stall-round-threshold",
        type=int,
        default=24,
        help="Rounds without active-order progress before entering recovery mode",
    )
    parser.add_argument(
        "--stall-recovery-rounds",
        type=int,
        default=40,
        help="Duration of recovery mode once triggered",
    )
    parser.add_argument(
        "--stall-recovery-preview-weight",
        type=float,
        default=0.0,
        help="Preview weight override in recovery mode",
    )
    parser.add_argument(
        "--stall-recovery-force-dropoff",
        dest="stall_recovery_force_dropoff",
        action="store_true",
        default=True,
        help="Force full non-matching bots to drop-off while in recovery mode",
    )
    parser.add_argument(
        "--disable-stall-recovery-force-dropoff",
        dest="stall_recovery_force_dropoff",
        action="store_false",
        help="Disable forced drop-off in recovery mode",
    )
    parser.add_argument(
        "--stall-recovery-strict-active",
        dest="stall_recovery_strict_active",
        action="store_true",
        default=True,
        help="Enforce strict active-order priority while in recovery mode",
    )
    parser.add_argument(
        "--disable-stall-recovery-strict-active",
        dest="stall_recovery_strict_active",
        action="store_false",
        help="Disable strict active-order priority in recovery mode",
    )
    parser.add_argument(
        "--clear-adjacent-dropoff-lane",
        dest="clear_adjacent_dropoff_lane",
        action="store_true",
        default=False,
        help="Move non-matching adjacent bots away from drop-off when carriers are approaching",
    )
    parser.add_argument(
        "--disable-clear-adjacent-dropoff-lane",
        dest="clear_adjacent_dropoff_lane",
        action="store_false",
        help="Disable adjacent drop-off lane clearing",
    )
    parser.add_argument(
        "--clear-lane-distance",
        type=int,
        default=4,
        help="Max Manhattan distance from drop-off for matching carriers that trigger lane clearing",
    )
    parser.add_argument(
        "--allow-same-shelf-for-same-type",
        dest="allow_same_shelf_for_same_type",
        action="store_true",
        default=False,
        help="Allow multiple bots to target the same shelf for duplicate active item types",
    )
    parser.add_argument(
        "--disable-allow-same-shelf-for-same-type",
        dest="allow_same_shelf_for_same_type",
        action="store_false",
        help="Keep spreading duplicate item picks across different shelves when possible",
    )
    parser.add_argument(
        "--stage-nonmatching-when-active-covered",
        dest="stage_nonmatching_when_active_covered",
        action="store_true",
        default=False,
        help="When active order is already covered by team inventory, stage non-matching bots toward drop-off",
    )
    parser.add_argument(
        "--disable-stage-nonmatching-when-active-covered",
        dest="stage_nonmatching_when_active_covered",
        action="store_false",
        help="Disable non-matching staging while active order is inventory-covered",
    )
    parser.add_argument(
        "--stage-nonmatching-endgame-rounds",
        type=int,
        default=0,
        help="Enable non-matching staging in the final N rounds even when global staging is disabled",
    )
    parser.add_argument(
        "--order-forecast-source",
        type=str,
        default="none",
        choices=["none", "snapshot", "mine", "simulate"],
        help="Optional order forecast source (works for any difficulty when artifacts exist)",
    )
    parser.add_argument(
        "--order-forecast-snapshot",
        type=str,
        default="",
        help="Snapshot path used when --order-forecast-source=snapshot (default: artifacts/<difficulty>/dataset_snapshot_v1.json)",
    )
    parser.add_argument(
        "--order-forecast-artifact-root",
        type=str,
        default="",
        help="Artifact root used when --order-forecast-source=mine/simulate (default: .seed_artifacts/nmiai/<difficulty>)",
    )
    parser.add_argument("--tie-break-dynamic", action="store_true", help="Use dynamic tie-break salt by active order index")
    parser.add_argument(
        "--artifact-root",
        type=str,
        default=".seed_artifacts/nmiai",
        help="Artifact root directory",
    )
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


def _apply_params_file_to_args(args: argparse.Namespace) -> None:
    setattr(args, "_raw_params_file", {})
    if not args.params_file:
        return
    payload = json.loads(Path(args.params_file).read_text(encoding="utf-8-sig"))
    raw: dict[str, Any] = {}
    if isinstance(payload, dict):
        if isinstance(payload.get("params"), dict):
            raw = dict(payload["params"])
        elif isinstance(payload.get("config"), dict):
            raw = dict(payload["config"])
        elif isinstance(payload.get("strategy"), dict):
            raw = dict(payload["strategy"])
        else:
            raw = dict(payload)
    mapping = {
        "lookahead_orders": "lookahead_k",
        "dropoff_completion_threshold": "dropoff_threshold",
        "zone_penalty_weight": "zone_penalty",
        "tie_break_seed": "seed",
    }
    for key, value in raw.items():
        target = mapping.get(key, key)
        if hasattr(args, target):
            setattr(args, target, value)
    setattr(args, "_raw_params_file", raw)


def _default_dataset_snapshot_path(difficulty: str) -> Path:
    diff = str(difficulty or "expert").strip().lower() or "expert"
    return Path("artifacts") / diff / "dataset_snapshot_v1.json"


def _load_order_forecast(
    args: argparse.Namespace,
    *,
    session: GameSession | None = None,
) -> dict[int, list[str]] | None:
    source = str(getattr(args, "order_forecast_source", "none") or "none").strip().lower()
    if source == "none":
        return None
    difficulty = str(getattr(args, "difficulty", "")).strip().lower()
    default_artifact_root = (
        f".seed_artifacts/nmiai/{difficulty}" if difficulty else ".seed_artifacts/nmiai/expert"
    )
    artifact_root = str(
        getattr(args, "order_forecast_artifact_root", "") or default_artifact_root
    )
    try:
        from bot._simulator import (
            default_generator_from_dataset,
            load_dataset_snapshot,
            mine_dataset,
            synthesize_orders,
        )
    except Exception:
        return None

    try:
        if source == "snapshot":
            raw_snapshot = str(getattr(args, "order_forecast_snapshot", "") or "").strip()
            snap_path = Path(raw_snapshot) if raw_snapshot else _default_dataset_snapshot_path(difficulty)
            if snap_path.exists():
                dataset = load_dataset_snapshot(snap_path)
            else:
                dataset = mine_dataset(artifact_root)
        else:
            dataset = mine_dataset(artifact_root)
    except Exception:
        return None

    if source == "simulate":
        try:
            map_seed = int(session.map_seed) if session is not None else 7002
            generator = default_generator_from_dataset(
                dataset,
                seed=map_seed,
                known_orders_mode="latest",
            )
            orders = synthesize_orders(dataset, generator)
        except Exception:
            return None
        return {
            idx: list(order.get("items_required", []))
            for idx, order in enumerate(orders)
        }

    return {idx: list(req) for idx, req in enumerate(dataset.observed_orders_exact)}


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

    cfg = DecisionConfig(
        lookahead_orders=args.lookahead_k,
        active_weight=args.active_weight,
        preview_weight=args.preview_weight,
        dropoff_completion_threshold=args.dropoff_threshold,
        zone_penalty_weight=args.zone_penalty,
        dist_weight=args.dist_weight,
        dropoff_dist_weight=args.dropoff_dist_weight,
        congestion_weight=args.congestion_weight,
        collision_risk_weight=args.collision_risk_weight,
        replan_penalty_weight=args.replan_penalty_weight,
        carry_home_bias_weight=args.carry_home_bias_weight,
        urgency_weight=args.urgency_weight,
        trip_chain_bonus_weight=args.trip_chain_bonus_weight,
        future_depth_decay=args.future_depth_decay,
        future_count_weight=args.future_count_weight,
        future_prefetch_bonus=args.future_prefetch_bonus,
        future_priority_mode=args.future_priority_mode,
        prefetch_min_completion=args.prefetch_min_completion,
        prefetch_spare_slots=args.prefetch_spare_slots,
        prefetch_nonmatching_cap=args.prefetch_nonmatching_cap,
        strict_active_priority=bool(args.strict_active_priority),
        strict_active_release_completion=args.strict_active_release_completion,
        force_dropoff_for_full_nonmatching=bool(args.force_dropoff_for_full_nonmatching),
        always_deliver_matching=bool(args.always_deliver_matching),
        avoid_dropoff_block_when_matching=bool(args.avoid_dropoff_block_when_matching),
        max_concurrent_deliverers=args.max_concurrent_deliverers,
        adaptive_deliver_queue=bool(args.adaptive_deliver_queue),
        deliver_queue_min=args.deliver_queue_min,
        deliver_queue_max=args.deliver_queue_max,
        assignment_strategy=args.assignment_strategy,
        reservation_horizon=args.reservation_horizon,
        hysteresis_penalty=args.hysteresis_penalty,
        sticky_target_bonus=args.sticky_target_bonus,
        early_deliver_matching_count=args.early_deliver_matching_count,
        early_deliver_inventory_threshold=args.early_deliver_inventory_threshold,
        endgame_disable_prefetch_rounds=args.endgame_disable_prefetch_rounds,
        endgame_force_deliver_rounds=args.endgame_force_deliver_rounds,
        endgame_strict_active=bool(args.endgame_strict_active),
        avoid_immediate_backtrack=bool(args.avoid_immediate_backtrack),
        backtrack_slack=args.backtrack_slack,
        wait_on_backtrack_conflict=bool(args.wait_on_backtrack_conflict),
        pickup_fail_blacklist_threshold=args.pickup_fail_blacklist_threshold,
        pickup_fail_blacklist_rounds=args.pickup_fail_blacklist_rounds,
        stall_round_threshold=args.stall_round_threshold,
        stall_recovery_rounds=args.stall_recovery_rounds,
        stall_recovery_preview_weight=args.stall_recovery_preview_weight,
        stall_recovery_force_dropoff=bool(args.stall_recovery_force_dropoff),
        stall_recovery_strict_active=bool(args.stall_recovery_strict_active),
        clear_adjacent_dropoff_lane=bool(args.clear_adjacent_dropoff_lane),
        clear_lane_distance=args.clear_lane_distance,
        allow_same_shelf_for_same_type=bool(args.allow_same_shelf_for_same_type),
        stage_nonmatching_when_active_covered=bool(args.stage_nonmatching_when_active_covered),
        stage_nonmatching_endgame_rounds=args.stage_nonmatching_endgame_rounds,
        collision_aggressiveness=args.collision_aggressiveness,
        tie_break_seed=args.seed,
        tie_break_dynamic=bool(args.tie_break_dynamic),
    )
    raw_params = getattr(args, "_raw_params_file", {})
    if isinstance(raw_params, dict) and raw_params:
        merged = cfg.to_dict()
        for key, value in raw_params.items():
            if key in merged:
                merged[key] = value
        cfg = DecisionConfig(**merged)
    order_forecast = _load_order_forecast(args, session=session)
    if args.orbit_wall:
        engine = WallOrbitEngine(
            debug=args.debug,
            reservation_horizon=max(1, int(args.reservation_horizon)),
            shelf_ids=getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS),
        )
    else:
        engine = DecisionEngine(
            use_astar=args.use_astar,
            debug=args.debug,
            verbose=False,
            config=cfg,
            order_forecast=order_forecast,
            capture_debug=bool(args.capture_decision_debug or args.record_decision_trace),
        )
    logger = RoundLogger(
        log_dir=str(log_dir),
        difficulty=args.difficulty,
        save_states=bool(args.save_states),
    )

    state0_raw: dict[str, Any] | None = None
    order_trace: list[dict[str, Any]] = []
    game_over_obj: GameOver | None = None
    decision_samples: list[float] = []
    idle_steps = 0
    collisions_avoided = 0
    tracker = OrderTracker(difficulty=args.difficulty)
    show_max_printed = False
    last_state_raw: dict[str, Any] | None = None
    last_state_round = -1
    item_spawn_trace: list[dict[str, Any]] = []
    prev_items_by_id: dict[str, dict[str, Any]] = {}

    def on_state(state: GameState, raw: dict[str, Any]) -> None:
        nonlocal state0_raw, show_max_printed, last_state_raw, last_state_round, prev_items_by_id
        tracker.update(state)
        last_state_raw = raw
        last_state_round = int(state.round)
        if args.record_item_spawn_trace:
            current_items_by_id: dict[str, dict[str, Any]] = {}
            for item in raw.get("items", []):
                item_id = str(item.get("id", ""))
                if not item_id:
                    continue
                pos_raw = item.get("position", [0, 0])
                pos = [0, 0]
                if isinstance(pos_raw, (list, tuple)) and len(pos_raw) == 2:
                    pos = [int(pos_raw[0]), int(pos_raw[1])]
                current_items_by_id[item_id] = {
                    "id": item_id,
                    "type": str(item.get("type", "")),
                    "position": pos,
                }
            new_item_ids = sorted(item_id for item_id in current_items_by_id if item_id not in prev_items_by_id)
            removed_item_ids = sorted(item_id for item_id in prev_items_by_id if item_id not in current_items_by_id)
            moved_items: list[dict[str, Any]] = []
            for item_id in sorted(set(current_items_by_id) & set(prev_items_by_id)):
                prev_pos = prev_items_by_id[item_id].get("position")
                now_pos = current_items_by_id[item_id].get("position")
                if prev_pos != now_pos:
                    moved_items.append(
                        {
                            "id": item_id,
                            "from": prev_pos,
                            "to": now_pos,
                        }
                    )
            item_spawn_trace.append(
                {
                    "round": int(state.round),
                    "score": int(state.score),
                    "active_order_index": int(state.active_order_index),
                    "new_item_ids": new_item_ids,
                    "removed_item_ids": removed_item_ids,
                    "moved_items": moved_items,
                    "items": [current_items_by_id[item_id] for item_id in sorted(current_items_by_id)],
                }
            )
            prev_items_by_id = current_items_by_id
        if args.record_order_trace:
            active = None
            preview = None
            for order in raw.get("orders", []):
                if order.get("status") == "active":
                    active = {
                        "id": order.get("id"),
                        "items_required": list(order.get("items_required", [])),
                        "items_delivered": list(order.get("items_delivered", [])),
                    }
                elif order.get("status") == "preview":
                    preview = {
                        "id": order.get("id"),
                        "items_required": list(order.get("items_required", [])),
                        "items_delivered": list(order.get("items_delivered", [])),
                    }
            order_trace.append(
                {
                    "round": state.round,
                    "score": state.score,
                    "active_order_index": state.active_order_index,
                    "active": active,
                    "preview": preview,
                }
            )
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
        decision_samples.append(engine.last_decision_ms)
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
                "decision_ms": float(engine.last_decision_ms),
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
                "use_astar": bool(args.use_astar),
                "show_max": bool(args.show_max),
                "order_forecast_source": str(args.order_forecast_source),
                "capture_decision_debug": bool(args.capture_decision_debug),
                "record_decision_trace": bool(args.record_decision_trace),
                "record_item_spawn_trace": bool(args.record_item_spawn_trace),
                "orbit_wall": bool(args.orbit_wall),
                "orbit_shelf_ids": list(getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS)),
                "strategy": cfg.to_dict(),
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
        if args.record_order_trace:
            _write_json(artifact_dir / "order_trace.json", {"trace": order_trace})
        if args.record_item_spawn_trace:
            _write_json(artifact_dir / "item_spawn_trace.json", {"trace": item_spawn_trace})
        (artifact_dir / "log.txt").write_text(
            "\n".join(
                [
                    f"run_index={run_index}",
                    f"difficulty={args.difficulty}",
                    f"map_seed={session.map_seed}",
                    f"ws_url={redact_ws_url(session.ws_url)}",
                    f"order_forecast_source={args.order_forecast_source}",
                    f"orbit_wall={args.orbit_wall}",
                    f"orbit_shelf_ids={getattr(args, '_orbit_shelf_ids', ORBIT_DEFAULT_SHELF_IDS)}",
                    f"score={summary.score}",
                    f"items_delivered={summary.items_delivered}",
                    f"orders_completed={summary.orders_completed}",
                    f"rounds_played={summary.rounds_played}",
                    f"idle_steps={summary.idle_steps}",
                    f"collisions_avoided={summary.collisions_avoided}",
                    f"avg_decision_ms={summary.avg_decision_ms:.3f}",
                    f"logger_path={logger.log_path}",
                    f"decision_trace_path={decision_trace_path}" if decision_trace_path is not None else "decision_trace_path=",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    print(
        f"[run {run_index}] score={summary.score} "
        f"items={summary.items_delivered} orders={summary.orders_completed} "
        f"idle={summary.idle_steps} avg_ms={summary.avg_decision_ms:.2f}"
    )
    return summary


async def async_main() -> None:
    args = parse_args()
    _apply_params_file_to_args(args)
    if args.orbit_wall:
        args._orbit_shelf_ids = _parse_orbit_shelf_ids(args.orbit_shelf_ids)
    else:
        args._orbit_shelf_ids = ORBIT_DEFAULT_SHELF_IDS
    if args.max_logs:
        args.record = True
        args.record_order_trace = True
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
        run_summary = await run_live_once(run_index=idx + 1, args=args)
        summaries.append(run_summary)

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


















