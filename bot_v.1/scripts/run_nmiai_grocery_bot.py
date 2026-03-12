"""Live runner for NMiAI Grocery Bot with artifact recording."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
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
from bot.orbit_flow_engine import OrbitFlowEngine
from bot.pathfinding import bfs_shortest_path
from bot.orders import (
    get_active_order,
    get_preview_order,
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
ORBIT_FIXED_BRANCH_EXIT: tuple[int, int] = (8, 15)
ORBIT_FIXED_BRANCH_CONTINUE: tuple[int, int] = (7, 15)
ORBIT_FIXED_DELIVERY_ENTRY: tuple[int, int] = (8, 16)
ORBIT_FIXED_REJOIN_BRANCH: tuple[int, int] = (3, 15)
ORBIT_FIXED_DROPOFF: tuple[int, int] = (1, 16)
ORBIT_FIXED_RETURN_BUFFER: tuple[tuple[int, int], ...] = ((3, 16), (2, 16))
ORBIT_MODE = "orbit_wall"
MOBILITY_ORBIT = "orbit"
DELIVER_MODE = "deliver"
QUEUE_MODE = "queue"
RETURN_MODE = "return"
BOOT_RELEASE_MODE = "boot_release"
PICK_DETOUR_MODE = "pick_detour"
DELIVER_OUTBOUND_MODE = "deliver_outbound"
DROP_QUEUE_MODE = "drop_queue"
RETURN_BUFFER_MODE = "return_buffer"
REJOIN_MODE = "rejoin"
CARGO_ACTIVE_ONLY = "active_only"
CARGO_MIXED_ACTIVE_PREVIEW = "mixed_active_preview"
CARGO_PREVIEW_HELD = "preview_held"
CARGO_DEADWEIGHT = "deadweight"
FLOW_BOOT_RELEASE = "boot_release"
FLOW_BALANCED_HARVEST = "balanced_harvest"
FLOW_FINISH_WAVE = "finish_wave"


@dataclass
class TopologyGraph:
    ring_cells: list[tuple[int, int]]
    ring_index: dict[tuple[int, int], int]
    entry_gates: list[tuple[int, int]]
    exit_gates: list[tuple[int, int]]
    pickup_adj_cells: set[tuple[int, int]]
    delivery_corridors: dict[str, list[tuple[int, int]]]
    drop_queue_cells: list[tuple[int, int]]
    return_buffer_cells: list[tuple[int, int]]
    stop_line: tuple[int, int]
    drop_off: tuple[int, int]
    branch_exit: tuple[int, int]
    branch_continue: tuple[int, int]
    delivery_entry: tuple[int, int]
    rejoin_branch: tuple[int, int]


@dataclass
class DemandLedger:
    active_need: Counter[str] = field(default_factory=Counter)
    active_reserved: Counter[str] = field(default_factory=Counter)
    active_deficit: Counter[str] = field(default_factory=Counter)
    active_serviceable_deficit: Counter[str] = field(default_factory=Counter)
    preview_need: Counter[str] = field(default_factory=Counter)
    active_carried: Counter[str] = field(default_factory=Counter)
    preview_carried: Counter[str] = field(default_factory=Counter)
    active_committed: Counter[str] = field(default_factory=Counter)
    preview_contingent: Counter[str] = field(default_factory=Counter)
    preview_reserved: Counter[str] = field(default_factory=Counter)
    slot_safety_stock: int = 0


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


def _parse_xy(raw: str, *, arg_name: str) -> tuple[int, int]:
    text = str(raw).strip()
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) != 2:
        raise SystemExit(f"{arg_name} must be in 'x,y' format")
    try:
        x, y = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise SystemExit(f"{arg_name} has invalid integer coordinates: {raw!r}") from exc
    return (x, y)


def _parse_xy_list(raw: str, *, arg_name: str) -> tuple[tuple[int, int], ...]:
    text = str(raw).strip()
    if not text:
        return tuple()
    out: list[tuple[int, int]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        out.append(_parse_xy(chunk, arg_name=arg_name))
    return tuple(out)


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
        migration_stage: int = 5,
        branch_exit: tuple[int, int] = ORBIT_FIXED_BRANCH_EXIT,
        branch_continue: tuple[int, int] = ORBIT_FIXED_BRANCH_CONTINUE,
        delivery_entry: tuple[int, int] = ORBIT_FIXED_DELIVERY_ENTRY,
        rejoin_branch: tuple[int, int] = ORBIT_FIXED_REJOIN_BRANCH,
        dropoff_override: tuple[int, int] | None = None,
        return_buffer_cells: tuple[tuple[int, int], ...] = ORBIT_FIXED_RETURN_BUFFER,
        delivery_quota_min: int = 1,
        delivery_quota_max: int = 3,
        forecast_buffer_cap: int = 2,
        rejoin_slot_headroom: int = 2,
        order_forecast: dict[int, list[str]] | None = None,
    ):
        self.debug = bool(debug)
        self.reservation_horizon = max(1, int(reservation_horizon))
        self.shelf_ids = shelf_ids
        self._branch_exit = (int(branch_exit[0]), int(branch_exit[1]))
        self._branch_continue = (int(branch_continue[0]), int(branch_continue[1]))
        self._delivery_entry = (int(delivery_entry[0]), int(delivery_entry[1]))
        self._rejoin_branch = (int(rejoin_branch[0]), int(rejoin_branch[1]))
        self._dropoff_override = (
            (int(dropoff_override[0]), int(dropoff_override[1]))
            if dropoff_override is not None
            else None
        )
        self._return_buffer_cells: tuple[tuple[int, int], ...] = tuple(
            (int(cell[0]), int(cell[1])) for cell in return_buffer_cells
        )
        self._delivery_quota_min = max(0, int(delivery_quota_min))
        self._delivery_quota_max = max(self._delivery_quota_min, int(delivery_quota_max))
        self._forecast_buffer_cap = max(0, int(forecast_buffer_cap))
        self._rejoin_slot_headroom = max(0, int(rejoin_slot_headroom))
        self._order_forecast = dict(order_forecast or {})
        self._loop_points: list[tuple[int, int]] = []
        self._orbit_phase: int = 0
        self._loop_spacing: int = 1
        self._flow_mode: str = FLOW_BALANCED_HARVEST
        self._topology: TopologyGraph | None = None
        self._orbit_token_indices: list[int] = []
        self._slot_by_bot: dict[int, int] = {}
        self._delivery_mode: set[int] = set()
        self._deliver_route_by_bot: dict[int, list[tuple[int, int]]] = {}
        self._deliver_goal_by_bot: dict[int, tuple[int, int]] = {}
        self._pickup_target_item_by_bot: dict[int, str] = {}
        self._pickup_target_cell_by_bot: dict[int, tuple[int, int]] = {}
        self._pickup_source_by_bot: dict[int, str] = {}
        self._detour_rejoin_slot_by_bot: dict[int, int] = {}
        self._return_mode: set[int] = set()
        self._return_slot_by_bot: dict[int, int] = {}
        self._yield_hold_cell_by_bot: dict[int, tuple[int, int]] = {}
        self._yield_hold_until_round_by_bot: dict[int, int] = {}
        self._bot_mode_by_bot: dict[int, str] = {}
        self._phase_hold_ticks: int = 0
        self._last_active_order_index: int | None = None
        self._last_transition_round: int = -999999
        self._last_score_seen: int = 0
        self._active_stall_rounds: int = 0
        self._last_delivery_quota_reason_code: int = 0
        self._last_unassigned_due_to_orbit_floor: int = 0
        self._prev_mission_type_by_bot: dict[int, str] = {}
        self._migration_stage: int = max(0, int(migration_stage))
        self._enable_preview_stage5: bool = self._migration_stage >= 5

        self.last_decision_ms: float = 0.0
        self.last_collisions_avoided: int = 0
        self.last_round_telemetry: dict[str, float] = {}
        self.last_assignment_snapshot: dict[int, dict[str, object]] = {}
        self.last_pre_collision_actions: dict[int, dict[str, object]] = {}
        self.last_round_debug: dict[str, Any] = {}
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
        self._topology = None
        if not self._loop_points:
            self._orbit_token_indices = []
        else:
            self._orbit_token_indices = [idx % len(self._loop_points) for idx in self._orbit_token_indices]

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
        self._orbit_token_indices = [
            (int(token_idx) + delta) % loop_len
            for token_idx in self._orbit_token_indices
        ]

    def _reassign_slots(
        self,
        *,
        bots_orbit: list[Any],
        slot_indices: list[int],
        loop_index_by_cell: dict[tuple[int, int], int],
    ) -> None:
        active_bot_ids = {int(bot.id) for bot in bots_orbit}
        valid_slots = {int(slot_idx) for slot_idx in slot_indices}
        loop_len = len(self._loop_points)
        if not active_bot_ids or not valid_slots or loop_len <= 0:
            self._slot_by_bot = {}
            return

        # Keep persistent token ownership; only repair invalid/duplicate ownership.
        kept: dict[int, int] = {}
        used_slots: set[int] = set()
        for bid in sorted(active_bot_ids):
            slot_idx = self._slot_by_bot.get(bid)
            if slot_idx is None:
                continue
            sidx = int(slot_idx)
            if sidx not in valid_slots or sidx in used_slots:
                continue
            kept[int(bid)] = sidx
            used_slots.add(sidx)
        self._slot_by_bot = dict(kept)

        free_slots = [int(slot_idx) for slot_idx in slot_indices if int(slot_idx) not in used_slots]
        if not free_slots:
            return

        for bot in sorted(bots_orbit, key=lambda row: int(row.id)):
            bid = int(bot.id)
            if bid in self._slot_by_bot:
                continue
            if not free_slots:
                break
            bpos = (int(bot.position[0]), int(bot.position[1]))
            bidx = loop_index_by_cell.get(bpos)
            if bidx is not None:
                choice = min(free_slots, key=lambda s: ((int(s) - int(bidx)) % loop_len, int(s)))
            else:
                choice = min(
                    free_slots,
                    key=lambda s: (
                        abs(int(bpos[0]) - int(self._loop_points[int(s)][0]))
                        + abs(int(bpos[1]) - int(self._loop_points[int(s)][1])),
                        int(s),
                    ),
                )
            self._slot_by_bot[bid] = int(choice)
            free_slots.remove(int(choice))

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

    def _pickup_cells_for_item(
        self,
        *,
        item_pos: tuple[int, int],
        grid: Grid,
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

    def _best_pickup_cell_for_item(
        self,
        *,
        start: tuple[int, int],
        item_pos: tuple[int, int],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> tuple[tuple[int, int] | None, int]:
        best_cell: tuple[int, int] | None = None
        best_dist = 999999
        for cell in self._pickup_cells_for_item(item_pos=item_pos, grid=grid, blocked=blocked):
            path = bfs_shortest_path(grid, start, cell, blocked=blocked)
            if path is None:
                continue
            dist = max(0, len(path) - 1)
            if dist < best_dist:
                best_dist = dist
                best_cell = cell
        return best_cell, best_dist

    def _assign_pickup_detours(
        self,
        *,
        state: GameState,
        bots_orbit: list[Any],
        active_need: Counter[str],
        preview_need: Counter[str],
        preview_budget: int,
        active_stall_rounds: int,
        rounds_left: int,
        detour_limit: int,
        orbit_floor: int,
        preview_designated_bots: set[int],
        reserved_item_ids: set[str],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> dict[int, tuple[str, str, str, tuple[int, int], int]]:
        self._last_unassigned_due_to_orbit_floor = 0
        if not bots_orbit:
            self._pickup_target_item_by_bot = {}
            self._pickup_target_cell_by_bot = {}
            self._pickup_source_by_bot = {}
            self._detour_rejoin_slot_by_bot = {}
            return {}

        bot_ids = {int(bot.id) for bot in bots_orbit}
        self._pickup_target_item_by_bot = {
            bid: item_id
            for bid, item_id in self._pickup_target_item_by_bot.items()
            if bid in bot_ids
        }
        self._pickup_target_cell_by_bot = {
            bid: cell
            for bid, cell in self._pickup_target_cell_by_bot.items()
            if bid in bot_ids
        }
        self._pickup_source_by_bot = {
            bid: source
            for bid, source in self._pickup_source_by_bot.items()
            if bid in bot_ids
        }
        self._detour_rejoin_slot_by_bot = {
            bid: slot_idx
            for bid, slot_idx in self._detour_rejoin_slot_by_bot.items()
            if bid in bot_ids
        }

        items_by_id = {str(item.id): item for item in state.items}
        assigned: dict[int, tuple[str, str, str, tuple[int, int], int]] = {}
        used_items = set(reserved_item_ids)

        preview_budget = max(0, min(int(preview_budget), int(sum(preview_need.values()))))
        active_need_total_current = int(sum(active_need.values()))
        # Hard active-first gate for detours: preview detours only after active need is closed.
        if active_need_total_current > 0:
            preview_budget = 0
        endgame_active_chase = bool(active_need_total_current > 0 and int(rounds_left) <= 120)
        stall_deep_scout = bool((int(active_stall_rounds) >= 10 or endgame_active_chase) and active_need_total_current > 0)
        max_detours_by_floor = max(0, len(bots_orbit) - max(1, int(orbit_floor)))
        total_limit = min(int(detour_limit), len(bots_orbit), max_detours_by_floor)
        if total_limit <= 0 and int(sum(active_need.values())) > 0 and bots_orbit:
            # Keep at least one active pickup lane alive if there is unresolved active demand.
            total_limit = 1
        if stall_deep_scout and active_need_total_current <= 2:
            # During prolonged no-progress windows, allow one extra deep scout.
            total_limit = max(int(total_limit), 2)
        if endgame_active_chase:
            total_limit = max(int(total_limit), 1 if active_need_total_current > 2 else 2)
        total_limit = max(0, total_limit)
        if total_limit <= 0:
            self._pickup_target_item_by_bot = {}
            self._pickup_target_cell_by_bot = {}
            self._pickup_source_by_bot = {}
            self._detour_rejoin_slot_by_bot = {}
            return {}

        candidate_capacity = 0
        # Preserve sticky missions when still useful.
        for bot in sorted(bots_orbit, key=lambda row: int(row.id)):
            if len(assigned) >= total_limit:
                break
            bid = int(bot.id)
            item_id = self._pickup_target_item_by_bot.get(bid)
            if not item_id or item_id in used_items:
                continue
            item = items_by_id.get(item_id)
            if item is None:
                continue
            item_type = str(item.type)
            source = self._pickup_source_by_bot.get(bid, "active")
            if source == "active":
                if active_need.get(item_type, 0) <= 0:
                    continue
            else:
                if bid not in preview_designated_bots:
                    continue
                if preview_budget <= 0 or preview_need.get(item_type, 0) <= 0:
                    continue

            start = (int(bot.position[0]), int(bot.position[1]))
            target_cell = self._pickup_target_cell_by_bot.get(bid)
            if target_cell is None:
                target_cell, _dist = self._best_pickup_cell_for_item(
                    start=start,
                    item_pos=(int(item.position[0]), int(item.position[1])),
                    grid=grid,
                    blocked=blocked,
                )
            if target_cell is None:
                continue

            path = bfs_shortest_path(grid, start, target_cell, blocked=blocked)
            if path is None:
                continue
            detour_dist = max(0, len(path) - 1)
            max_detour = 8 if source == "active" else 4
            if source == "active" and active_need_total_current <= 1 and active_need.get(item_type, 0) > 0:
                max_detour = max(max_detour, 14)
            if source == "active" and stall_deep_scout and active_need_total_current <= 2 and active_need.get(item_type, 0) > 0:
                max_detour = 40
            if source == "active" and endgame_active_chase and active_need.get(item_type, 0) > 0:
                max_detour = max(max_detour, 28)
            if detour_dist > max_detour:
                continue
            reserved_slot = self._slot_by_bot.get(bid)
            if reserved_slot is None:
                continue
            candidate_capacity += 1

            assigned[bid] = (item_id, item_type, source, target_cell, int(reserved_slot))
            self._detour_rejoin_slot_by_bot[bid] = int(reserved_slot)
            used_items.add(item_id)
            self._pickup_target_cell_by_bot[bid] = target_cell
            if source == "active":
                active_need[item_type] -= 1
            else:
                preview_need[item_type] -= 1
                preview_budget -= 1

        # Global greedy assignment for remaining bots.
        candidate_rows: list[tuple[float, int, str, str, str, tuple[int, int], int]] = []
        for bot in bots_orbit:
            bid = int(bot.id)
            if bid in assigned:
                continue
            if len(bot.inventory) >= 3:
                continue
            reserved_slot = self._slot_by_bot.get(bid)
            if reserved_slot is None:
                continue
            start = (int(bot.position[0]), int(bot.position[1]))
            sticky_item = self._pickup_target_item_by_bot.get(bid)
            for item in state.items:
                item_id = str(item.id)
                if item_id in used_items:
                    continue
                item_type = str(item.type)
                source = ""
                if active_need.get(item_type, 0) > 0:
                    source = "active"
                elif (
                    bid in preview_designated_bots
                    and preview_budget > 0
                    and preview_need.get(item_type, 0) > 0
                ):
                    source = "preview"
                if not source:
                    continue

                pickup_cell, dist = self._best_pickup_cell_for_item(
                    start=start,
                    item_pos=(int(item.position[0]), int(item.position[1])),
                    grid=grid,
                    blocked=blocked,
                )
                if pickup_cell is None:
                    continue
                max_detour = 8 if source == "active" else 4
                if source == "active" and active_need_total_current <= 1 and active_need.get(item_type, 0) > 0:
                    max_detour = max(max_detour, 14)
                if source == "active" and stall_deep_scout and active_need_total_current <= 2 and active_need.get(item_type, 0) > 0:
                    max_detour = 40
                if source == "active" and endgame_active_chase and active_need.get(item_type, 0) > 0:
                    max_detour = max(max_detour, 28)
                if dist > max_detour:
                    continue
                candidate_capacity += 1

                demand_value = 100.0 if source == "active" else 28.0
                if source == "active" and active_need.get(item_type, 0) <= 1:
                    demand_value += 22.0
                eta_weight = 12.0
                if source == "active" and stall_deep_scout and active_need.get(item_type, 0) <= 1:
                    demand_value += 42.0
                    eta_weight = 5.0
                eta_penalty = eta_weight * float(dist)
                congestion_penalty = 3.0 * float(len(assigned))
                orbit_vacancy_cost = 6.0 if source == "active" else 10.0
                replan_cost = 0.0 if sticky_item == item_id else 5.0
                inventory_penalty = 2.0 * float(len(bot.inventory))
                score = demand_value - eta_penalty - congestion_penalty - orbit_vacancy_cost - replan_cost - inventory_penalty
                candidate_rows.append((score, bid, item_id, item_type, source, pickup_cell, int(reserved_slot)))

        for _score, bid, item_id, item_type, source, pickup_cell, reserved_slot in sorted(candidate_rows, reverse=True):
            if len(assigned) >= total_limit:
                break
            if bid in assigned:
                continue
            if item_id in used_items:
                continue
            if source == "active":
                if active_need.get(item_type, 0) <= 0:
                    continue
                active_need[item_type] -= 1
            else:
                if preview_budget <= 0 or preview_need.get(item_type, 0) <= 0:
                    continue
                preview_need[item_type] -= 1
                preview_budget -= 1
            assigned[bid] = (item_id, item_type, source, pickup_cell, int(reserved_slot))
            used_items.add(item_id)
            self._pickup_target_item_by_bot[bid] = item_id
            self._pickup_target_cell_by_bot[bid] = pickup_cell
            self._pickup_source_by_bot[bid] = source
            self._detour_rejoin_slot_by_bot[bid] = int(reserved_slot)

        assigned_ids = set(assigned)
        self._pickup_target_item_by_bot = {
            bid: item_id
            for bid, item_id in self._pickup_target_item_by_bot.items()
            if bid in assigned_ids
        }
        self._pickup_target_cell_by_bot = {
            bid: cell
            for bid, cell in self._pickup_target_cell_by_bot.items()
            if bid in assigned_ids
        }
        self._pickup_source_by_bot = {
            bid: source
            for bid, source in self._pickup_source_by_bot.items()
            if bid in assigned_ids
        }
        self._detour_rejoin_slot_by_bot = {
            bid: slot_idx
            for bid, slot_idx in self._detour_rejoin_slot_by_bot.items()
            if bid in assigned_ids
        }
        self._last_unassigned_due_to_orbit_floor = max(
            0,
            int(candidate_capacity) - int(total_limit),
        )
        return assigned

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

    def _delivery_quota(
        self,
        *,
        bot_count: int,
        rounds_left: int,
        active_remaining_total: int,
        active_carried_total: int,
    ) -> int:
        if active_carried_total <= 0:
            return 0
        quota = max(0, int(self._delivery_quota_min))
        if active_carried_total >= 3 and (active_remaining_total <= 3 or rounds_left <= 55):
            quota = max(quota, min(2, int(self._delivery_quota_max)))
        if rounds_left <= 15 and active_carried_total >= 6 and bot_count >= 10:
            quota = max(quota, min(3, int(self._delivery_quota_max)))
        quota_cap = max(0, min(int(self._delivery_quota_max), max(1, bot_count // 3)))
        return max(0, min(quota, quota_cap))

    def _delivery_value(
        self,
        *,
        bot_id: int,
        active_match_count: int,
        inv_size: int,
        dist_drop: int,
        active_remaining_total: int,
        rounds_left: int,
    ) -> float:
        value = active_match_count * 12.0 + inv_size * 2.0 - dist_drop
        if active_remaining_total == 0:
            value += 6.0
        if active_match_count > 0 and dist_drop <= 8:
            value += 3.0
        if rounds_left <= 35 and active_match_count > 0:
            value += 3.0
        return value

    def _drop_queue_cells(
        self,
        *,
        drop_off: tuple[int, int],
        grid: Grid,
        blocked: set[tuple[int, int]],
        limit: int = 4,
    ) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()

        def _append(cell: tuple[int, int]) -> None:
            if cell in seen:
                return
            if cell == drop_off:
                return
            if cell in blocked:
                return
            if not grid.is_walkable(cell[0], cell[1]):
                return
            seen.add(cell)
            out.append(cell)

        # Keep the immediate cell next to drop-off clear as a stop-line.
        for dx in range(2, limit + 5):
            _append((int(drop_off[0] + dx), int(drop_off[1])))
        for dy in range(1, 4):
            _append((int(drop_off[0] + 2), int(drop_off[1] - dy)))
            _append((int(drop_off[0] + 2), int(drop_off[1] + dy)))
            _append((int(drop_off[0] + 1), int(drop_off[1] - dy)))

        return out[: max(1, limit)]

    def _assign_return_slots(
        self,
        *,
        returning_bots: list[Any],
        slot_indices: list[int],
        occupied_ring_indices: set[int],
        loop_index_by_cell: dict[tuple[int, int], int],
        start_by_bot: dict[int, tuple[int, int]],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> dict[int, int]:
        if not returning_bots or not self._loop_points:
            self._return_slot_by_bot = {}
            return {}

        loop_len = len(self._loop_points)
        free_slots = [idx for idx in slot_indices if idx not in occupied_ring_indices]
        safe_slots = [
            idx
            for idx in free_slots
            if ((idx - 1) % loop_len) not in occupied_ring_indices
            and ((idx + 1) % loop_len) not in occupied_ring_indices
        ]
        candidates = safe_slots or free_slots
        if not candidates:
            all_ring_slots = [idx for idx in range(loop_len) if idx not in occupied_ring_indices]
            fallback_safe = [
                idx
                for idx in all_ring_slots
                if ((idx - 1) % loop_len) not in occupied_ring_indices
                and ((idx + 1) % loop_len) not in occupied_ring_indices
            ]
            candidates = fallback_safe or all_ring_slots

        assigned: dict[int, int] = {}
        used: set[int] = set()

        for bot in sorted(returning_bots, key=lambda row: int(row.id)):
            bid = int(bot.id)
            reserved = self._return_slot_by_bot.get(bid)
            if reserved in candidates and reserved not in used:
                assigned[bid] = int(reserved)
                used.add(int(reserved))

        remaining_slots = [idx for idx in candidates if idx not in used]
        for bot in sorted(returning_bots, key=lambda row: int(row.id)):
            bid = int(bot.id)
            if bid in assigned:
                continue
            if not remaining_slots:
                break
            start = start_by_bot[bid]
            start_idx = loop_index_by_cell.get(start)

            def _slot_cost(slot_idx: int) -> tuple[int, int]:
                if start_idx is not None:
                    return (((slot_idx - start_idx) % loop_len), slot_idx)
                goal = self._loop_points[slot_idx]
                path = bfs_shortest_path(grid, start, goal, blocked=blocked)
                dist = 999999 if path is None else max(0, len(path) - 1)
                return (dist, slot_idx)

            chosen = min(remaining_slots, key=_slot_cost)
            assigned[bid] = int(chosen)
            used.add(int(chosen))
            remaining_slots.remove(chosen)

        self._return_slot_by_bot = dict(assigned)
        return assigned

    def _compile_topology(
        self,
        *,
        state: GameState,
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> TopologyGraph:
        ring_cells = list(self._loop_points)
        ring_index = {cell: idx for idx, cell in enumerate(ring_cells)}
        drop_off = (
            tuple(self._dropoff_override)
            if self._dropoff_override is not None
            else (int(state.drop_off[0]), int(state.drop_off[1]))
        )

        def _valid(cell: tuple[int, int]) -> bool:
            x, y = int(cell[0]), int(cell[1])
            if x < 0 or y < 0 or x >= int(state.grid.width) or y >= int(state.grid.height):
                return False
            if (x, y) in blocked:
                return False
            return bool(grid.is_walkable(x, y))

        if ring_cells:
            ys = [int(pt[1]) for pt in ring_cells]
            top_y = min(ys)
            bottom_y = max(ys)
            top_candidates = [pt for pt in ring_cells if int(pt[1]) == top_y]
            bottom_candidates = [pt for pt in ring_cells if int(pt[1]) == bottom_y]
            gate_top = min(
                top_candidates,
                key=lambda pt: (abs(int(pt[0]) - int(drop_off[0])), int(pt[0]), int(pt[1])),
            ) if top_candidates else ring_cells[0]
            gate_bottom = min(
                bottom_candidates,
                key=lambda pt: (abs(int(pt[0]) - int(drop_off[0])), int(pt[0]), int(pt[1])),
            ) if bottom_candidates else ring_cells[0]
        else:
            gate_top = drop_off
            gate_bottom = drop_off
            top_y = int(drop_off[1])
            bottom_y = int(drop_off[1])

        pickup_adj_cells: set[tuple[int, int]] = set()
        for item in state.items:
            ipos = (int(item.position[0]), int(item.position[1]))
            for cell in ring_cells:
                if abs(cell[0] - ipos[0]) + abs(cell[1] - ipos[1]) == 1:
                    pickup_adj_cells.add(cell)

        queue_cells = self._drop_queue_cells(drop_off=drop_off, grid=grid, blocked=blocked, limit=6)
        stop_line = (int(drop_off[0] + 1), int(drop_off[1] - 1))
        if stop_line in blocked or not grid.is_walkable(stop_line[0], stop_line[1]):
            stop_line = (int(drop_off[0] + 1), int(drop_off[1]))
        if stop_line in blocked or not grid.is_walkable(stop_line[0], stop_line[1]):
            stop_line = queue_cells[0] if queue_cells else drop_off

        return_buffer_cells: list[tuple[int, int]] = []
        for cell in self._return_buffer_cells:
            cell_tuple = (int(cell[0]), int(cell[1]))
            if cell_tuple == drop_off or cell_tuple == stop_line:
                continue
            if not _valid(cell_tuple):
                continue
            return_buffer_cells.append(cell_tuple)
        if not return_buffer_cells:
            for cell in reversed(queue_cells):
                if cell == drop_off or cell == stop_line:
                    continue
                return_buffer_cells.append(cell)
                if len(return_buffer_cells) >= 2:
                    break
        if gate_bottom not in return_buffer_cells and _valid(gate_bottom):
            return_buffer_cells.append(gate_bottom)

        def _corridor(y_lane: int, start_x: int, end_x: int) -> list[tuple[int, int]]:
            x0 = min(int(start_x), int(end_x))
            x1 = max(int(start_x), int(end_x))
            out: list[tuple[int, int]] = []
            for x in range(x0, x1 + 1):
                cell = (int(x), int(y_lane))
                if cell in blocked:
                    continue
                if not grid.is_walkable(cell[0], cell[1]):
                    continue
                out.append(cell)
            return out

        bottom_lane_y = int(bottom_y + 1)
        if not grid.is_walkable(int(gate_bottom[0]), bottom_lane_y):
            bottom_lane_y = int(bottom_y)
        if _valid(self._delivery_entry):
            bottom_lane_y = int(self._delivery_entry[1])

        delivery_corridors = {
            "top": _corridor(top_y, int(gate_top[0]), int(drop_off[0])),
            "bottom": _corridor(bottom_lane_y, int(gate_bottom[0]), int(drop_off[0])),
        }

        branch_exit = self._branch_exit if _valid(self._branch_exit) else gate_bottom
        branch_continue = self._branch_continue
        if not _valid(branch_continue):
            if branch_exit in ring_index:
                branch_continue = ring_cells[(int(ring_index[branch_exit]) + 1) % max(1, len(ring_cells))]
            else:
                branch_continue = gate_bottom
        delivery_entry = self._delivery_entry
        if not _valid(delivery_entry):
            fallback_entry = (int(branch_exit[0]), int(branch_exit[1] + 1))
            delivery_entry = fallback_entry if _valid(fallback_entry) else drop_off
        rejoin_branch = self._rejoin_branch
        if not _valid(rejoin_branch):
            fallback_rejoin = (int(drop_off[0] + 2), int(drop_off[1] - 1))
            rejoin_branch = fallback_rejoin if _valid(fallback_rejoin) else gate_bottom

        return TopologyGraph(
            ring_cells=ring_cells,
            ring_index=ring_index,
            entry_gates=[branch_exit, gate_bottom],
            exit_gates=[branch_exit, gate_bottom],
            pickup_adj_cells=pickup_adj_cells,
            delivery_corridors=delivery_corridors,
            drop_queue_cells=queue_cells,
            return_buffer_cells=return_buffer_cells,
            stop_line=stop_line,
            drop_off=drop_off,
            branch_exit=branch_exit,
            branch_continue=branch_continue,
            delivery_entry=delivery_entry,
            rejoin_branch=rejoin_branch,
        )

    def _ensure_topology(
        self,
        *,
        state: GameState,
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> TopologyGraph:
        if self._topology is None:
            self._topology = self._compile_topology(state=state, grid=grid, blocked=blocked)
            return self._topology
        if self._topology.ring_cells != self._loop_points:
            self._topology = self._compile_topology(state=state, grid=grid, blocked=blocked)
            return self._topology
        current_drop = (
            tuple(self._dropoff_override)
            if self._dropoff_override is not None
            else (int(state.drop_off[0]), int(state.drop_off[1]))
        )
        if self._topology.drop_off != current_drop:
            self._topology = self._compile_topology(state=state, grid=grid, blocked=blocked)
            return self._topology
        return self._topology

    def _ensure_orbit_tokens(self, *, target_count: int, loop_len: int) -> list[int]:
        if loop_len <= 0:
            self._orbit_token_indices = []
            return []
        target_count = max(1, min(int(target_count), int(loop_len)))
        if len(self._orbit_token_indices) != target_count:
            self._orbit_token_indices = [
                (self._orbit_phase + (i * loop_len) // target_count) % loop_len
                for i in range(target_count)
            ]
        else:
            self._orbit_token_indices = [int(idx) % loop_len for idx in self._orbit_token_indices]

        normalized: list[int] = []
        seen: set[int] = set()
        for idx in self._orbit_token_indices:
            probe = int(idx)
            while probe in seen:
                probe = (probe + 1) % loop_len
            normalized.append(probe)
            seen.add(probe)
        # Preserve cyclic token order to keep phase-driven orbit rotation.
        self._orbit_token_indices = list(normalized)
        return list(self._orbit_token_indices)

    def _build_demand_ledger(
        self,
        *,
        state: GameState,
        bots_sorted: list[Any],
    ) -> DemandLedger:
        active_order = get_active_order(state)
        preview_order = get_preview_order(state)

        active_need: Counter[str] = Counter()
        if active_order is not None:
            active_need = Counter(str(item_type) for item_type in active_order.items_required)
            for item_type in active_order.items_delivered:
                key = str(item_type)
                if active_need.get(key, 0) > 0:
                    active_need[key] -= 1
                    if active_need[key] <= 0:
                        del active_need[key]

        preview_need_raw: Counter[str] = Counter()
        if preview_order is not None:
            preview_need_raw = Counter(str(item_type) for item_type in preview_order.items_required)
            for item_type in preview_order.items_delivered:
                key = str(item_type)
                if preview_need_raw.get(key, 0) > 0:
                    preview_need_raw[key] -= 1
                    if preview_need_raw[key] <= 0:
                        del preview_need_raw[key]
        prefetch_allowed = bool(should_prefetch_preview(state))
        if not prefetch_allowed:
            preview_need_raw = Counter()

        active_carried: Counter[str] = Counter()
        preview_carried: Counter[str] = Counter()
        active_committed: Counter[str] = Counter()
        preview_contingent: Counter[str] = Counter()
        active_reserved: Counter[str] = Counter()
        preview_reserved: Counter[str] = Counter()
        drop_off = (
            tuple(self._dropoff_override)
            if self._dropoff_override is not None
            else (int(state.drop_off[0]), int(state.drop_off[1]))
        )
        free_slots_total = sum(max(0, 3 - len(bot.inventory)) for bot in bots_sorted)
        items_by_id = {str(item.id): item for item in state.items}
        active_bot_ids = {int(bot.id) for bot in bots_sorted}

        for bid, item_id in self._pickup_target_item_by_bot.items():
            if int(bid) not in active_bot_ids:
                continue
            if int(bid) in self._delivery_mode or int(bid) in self._return_mode:
                continue
            item = items_by_id.get(str(item_id))
            if item is None:
                continue
            item_type = str(item.type)
            source = self._pickup_source_by_bot.get(int(bid), "")
            if source == "active" and active_need.get(item_type, 0) > 0:
                active_reserved[item_type] += 1
            elif source == "preview" and preview_need_raw.get(item_type, 0) > 0:
                preview_reserved[item_type] += 1

        # Forecast-buffer prefetch: keep a small rolling buffer for upcoming orders
        # when active demand is not currently service-critical.
        if prefetch_allowed and self._forecast_buffer_cap > 0:
            active_idx = int(getattr(state, "active_order_index", 0))
            forecast_queue: list[str] = []
            for depth in range(1, 4):
                forecast_items = self._order_forecast.get(active_idx + depth)
                if not forecast_items:
                    continue
                for item_type in forecast_items:
                    forecast_queue.append(str(item_type))
            if forecast_queue:
                forecast_counter: Counter[str] = Counter()
                for item_type in forecast_queue:
                    if int(sum(forecast_counter.values())) >= int(self._forecast_buffer_cap):
                        break
                    forecast_counter[item_type] += 1
                preview_need_raw.update(forecast_counter)

        for bot in bots_sorted:
            bid = int(bot.id)
            active_matches = items_matching_active(bot, state)
            active_match_counter = Counter(str(item_type) for item_type in active_matches)
            active_carried.update(active_match_counter)

            inv_counter = Counter(str(item_type) for item_type in bot.inventory)
            inv_counter.subtract(active_match_counter)
            for item_type, count in inv_counter.items():
                if count <= 0:
                    continue
                if preview_need_raw.get(item_type, 0) > 0:
                    preview_carried[item_type] += int(count)

            start = (int(bot.position[0]), int(bot.position[1]))
            dist_drop = abs(start[0] - int(drop_off[0])) + abs(start[1] - int(drop_off[1]))
            committed_now = (
                bid in self._delivery_mode
                or len(bot.inventory) >= 3
                or dist_drop <= 8
            )
            if committed_now and active_match_counter:
                active_committed.update(active_match_counter)
                for item_type, count in inv_counter.items():
                    if count <= 0:
                        continue
                    if preview_need_raw.get(item_type, 0) > 0:
                        preview_contingent[item_type] += int(count)

        active_deficit = Counter(active_need)
        active_deficit.subtract(active_reserved)
        active_deficit.subtract(active_committed)
        active_deficit = Counter({key: int(val) for key, val in active_deficit.items() if int(val) > 0})

        active_serviceable_deficit = Counter(active_need)
        active_serviceable_deficit.subtract(active_reserved)
        active_serviceable_deficit.subtract(active_committed)
        active_serviceable_deficit = Counter({
            key: int(val) for key, val in active_serviceable_deficit.items() if int(val) > 0
        })

        preview_need = Counter(preview_need_raw)
        preview_need.subtract(active_need)
        preview_need.subtract(preview_contingent)
        preview_need.subtract(preview_reserved)
        preview_need = Counter({key: int(val) for key, val in preview_need.items() if int(val) > 0})

        active_serviceable_total = int(sum(active_serviceable_deficit.values()))
        slot_safety_stock = 0
        if int(sum(active_need.values())) > 0:
            slot_safety_stock = max(1, active_serviceable_total)
            slot_safety_stock = min(int(free_slots_total), int(slot_safety_stock))

        return DemandLedger(
            active_need=active_need,
            active_reserved=active_reserved,
            active_deficit=active_deficit,
            active_serviceable_deficit=active_serviceable_deficit,
            preview_need=preview_need,
            active_carried=active_carried,
            preview_carried=preview_carried,
            active_committed=active_committed,
            preview_contingent=preview_contingent,
            preview_reserved=preview_reserved,
            slot_safety_stock=int(slot_safety_stock),
        )

    def _cargo_class_for_bot(
        self,
        *,
        bot: Any,
        state: GameState,
        ledger: DemandLedger,
    ) -> str:
        inv = [str(item_type) for item_type in getattr(bot, "inventory", []) or []]
        if not inv:
            return CARGO_DEADWEIGHT

        active_matches = Counter(str(item_type) for item_type in items_matching_active(bot, state))
        active_count = int(sum(active_matches.values()))
        if active_count >= len(inv):
            return CARGO_ACTIVE_ONLY
        if active_count > 0:
            return CARGO_MIXED_ACTIVE_PREVIEW

        preview_hits = sum(1 for item_type in inv if ledger.preview_need.get(item_type, 0) > 0)
        if preview_hits > 0:
            return CARGO_PREVIEW_HELD
        return CARGO_DEADWEIGHT

    def _flow_controller(
        self,
        *,
        state: GameState,
        ledger: DemandLedger,
        bot_count: int,
        free_slots: int,
    ) -> tuple[str, int, int, int, int]:
        rounds_left = max(0, int(state.max_rounds) - int(state.round))
        active_need_total = int(sum(ledger.active_need.values()))
        active_reserved_total = int(sum(ledger.active_reserved.values()))
        active_deficit_total = int(sum(ledger.active_deficit.values()))
        active_serviceable_total = int(sum(ledger.active_serviceable_deficit.values()))
        preview_need_total = int(sum(ledger.preview_need.values()))
        active_carried_total = int(sum(ledger.active_carried.values()))
        active_committed_total = int(sum(ledger.active_committed.values()))
        preview_contingent_total = int(sum(ledger.preview_contingent.values()))
        slot_safety_stock = int(max(0, ledger.slot_safety_stock))
        active_open_total = int(active_serviceable_total)
        preview_open_total = int(preview_need_total)
        transition_recent = (int(state.round) - int(self._last_transition_round)) <= 8
        transition_likely_soon = active_open_total <= 1 and active_committed_total > 0

        flow_mode = FLOW_BALANCED_HARVEST
        if int(state.round) < 18:
            flow_mode = FLOW_BOOT_RELEASE
        elif rounds_left <= 45 or (active_open_total <= 2 and active_carried_total > 0):
            flow_mode = FLOW_FINISH_WAVE

        delivery_cap = self._delivery_quota(
            bot_count=bot_count,
            rounds_left=rounds_left,
            active_remaining_total=active_open_total,
            active_carried_total=active_carried_total,
        )
        quota_reason_code = 0
        min_delivery = 0
        if active_carried_total > 0 and int(state.round) >= 8:
            min_delivery = 1
            quota_reason_code = 2
        if rounds_left <= 35 and active_carried_total >= 2 and bot_count >= 8:
            min_delivery = max(min_delivery, 2)
            quota_reason_code = 4
        if flow_mode == FLOW_FINISH_WAVE and active_carried_total >= 3:
            delivery_cap = min(max(0, bot_count - 1), delivery_cap + 1)
            quota_reason_code = 5
        min_delivery = min(min_delivery, delivery_cap)
        if active_carried_total <= 0:
            quota_reason_code = 1
        if flow_mode == FLOW_BOOT_RELEASE and int(state.round) < 8 and active_carried_total > 0:
            quota_reason_code = 3

        if flow_mode == FLOW_BOOT_RELEASE:
            orbit_candidates = [max(5, bot_count - 4), max(6, bot_count - 3), max(6, bot_count - 2)]
            deliver_candidates = [0, 1]
            preview_candidates = [0]
        elif flow_mode == FLOW_FINISH_WAVE:
            orbit_candidates = [max(5, bot_count - 3), max(5, bot_count - 2), max(5, bot_count - 1)]
            deliver_candidates = [1, 2, 3]
            preview_candidates = [0]
        else:
            orbit_candidates = [max(6, bot_count - 2), max(6, bot_count - 1), max(5, bot_count - 3)]
            deliver_candidates = [1, 2]
            preview_candidates = [0, 1]

        preview_cap = max(0, min(2, int(free_slots) - int(slot_safety_stock)))
        if active_open_total > 0:
            preview_cap = 0

        best_tuple: tuple[float, int, int, int, int] | None = None
        for orbit_target_raw in orbit_candidates:
            orbit_target_base = max(4, min(int(orbit_target_raw), bot_count))
            for deliver_target_raw in deliver_candidates:
                deliver_target = max(0, min(int(deliver_target_raw), delivery_cap, max(0, bot_count - 1)))
                if deliver_target < min_delivery:
                    continue
                harvest_bots = max(0, bot_count - deliver_target)
                orbit_target = min(orbit_target_base, harvest_bots)
                orbit_target = max(4, min(orbit_target, bot_count))
                off_ring_budget = max(0, harvest_bots - orbit_target)
                detour_limit = max(1, min(bot_count, off_ring_budget + 1))

                for preview_target_raw in preview_candidates:
                    preview_budget = max(0, min(int(preview_target_raw), preview_cap, preview_open_total))
                    # Active-first hard rule: preview consumes only true slack.
                    if preview_budget > 0 and active_open_total > 0:
                        continue
                    if preview_budget > 0 and active_carried_total > 0 and deliver_target <= 0:
                        continue

                    delivery_gain = min(deliver_target, active_carried_total)
                    pickup_gain = min(active_open_total, active_reserved_total + off_ring_budget + max(1, orbit_target // 4))
                    preview_gain = min(preview_budget, preview_open_total)
                    coverage = orbit_target
                    if preview_gain > 0 and (transition_recent or transition_likely_soon):
                        preview_gain += 0.5

                    under_delivery_penalty = 7.0 * max(0, active_open_total - (delivery_gain + pickup_gain))
                    congestion_penalty = 5.0 * max(0, deliver_target - 1) + 3.0 * max(0, off_ring_budget - detour_limit)
                    vacancy_penalty = 4.0 * max(0, 6 - coverage)
                    active_first_penalty = 18.0 * preview_budget if active_open_total > 0 and preview_budget > 0 else 0.0

                    utility = (
                        40.0 * delivery_gain
                        + 18.0 * pickup_gain
                        + 6.0 * preview_gain
                        + 2.0 * coverage
                        - under_delivery_penalty
                        - congestion_penalty
                        - vacancy_penalty
                        - active_first_penalty
                    )

                    candidate = (
                        utility,
                        int(orbit_target),
                        int(deliver_target),
                        int(preview_budget),
                        int(detour_limit),
                    )
                    if best_tuple is None or candidate > best_tuple:
                        best_tuple = candidate

        if best_tuple is None:
            orbit_target = max(6, bot_count - 2)
            delivery_tokens = min(delivery_cap, max(min_delivery, 1 if active_carried_total > 0 else 0))
            preview_budget = 0
            detour_limit = max(1, bot_count // 2)
        else:
            _utility, orbit_target, delivery_tokens, preview_budget, detour_limit = best_tuple

        delivery_tokens = max(delivery_tokens, min_delivery)
        delivery_tokens = max(0, min(delivery_tokens, max(0, bot_count - 1)))
        if active_carried_total <= 0:
            delivery_tokens = 0
        if active_open_total > 0 or (active_need_total > 0 and delivery_tokens <= 0 and active_carried_total > 0):
            preview_budget = 0
        self._last_delivery_quota_reason_code = int(quota_reason_code)
        return flow_mode, orbit_target, delivery_tokens, preview_budget, detour_limit

    def _mobility_state_for_bot(self, *, bot_id: int, round_index: int) -> str:
        if bot_id in self._return_mode:
            if bot_id in self._return_slot_by_bot:
                return REJOIN_MODE
            return RETURN_BUFFER_MODE
        if bot_id in self._delivery_mode:
            mode = self._bot_mode_by_bot.get(bot_id, DELIVER_MODE)
            if mode == QUEUE_MODE:
                return DROP_QUEUE_MODE
            return DELIVER_OUTBOUND_MODE
        mode = self._bot_mode_by_bot.get(bot_id, "")
        if mode == "pickup_detour":
            return PICK_DETOUR_MODE
        if mode == ORBIT_MODE:
            return MOBILITY_ORBIT
        if round_index < 8:
            return BOOT_RELEASE_MODE
        return MOBILITY_ORBIT

    def _select_preview_designated_bots(
        self,
        *,
        bots_orbit: list[Any],
        state: GameState,
        preview_budget: int,
    ) -> set[int]:
        if preview_budget <= 0 or not bots_orbit:
            return set()
        drop_off = (int(state.drop_off[0]), int(state.drop_off[1]))
        limit = max(1, min(len(bots_orbit), int(preview_budget) + 1))
        rows: list[tuple[int, int, int]] = []
        for bot in bots_orbit:
            bid = int(bot.id)
            active_matches = len(items_matching_active(bot, state))
            if active_matches > 0:
                continue
            inv_size = len(bot.inventory)
            if inv_size >= 3:
                continue
            pos = (int(bot.position[0]), int(bot.position[1]))
            dist_drop = abs(pos[0] - int(drop_off[0])) + abs(pos[1] - int(drop_off[1]))
            # Prefer bots farther from drop-off and with lower active-delivery utility.
            priority = dist_drop * 10 - inv_size * 3
            rows.append((priority, -dist_drop, bid))
        rows.sort(reverse=True)
        return {int(bid) for _prio, _dist, bid in rows[:limit]}

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()
        self.last_assignment_snapshot = {}
        self.last_pre_collision_actions = {}
        self.last_round_debug = {}
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
                "flow_orbit_target": 0.0,
                "flow_deliver_target": 0.0,
                "flow_preview_budget": 0.0,
                "migration_stage": float(self._migration_stage),
                "preview_stage5_enabled": 1.0 if self._enable_preview_stage5 else 0.0,
                "orbit_target": 0.0,
                "deliver_target": 0.0,
                "preview_budget": 0.0,
                "active_stall_rounds": 0.0,
                "active_safety_stock": 0.0,
                "delivery_quota_reason_code": 0.0,
                "delivery_quota_reason": 0.0,
                "occupied_tokens": 0.0,
                "vacant_tokens": 0.0,
                "reserved_rejoin_tokens": 0.0,
                "headway_gap_1": 0.0,
                "headway_gap_2": 0.0,
                "headway_gap_3p": 0.0,
                "gate_admission_denied": 0.0,
                "rejoin_queue_len": 0.0,
                "orbit_floor_violations": 0.0,
                "mission_orbit_count": 0.0,
                "mission_pickup_detour_count": 0.0,
                "mission_pick_item_count": 0.0,
                "mission_deliver_count": 0.0,
                "mission_queue_count": 0.0,
                "mission_rejoin_count": 0.0,
                "mission_value_avg": 0.0,
                "assignment_churn_by_bot": 0.0,
                "unassigned_reachable_active_items": 0.0,
                "unassigned_due_to_orbit_floor": 0.0,
                "unassigned_due_to_reservation_conflict": 0.0,
                "ready_active_cargo_units": 0.0,
                "committed_active_cargo_units": 0.0,
                "drop_queue_len": 0.0,
                "avg_drop_wait": 0.0,
                "delivery_token_utilization": 0.0,
                "return_to_orbit_eta": 0.0,
                "transition_event": 0.0,
                "auto_delivered_on_transition": 0.0,
                "A_need": 0.0,
                "A_reserved": 0.0,
                "A_committed": 0.0,
                "P_need": 0.0,
                "P_contingent": 0.0,
                "ring_direction_violation": 0.0,
                "illegal_reentry_attempt": 0.0,
                "queue_semantics_violation": 0.0,
                "preview_stole_active_capacity": 0.0,
                "reservation_conflict_count": 0.0,
                "orbit_pick_active": 0.0,
                "orbit_pick_preview": 0.0,
                "orbit_min_gap": 0.0,
                "branch_exit_visits": 0.0,
                "branch_to_delivery": 0.0,
                "branch_continue_moves": 0.0,
                "branch_waits": 0.0,
                "rejoin_branch_visits": 0.0,
                "rejoin_admissions": 0.0,
                "rejoin_denials": 0.0,
                "wait_due_to_no_assignment": float(len(actions)),
                "wait_due_to_collision_block": 0.0,
                "wait_due_to_spacing_guard": 0.0,
            }
            self.last_round_debug = {
                "mission_type_by_bot": {},
                "mission_value_by_bot": {},
                "headway_histogram": {"gap1": 0, "gap2": 0, "gap3p": 0},
                "A_need_by_type": {},
                "A_reserved_by_type": {},
                "A_committed_by_type": {},
                "P_need_by_type": {},
                "P_contingent_by_type": {},
            }
            return RoundActions(actions=actions)

        bots_sorted = sorted(state.bots, key=lambda row: int(row.id))
        bot_by_id: dict[int, Any] = {int(bot.id): bot for bot in bots_sorted}
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
        topology = self._ensure_topology(state=state, grid=grid, blocked=set(item_blocked))

        start_by_bot: dict[int, tuple[int, int]] = {
            int(bot.id): (int(bot.position[0]), int(bot.position[1]))
            for bot in bots_sorted
        }
        active_bot_ids_now = set(start_by_bot.keys())
        self._yield_hold_cell_by_bot = {
            int(bid): cell
            for bid, cell in self._yield_hold_cell_by_bot.items()
            if int(bid) in active_bot_ids_now
        }
        self._yield_hold_until_round_by_bot = {
            int(bid): int(until_round)
            for bid, until_round in self._yield_hold_until_round_by_bot.items()
            if int(bid) in active_bot_ids_now and int(until_round) > int(state.round)
        }
        current_active_idx = int(getattr(state, "active_order_index", 0))
        active_index_changed = False
        if self._last_active_order_index is None:
            self._last_active_order_index = current_active_idx
        elif current_active_idx != int(self._last_active_order_index):
            active_index_changed = True
            if current_active_idx > int(self._last_active_order_index):
                self._last_transition_round = int(state.round)
            self._last_active_order_index = current_active_idx

        ledger = self._build_demand_ledger(state=state, bots_sorted=bots_sorted)
        active_need_working = Counter(ledger.active_need)
        preview_need = Counter(ledger.preview_need)
        reserved_item_ids: set[str] = set()
        active_remaining_total = int(sum(active_need_working.values()))
        active_need_total = int(sum(ledger.active_need.values()))
        active_reserved_total = int(sum(ledger.active_reserved.values()))
        active_committed_total = int(sum(ledger.active_committed.values()))
        preview_need_total = int(sum(ledger.preview_need.values()))
        preview_contingent_total = int(sum(ledger.preview_contingent.values()))
        preview_reserved_total = int(sum(ledger.preview_reserved.values()))
        current_score = int(getattr(state, "score", 0))
        if active_index_changed:
            self._active_stall_rounds = 0
        elif active_remaining_total <= 0:
            self._active_stall_rounds = 0
        elif current_score > int(self._last_score_seen):
            self._active_stall_rounds = 0
        else:
            self._active_stall_rounds = int(self._active_stall_rounds) + 1
        self._last_score_seen = int(current_score)
        transition_recent = 1 if (int(state.round) - int(self._last_transition_round)) <= 8 else 0
        drop_off = (int(topology.drop_off[0]), int(topology.drop_off[1]))
        branch_exit = (int(topology.branch_exit[0]), int(topology.branch_exit[1]))
        branch_continue = (int(topology.branch_continue[0]), int(topology.branch_continue[1]))
        delivery_entry = (int(topology.delivery_entry[0]), int(topology.delivery_entry[1]))
        rejoin_branch = (int(topology.rejoin_branch[0]), int(topology.rejoin_branch[1]))
        rounds_left = max(0, int(state.max_rounds) - int(state.round))
        active_carried_total = int(sum(ledger.active_carried.values()))
        free_slots = sum(max(0, 3 - len(bot.inventory)) for bot in bots_sorted)
        flow_mode, orbit_target, flow_delivery_cap, preview_budget, detour_limit = self._flow_controller(
            state=state,
            ledger=ledger,
            bot_count=bot_count,
            free_slots=free_slots,
        )
        self._flow_mode = flow_mode
        cargo_class_by_bot: dict[int, str] = {
            int(bot.id): self._cargo_class_for_bot(bot=bot, state=state, ledger=ledger)
            for bot in bots_sorted
        }
        action_by_bot: dict[int, BotActionCommand] = {}
        movement_target_by_bot: dict[int, tuple[int, int] | None] = {}
        target_type_by_bot: dict[int, str] = {}
        slot_idx_by_bot: dict[int, int] = {}
        mission_value_by_bot: dict[int, float] = {}
        assignment_reservation_conflicts = 0
        gate_admission_denied = 0
        illegal_reentry_attempts = 0

        pick_active_count = 0
        pick_preview_count = 0
        detour_active_assigned = 0
        detour_preview_assigned = 0
        branch_exit_visits = 0
        branch_to_delivery = 0
        branch_continue_moves = 0
        branch_waits = 0
        rejoin_branch_visits = 0
        rejoin_admissions = 0
        rejoin_denials = 0
        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        occupied_now: set[tuple[int, int]] = set()
        orbit_bots: list[Any] = []
        claimed_targets: set[tuple[int, int]] = set()

        # Delivery mode management.
        active_bot_ids = {int(bot.id) for bot in bots_sorted}
        previous_delivery = set(self._delivery_mode)
        self._delivery_mode = {bid for bid in self._delivery_mode if bid in active_bot_ids}
        self._deliver_route_by_bot = {bid: route for bid, route in self._deliver_route_by_bot.items() if bid in active_bot_ids}
        self._deliver_goal_by_bot = {bid: goal for bid, goal in self._deliver_goal_by_bot.items() if bid in active_bot_ids}
        self._pickup_target_item_by_bot = {
            bid: item_id
            for bid, item_id in self._pickup_target_item_by_bot.items()
            if bid in active_bot_ids
        }
        self._pickup_target_cell_by_bot = {
            bid: cell
            for bid, cell in self._pickup_target_cell_by_bot.items()
            if bid in active_bot_ids
        }
        self._pickup_source_by_bot = {
            bid: source
            for bid, source in self._pickup_source_by_bot.items()
            if bid in active_bot_ids
        }
        self._detour_rejoin_slot_by_bot = {
            bid: slot_idx
            for bid, slot_idx in self._detour_rejoin_slot_by_bot.items()
            if bid in active_bot_ids
        }
        self._return_mode = {bid for bid in self._return_mode if bid in active_bot_ids}
        self._return_slot_by_bot = {
            bid: slot_idx
            for bid, slot_idx in self._return_slot_by_bot.items()
            if bid in self._return_mode
        }
        self._bot_mode_by_bot = {
            bid: mode
            for bid, mode in self._bot_mode_by_bot.items()
            if bid in active_bot_ids
        }

        delivery_cap = self._delivery_quota(
            bot_count=bot_count,
            rounds_left=rounds_left,
            active_remaining_total=active_remaining_total,
            active_carried_total=active_carried_total,
        )
        delivery_cap = min(int(delivery_cap), int(flow_delivery_cap))
        delivery_scores_by_bot: dict[int, float] = {}
        delivery_candidates: list[tuple[float, int, int, int, int]] = []
        for bot in bots_sorted:
            bid = int(bot.id)
            if bid in self._return_mode:
                continue
            active_matches = items_matching_active(bot, state)
            active_match_count = len(active_matches)
            if active_match_count <= 0:
                continue
            inv_size = len(bot.inventory)
            start = start_by_bot[bid]
            dist_drop = abs(int(start[0]) - int(drop_off[0])) + abs(int(start[1]) - int(drop_off[1]))
            delivery_value = self._delivery_value(
                bot_id=bid,
                active_match_count=active_match_count,
                inv_size=inv_size,
                dist_drop=dist_drop,
                active_remaining_total=active_remaining_total,
                rounds_left=rounds_left,
            )
            urgent = (
                inv_size >= 3
                or inv_size >= 2
                or dist_drop <= 8
                or active_match_count >= 2
                or active_remaining_total == 0
                or rounds_left <= 25
            )
            min_delivery_value = 3.0 if urgent else 7.0
            if flow_mode == FLOW_BOOT_RELEASE:
                min_delivery_value += 1.0
            elif flow_mode == FLOW_FINISH_WAVE:
                min_delivery_value -= 1.0
            if delivery_value < min_delivery_value:
                continue
            delivery_scores_by_bot[bid] = delivery_value
            # Higher delivery value first, then shorter path and richer payload.
            delivery_candidates.append(
                (-delivery_value, dist_drop, -active_match_count, -inv_size, bid)
            )

        selected_delivery = {bid for *_rest, bid in sorted(delivery_candidates)[:delivery_cap]}
        released_delivery = (previous_delivery & active_bot_ids) - selected_delivery
        self._return_mode |= released_delivery
        self._return_mode -= selected_delivery
        for bid in selected_delivery:
            self._return_slot_by_bot.pop(bid, None)
        self._delivery_mode = set(selected_delivery)
        self._deliver_route_by_bot = {
            bid: route
            for bid, route in self._deliver_route_by_bot.items()
            if bid in self._delivery_mode
        }
        self._deliver_goal_by_bot = {
            bid: goal
            for bid, goal in self._deliver_goal_by_bot.items()
            if bid in self._delivery_mode
        }

        for bot in bots_sorted:
            bid = int(bot.id)
            # Prioritize active order delivery; preview-only inventory stays on ring.
            if bid not in self._delivery_mode:
                self._delivery_mode.discard(bid)
                self._deliver_route_by_bot.pop(bid, None)
                self._deliver_goal_by_bot.pop(bid, None)
                if bid in self._return_mode:
                    self._pickup_target_item_by_bot.pop(bid, None)
                    self._pickup_target_cell_by_bot.pop(bid, None)
                    self._pickup_source_by_bot.pop(bid, None)

        returning_bots = [
            bot for bot in bots_sorted
            if int(bot.id) in self._return_mode
        ]
        for bot in bots_sorted:
            bid = int(bot.id)
            if bid in self._delivery_mode or bid in self._return_mode:
                continue
            orbit_bots.append(bot)
        orbit_bot_ids = {int(bot.id) for bot in orbit_bots}
        preview_designated_bots = self._select_preview_designated_bots(
            bots_orbit=orbit_bots,
            state=state,
            preview_budget=preview_budget,
        )
        preview_budget_remaining = int(preview_budget)

        orbit_count = len(orbit_bots)
        spacing = 2 if orbit_count > 0 and loop_len >= orbit_count * 2 else max(1, loop_len // max(1, orbit_count))
        self._loop_spacing = spacing
        rejoin_headroom = min(len(returning_bots), int(self._rejoin_slot_headroom))
        token_target = max(int(orbit_target), int(orbit_count) + int(rejoin_headroom))
        slot_indices = self._ensure_orbit_tokens(target_count=token_target, loop_len=loop_len)
        self._reassign_slots(
            bots_orbit=orbit_bots,
            slot_indices=slot_indices,
            loop_index_by_cell=loop_index_by_cell,
        )
        occupied_ring_indices = {
            loop_index_by_cell[start_by_bot[bid]]
            for bid in orbit_bot_ids
            if start_by_bot.get(bid) in loop_index_by_cell
        }
        return_slot_by_bot = self._assign_return_slots(
            returning_bots=returning_bots,
            slot_indices=slot_indices,
            occupied_ring_indices=occupied_ring_indices,
            loop_index_by_cell=loop_index_by_cell,
            start_by_bot=start_by_bot,
            grid=grid,
            blocked=set(item_blocked),
        )
        queue_capacity = max(1, len(self._delivery_mode))
        queue_cells: list[tuple[int, int]] = []
        if topology.stop_line != drop_off:
            queue_cells.append(topology.stop_line)
        for cell in topology.drop_queue_cells:
            if cell == drop_off or cell == topology.stop_line:
                continue
            queue_cells.append(cell)
            if len(queue_cells) >= queue_capacity:
                break
        if len(queue_cells) < queue_capacity:
            fallback_cells = self._drop_queue_cells(
                drop_off=drop_off,
                grid=grid,
                blocked=set(item_blocked),
                limit=queue_capacity + 1,
            )
            for cell in fallback_cells:
                if cell == drop_off or cell == topology.stop_line:
                    continue
                if cell in queue_cells:
                    continue
                queue_cells.append(cell)
                if len(queue_cells) >= queue_capacity:
                    break
        delivery_order = sorted(
            self._delivery_mode,
            key=lambda bid: (
                abs(int(start_by_bot[bid][0]) - int(drop_off[0])) + abs(int(start_by_bot[bid][1]) - int(drop_off[1])),
                -delivery_scores_by_bot.get(bid, 0.0),
                bid,
            ),
        )
        delivery_target_by_bot: dict[int, tuple[int, int]] = {}
        queue_rank_by_bot: dict[int, int] = {}
        for idx, bid in enumerate(delivery_order):
            queue_rank_by_bot[bid] = idx
            if idx == 0:
                delivery_target_by_bot[bid] = drop_off
            else:
                q_idx = min(idx - 1, max(0, len(queue_cells) - 1))
                delivery_target_by_bot[bid] = queue_cells[q_idx]

        # 1) Opportunistic pickup from ring-adjacent cells.
        for bot in orbit_bots:
            bid = int(bot.id)
            self._bot_mode_by_bot[bid] = ORBIT_MODE
            start = start_by_bot[bid]
            allow_preview_adjacent = (
                preview_budget_remaining > 0
                and bid in preview_designated_bots
                and int(sum(active_need_working.values())) <= 0
            )
            preview_need_adjacent = preview_need if allow_preview_adjacent else Counter()
            pick_choice = self._pick_adjacent_item(
                bot_pos=start,
                bot_inventory_size=len(bot.inventory),
                state=state,
                active_need=active_need_working,
                preview_need=preview_need_adjacent,
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
                    preview_budget_remaining = max(0, int(preview_budget_remaining) - 1)
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
            queue_rank = queue_rank_by_bot.get(bid, 0)
            delivery_target = delivery_target_by_bot.get(bid, drop_off)
            target_type_by_bot[bid] = "deliver" if queue_rank == 0 else "deliver_queue"
            slot_idx_by_bot[bid] = int(self._slot_by_bot.get(bid, 0))
            self._bot_mode_by_bot[bid] = DELIVER_MODE if queue_rank == 0 else QUEUE_MODE
            active_cargo_count = len(items_matching_active(bot, state))

            # Fixed expert branch rule at (8,15):
            # - exit to delivery corridor entry only with quota+useful cargo
            # - otherwise continue clockwise on orbit.
            if start in loop_index_by_cell:
                cur_idx = int(loop_index_by_cell[start])
                cw_next = self._loop_points[(cur_idx + 1) % loop_len]
                if start == branch_exit:
                    branch_exit_visits += 1
                    can_exit = (
                        int(flow_delivery_cap) > 0
                        and int(active_cargo_count) > 0
                        and bool(grid.is_walkable(int(delivery_entry[0]), int(delivery_entry[1])))
                        and delivery_entry not in item_blocked
                    )
                    if can_exit and delivery_entry not in claimed_targets:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, delivery_entry))
                        movement_target_by_bot[bid] = delivery_entry
                        move_plans.append((bid, start, delivery_entry))
                        claimed_targets.add(delivery_entry)
                        branch_to_delivery += 1
                    else:
                        lane_next = branch_continue if branch_continue == cw_next else cw_next
                        if lane_next not in claimed_targets:
                            action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, lane_next))
                            movement_target_by_bot[bid] = lane_next
                            move_plans.append((bid, start, lane_next))
                            claimed_targets.add(lane_next)
                            branch_continue_moves += 1
                        else:
                            action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                            movement_target_by_bot[bid] = None
                            occupied_now.add(start)
                            self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                            assignment_reservation_conflicts += 1
                            branch_waits += 1
                    self.last_assignment_snapshot[bid] = {
                        "target_type": target_type_by_bot[bid],
                        "target_id": None,
                        "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                        "drop_off": [int(drop_off[0]), int(drop_off[1])],
                        "source": "deliver" if queue_rank == 0 else "queue",
                        "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                        "phase": self._orbit_phase,
                        "queue_rank": queue_rank,
                    }
                    continue

                if cw_next not in claimed_targets:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, cw_next))
                    movement_target_by_bot[bid] = cw_next
                    move_plans.append((bid, start, cw_next))
                    claimed_targets.add(cw_next)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target_by_bot[bid] = None
                    occupied_now.add(start)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                    assignment_reservation_conflicts += 1
                self.last_assignment_snapshot[bid] = {
                    "target_type": target_type_by_bot[bid],
                    "target_id": None,
                    "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                    "drop_off": [int(drop_off[0]), int(drop_off[1])],
                    "source": "deliver" if queue_rank == 0 else "queue",
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "phase": self._orbit_phase,
                    "queue_rank": queue_rank,
                }
                continue

            if start == drop_off:
                if items_matching_active(bot, state):
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.DROP_OFF)
                    occupied_now.add(start)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    occupied_now.add(start)
                    self._delivery_mode.discard(bid)
                    self._return_mode.add(bid)
                    self._bot_mode_by_bot[bid] = RETURN_MODE
                self._deliver_route_by_bot.pop(bid, None)
                self._deliver_goal_by_bot.pop(bid, None)
                movement_target_by_bot[bid] = None
                self.last_assignment_snapshot[bid] = {
                    "target_type": target_type_by_bot[bid],
                    "target_id": None,
                    "pickup_pos": None,
                    "drop_off": [int(drop_off[0]), int(drop_off[1])],
                    "source": "deliver" if queue_rank == 0 else "queue",
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "phase": self._orbit_phase,
                    "queue_rank": queue_rank,
                }
                continue

            if start == delivery_target and delivery_target != drop_off:
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                movement_target_by_bot[bid] = None
                occupied_now.add(start)
                self.last_assignment_snapshot[bid] = {
                    "target_type": target_type_by_bot[bid],
                    "target_id": None,
                    "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                    "drop_off": [int(drop_off[0]), int(drop_off[1])],
                    "source": "queue",
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "phase": self._orbit_phase,
                    "queue_rank": queue_rank,
                }
                continue

            # Directional delivery corridor rule:
            # y=delivery_entry.y is one-way left toward drop-off corridor.
            if int(start[1]) == int(delivery_entry[1]) and int(start[0]) > int(drop_off[0]):
                # Queue bots may peel to queue cells once horizontally aligned.
                if (
                    queue_rank > 0
                    and delivery_target != drop_off
                    and int(start[0]) <= int(delivery_target[0])
                ):
                    path_to_queue = bfs_shortest_path(grid, start, delivery_target, blocked=set(item_blocked))
                    if path_to_queue is not None and len(path_to_queue) >= 2:
                        nxt = (int(path_to_queue[1][0]), int(path_to_queue[1][1]))
                        if nxt not in claimed_targets:
                            action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                            movement_target_by_bot[bid] = nxt
                            move_plans.append((bid, start, nxt))
                            claimed_targets.add(nxt)
                            self.last_assignment_snapshot[bid] = {
                                "target_type": target_type_by_bot[bid],
                                "target_id": None,
                                "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                                "drop_off": [int(drop_off[0]), int(drop_off[1])],
                                "source": "queue",
                                "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                                "phase": self._orbit_phase,
                                "queue_rank": queue_rank,
                            }
                            continue

                lane_next = (int(start[0]) - 1, int(delivery_entry[1]))
                if lane_next not in claimed_targets:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, lane_next))
                    movement_target_by_bot[bid] = lane_next
                    move_plans.append((bid, start, lane_next))
                    claimed_targets.add(lane_next)
                else:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target_by_bot[bid] = None
                    occupied_now.add(start)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                    assignment_reservation_conflicts += 1
                self.last_assignment_snapshot[bid] = {
                    "target_type": target_type_by_bot[bid],
                    "target_id": None,
                    "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                    "drop_off": [int(drop_off[0]), int(drop_off[1])],
                    "source": "deliver" if queue_rank == 0 else "queue",
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "phase": self._orbit_phase,
                    "queue_rank": queue_rank,
                }
                continue

            if (
                bid not in self._deliver_route_by_bot
                or self._deliver_goal_by_bot.get(bid) != delivery_target
            ):
                self._deliver_route_by_bot[bid] = self._plan_delivery_route(
                    start=start,
                    drop_off=delivery_target,
                    grid=grid,
                    blocked=set(item_blocked),
                )
                self._deliver_goal_by_bot[bid] = delivery_target
            route = list(self._deliver_route_by_bot.get(bid, []))
            while route and route[0] == start:
                route.pop(0)
            self._deliver_route_by_bot[bid] = list(route)
            delivery_target = route[0] if route else delivery_target

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
                "target_type": target_type_by_bot[bid],
                "target_id": None,
                "pickup_pos": [int(delivery_target[0]), int(delivery_target[1])],
                "drop_off": [int(drop_off[0]), int(drop_off[1])],
                "source": "deliver" if queue_rank == 0 else "queue",
                "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                "phase": self._orbit_phase,
                "queue_rank": queue_rank,
            }

        # 3) Controlled return to orbit through reserved free slots.
        return_staging_target = topology.return_buffer_cells[0] if topology.return_buffer_cells else (queue_cells[-1] if queue_cells else drop_off)
        admission_cell = (int(rejoin_branch[0] + 1), int(rejoin_branch[1]))
        for bot in returning_bots:
            bid = int(bot.id)
            if bid in action_by_bot:
                continue
            start = start_by_bot[bid]
            reserved_slot = return_slot_by_bot.get(bid)
            slot_idx_by_bot[bid] = int(reserved_slot) if reserved_slot is not None else -1
            target_type_by_bot[bid] = "return_ring"
            self._bot_mode_by_bot[bid] = RETURN_MODE

            if start == rejoin_branch:
                rejoin_branch_visits += 1
                can_admit = (
                    reserved_slot is not None
                    and admission_cell in loop_index_by_cell
                    and admission_cell not in claimed_targets
                )
                if can_admit:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, admission_cell))
                    movement_target_by_bot[bid] = admission_cell
                    move_plans.append((bid, start, admission_cell))
                    claimed_targets.add(admission_cell)
                    self._slot_by_bot[bid] = int(reserved_slot)
                    rejoin_admissions += 1
                else:
                    # Keep a stable gate queue at rejoin branch.
                    # If slot is already reserved, avoid bouncing to staging cells:
                    # bouncing causes route drift and extra spacing conflicts.
                    if reserved_slot is not None:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                    else:
                        fallback_goal = return_staging_target if return_staging_target != start else rejoin_branch
                        if fallback_goal != start:
                            path = bfs_shortest_path(grid, start, fallback_goal, blocked=set(item_blocked))
                            if path is not None and len(path) >= 2 and tuple(path[1]) not in claimed_targets:
                                nxt = (int(path[1][0]), int(path[1][1]))
                                action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                                movement_target_by_bot[bid] = nxt
                                move_plans.append((bid, start, nxt))
                                claimed_targets.add(nxt)
                            else:
                                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                                movement_target_by_bot[bid] = None
                                occupied_now.add(start)
                                self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                        else:
                            action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                            movement_target_by_bot[bid] = None
                            occupied_now.add(start)
                    gate_admission_denied += 1
                    rejoin_denials += 1
                self.last_assignment_snapshot[bid] = {
                    "target_type": "return_ring",
                    "target_id": None,
                    "pickup_pos": [int(rejoin_branch[0]), int(rejoin_branch[1])],
                    "drop_off": None,
                    "source": "return",
                    "slot_idx": int(reserved_slot) if reserved_slot is not None else -1,
                    "phase": self._orbit_phase,
                }
                continue

            if start in loop_index_by_cell:
                if reserved_slot is None:
                    illegal_reentry_attempts += 1
                    gate_admission_denied += 1
                    cur_idx = int(loop_index_by_cell[start])
                    nxt = self._loop_points[(cur_idx + 1) % loop_len]
                    if nxt not in claimed_targets:
                        # Stay in clockwise traffic flow while waiting for admission slot.
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                        movement_target_by_bot[bid] = nxt
                        move_plans.append((bid, start, nxt))
                        claimed_targets.add(nxt)
                    else:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                        rejoin_denials += 1
                else:
                    cur_idx = int(loop_index_by_cell[start])
                    if cur_idx == int(reserved_slot):
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._return_mode.discard(bid)
                        self._return_slot_by_bot.pop(bid, None)
                        self._bot_mode_by_bot[bid] = ORBIT_MODE
                    else:
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
                            assignment_reservation_conflicts += 1
                self.last_assignment_snapshot[bid] = {
                    "target_type": "return_ring",
                    "target_id": None,
                    "pickup_pos": [int(rejoin_branch[0]), int(rejoin_branch[1])],
                    "drop_off": None,
                    "source": "return",
                    "slot_idx": int(reserved_slot) if reserved_slot is not None else -1,
                    "phase": self._orbit_phase,
                }
                continue

            goal = rejoin_branch if reserved_slot is not None else return_staging_target
            if reserved_slot is None:
                gate_admission_denied += 1
            path = bfs_shortest_path(grid, start, goal, blocked=set(item_blocked))
            if path is not None and len(path) >= 2:
                preferred = self._best_step_toward(
                    start=start,
                    goal=goal,
                    grid=grid,
                    blocked=set(item_blocked),
                    forbidden=claimed_targets,
                    prefer_axis="x" if reserved_slot is None else "y",
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
                if reserved_slot is None:
                    rejoin_denials += 1

            self.last_assignment_snapshot[bid] = {
                "target_type": "return_ring",
                "target_id": None,
                "pickup_pos": [int(goal[0]), int(goal[1])],
                "drop_off": None,
                "source": "return",
                "slot_idx": int(reserved_slot) if reserved_slot is not None else -1,
                "phase": self._orbit_phase,
            }

        # 4) Pickup detours from ring (active-first, bounded).
        detour_assignments = self._assign_pickup_detours(
            state=state,
            bots_orbit=orbit_bots,
            active_need=active_need_working,
            preview_need=preview_need,
            preview_budget=preview_budget_remaining,
            active_stall_rounds=int(self._active_stall_rounds),
            rounds_left=int(rounds_left),
            detour_limit=detour_limit,
            orbit_floor=orbit_target,
            preview_designated_bots=preview_designated_bots,
            reserved_item_ids=reserved_item_ids,
            grid=grid,
            blocked=set(item_blocked),
        )
        items_by_id = {str(item.id): item for item in state.items}
        for bot in orbit_bots:
            bid = int(bot.id)
            if bid in action_by_bot:
                continue
            mission = detour_assignments.get(bid)
            if mission is None:
                continue
            item_id, item_type, source, pickup_cell, reserved_rejoin_slot = mission
            start = start_by_bot[bid]
            slot_idx_by_bot[bid] = int(self._slot_by_bot.get(bid, 0))
            target_type_by_bot[bid] = "pickup_detour"
            self._bot_mode_by_bot[bid] = "pickup_detour"
            self._detour_rejoin_slot_by_bot[bid] = int(reserved_rejoin_slot)

            yield_until = int(self._yield_hold_until_round_by_bot.get(bid, 0))
            yield_cell = self._yield_hold_cell_by_bot.get(bid)
            if yield_cell is not None and int(state.round) < yield_until:
                if start == yield_cell:
                    action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                    movement_target_by_bot[bid] = None
                    occupied_now.add(start)
                    self._round_wait_reason_by_bot[bid] = "wait_due_to_spacing_guard"
                else:
                    yield_step = self._best_step_toward(
                        start=start,
                        goal=yield_cell,
                        grid=grid,
                        blocked=set(item_blocked),
                        forbidden=claimed_targets,
                        prefer_axis="x",
                    )
                    if yield_step is not None:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, yield_step))
                        movement_target_by_bot[bid] = yield_step
                        move_plans.append((bid, start, yield_step))
                        claimed_targets.add(yield_step)
                    else:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_collision_block"
                self.last_assignment_snapshot[bid] = {
                    "target_type": "pickup_detour",
                    "target_id": item_id,
                    "pickup_pos": [int(pickup_cell[0]), int(pickup_cell[1])],
                    "drop_off": None,
                    "source": "yield_hold",
                    "item_type": item_type,
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "reserved_rejoin_slot": int(reserved_rejoin_slot),
                    "phase": self._orbit_phase,
                }
                continue
            if int(state.round) >= yield_until:
                self._yield_hold_cell_by_bot.pop(bid, None)
                self._yield_hold_until_round_by_bot.pop(bid, None)

            item = items_by_id.get(item_id)
            if item is None:
                self._pickup_target_item_by_bot.pop(bid, None)
                self._pickup_target_cell_by_bot.pop(bid, None)
                self._pickup_source_by_bot.pop(bid, None)
                self._detour_rejoin_slot_by_bot.pop(bid, None)
                continue

            item_pos = (int(item.position[0]), int(item.position[1]))
            if len(bot.inventory) < 3 and self._can_pick_from(start, item_pos):
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=item_id)
                movement_target_by_bot[bid] = None
                occupied_now.add(start)
                reserved_item_ids.add(item_id)
                self._pickup_target_item_by_bot.pop(bid, None)
                self._pickup_target_cell_by_bot.pop(bid, None)
                self._pickup_source_by_bot.pop(bid, None)
                self._detour_rejoin_slot_by_bot.pop(bid, None)
                if source == "active":
                    pick_active_count += 1
                else:
                    pick_preview_count += 1
                self.last_assignment_snapshot[bid] = {
                    "target_type": "pickup_detour",
                    "target_id": item_id,
                    "pickup_pos": [int(start[0]), int(start[1])],
                    "drop_off": None,
                    "source": source,
                    "item_type": item_type,
                    "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                    "reserved_rejoin_slot": int(reserved_rejoin_slot),
                    "phase": self._orbit_phase,
                }
                continue

            if source == "active":
                detour_active_assigned += 1
            else:
                detour_preview_assigned += 1
            if start == pickup_cell:
                # Cannot pick right now, fall back to orbit next.
                self._pickup_target_item_by_bot.pop(bid, None)
                self._pickup_target_cell_by_bot.pop(bid, None)
                self._pickup_source_by_bot.pop(bid, None)
                self._detour_rejoin_slot_by_bot.pop(bid, None)
                continue

            preferred = self._best_step_toward(
                start=start,
                goal=pickup_cell,
                grid=grid,
                blocked=set(item_blocked),
                forbidden=claimed_targets,
                prefer_axis="x" if (slot_idx_by_bot[bid] % 2 == 0) else "y",
            )
            if preferred is None:
                # Keep the mission but let orbit movement handle this round.
                assignment_reservation_conflicts += 1
                continue

            action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, preferred))
            movement_target_by_bot[bid] = preferred
            move_plans.append((bid, start, preferred))
            claimed_targets.add(preferred)
            self.last_assignment_snapshot[bid] = {
                "target_type": "pickup_detour",
                "target_id": item_id,
                "pickup_pos": [int(pickup_cell[0]), int(pickup_cell[1])],
                "drop_off": None,
                "source": source,
                "item_type": item_type,
                "slot_idx": int(self._slot_by_bot.get(bid, 0)),
                "reserved_rejoin_slot": int(reserved_rejoin_slot),
                "phase": self._orbit_phase,
            }

        # 4.5) Delivery-corridor priority: active-cargo deliverers outrank active pickup detours.
        active_detour_bids_for_priority = {
            int(bid)
            for bid, mission in target_type_by_bot.items()
            if str(mission) == "pickup_detour"
            and str((self.last_assignment_snapshot.get(int(bid)) or {}).get("source", "")) in {"active", "yield_hold"}
        }
        deliver_priority_bids = [
            int(bot.id)
            for bot in bots_sorted
            if int(bot.id) in self._delivery_mode
            and len(items_matching_active(bot, state)) > 0
        ]
        occupied_starts = {tuple(pos) for pos in start_by_bot.values()}
        for deliver_bid in sorted(deliver_priority_bids):
            deliver_start = start_by_bot.get(int(deliver_bid))
            deliver_target = movement_target_by_bot.get(int(deliver_bid))
            if deliver_start is None or deliver_target is None:
                continue
            for detour_bid in sorted(active_detour_bids_for_priority):
                if int(detour_bid) == int(deliver_bid):
                    continue
                detour_start = start_by_bot.get(int(detour_bid))
                detour_target = movement_target_by_bot.get(int(detour_bid))
                if detour_start is None:
                    continue
                if detour_start != deliver_target:
                    continue
                # No conflict if detour is already clearing elsewhere.
                if detour_target is not None and detour_target not in {deliver_start, deliver_target}:
                    continue

                detour_snap = self.last_assignment_snapshot.get(int(detour_bid)) or {}
                pickup_raw = detour_snap.get("pickup_pos")
                pickup_cell: tuple[int, int] | None = None
                if isinstance(pickup_raw, (list, tuple)) and len(pickup_raw) == 2:
                    pickup_cell = (int(pickup_raw[0]), int(pickup_raw[1]))

                side_steps: list[tuple[int, int, int, tuple[int, int]]] = []
                for nx, ny in grid.neighbors(int(detour_start[0]), int(detour_start[1])):
                    step = (int(nx), int(ny))
                    if step == deliver_start or step == deliver_target:
                        continue
                    if step in item_blocked or step in claimed_targets:
                        continue
                    if step in occupied_starts:
                        continue
                    if not grid.is_walkable(int(step[0]), int(step[1])):
                        continue
                    pickup_dist = (
                        abs(int(step[0]) - int(pickup_cell[0])) + abs(int(step[1]) - int(pickup_cell[1]))
                        if pickup_cell is not None
                        else 999
                    )
                    lane_penalty = 10 if int(step[1]) == int(delivery_entry[1]) else 0
                    side_steps.append((int(lane_penalty), int(pickup_dist), int(step[0]), step))
                if not side_steps:
                    continue

                side_steps.sort(key=lambda row: (int(row[0]), int(row[1]), int(row[2]), int(row[3][1])))
                chosen = side_steps[0][3]
                if detour_target is not None:
                    claimed_targets.discard(detour_target)
                claimed_targets.add(chosen)
                movement_target_by_bot[int(detour_bid)] = chosen
                action_by_bot[int(detour_bid)] = BotActionCommand(
                    bot=int(detour_bid),
                    action=action_for_move(detour_start, chosen),
                )
                hold_until = int(state.round) + 2
                self._yield_hold_cell_by_bot[int(detour_bid)] = chosen
                self._yield_hold_until_round_by_bot[int(detour_bid)] = int(hold_until)
                replaced = False
                for idx, (mbid, mstart, _mtarget) in enumerate(move_plans):
                    if int(mbid) == int(detour_bid) and mstart == detour_start:
                        move_plans[idx] = (int(detour_bid), detour_start, chosen)
                        replaced = True
                        break
                if not replaced:
                    move_plans.append((int(detour_bid), detour_start, chosen))
                break

        # 5) Orbit movement (clockwise only on ring).
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
            target_type_by_bot[bid] = ORBIT_MODE
            self._bot_mode_by_bot[bid] = ORBIT_MODE

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
                        assignment_reservation_conflicts += 1
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
                    # If direct route to slot-goal is unavailable, drift toward orbit branch instead of stalling.
                    fallback_goal = branch_exit
                    fallback_path = bfs_shortest_path(grid, start, fallback_goal, blocked=set(item_blocked))
                    moved = False
                    if fallback_path is not None and len(fallback_path) >= 2:
                        preferred = self._best_step_toward(
                            start=start,
                            goal=fallback_goal,
                            grid=grid,
                            blocked=set(item_blocked),
                            forbidden=claimed_targets,
                            prefer_axis="x",
                        )
                        nxt = preferred if preferred is not None else (int(fallback_path[1][0]), int(fallback_path[1][1]))
                        if nxt not in claimed_targets:
                            action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, nxt))
                            movement_target_by_bot[bid] = nxt
                            move_plans.append((bid, start, nxt))
                            claimed_targets.add(nxt)
                            moved = True
                    if not moved:
                        for cand in (
                            (int(start[0]) - 1, int(start[1])),
                            (int(start[0]), int(start[1]) - 1),
                            (int(start[0]), int(start[1]) + 1),
                            (int(start[0]) + 1, int(start[1])),
                        ):
                            if not grid.is_walkable(int(cand[0]), int(cand[1])):
                                continue
                            if cand in item_blocked or cand in claimed_targets:
                                continue
                            action_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(start, cand))
                            movement_target_by_bot[bid] = cand
                            move_plans.append((bid, start, cand))
                            claimed_targets.add(cand)
                            moved = True
                            break
                    if not moved:
                        action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                        movement_target_by_bot[bid] = None
                        occupied_now.add(start)
                        self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"

            self.last_assignment_snapshot[bid] = {
                "target_type": ORBIT_MODE,
                "target_id": None,
                "pickup_pos": [int(goal[0]), int(goal[1])],
                "drop_off": None,
                "source": ORBIT_MODE,
                "slot_idx": int(slot_idx),
                "phase": self._orbit_phase,
            }

        # 5.5) Targeted swap-breaker near upper aisles:
        # let an active pickup detour pass when a full non-active orbit bot blocks with a head-on swap.
        active_detour_bids = {
            int(bid)
            for bid, mission in target_type_by_bot.items()
            if str(mission) == "pickup_detour"
            and str((self.last_assignment_snapshot.get(int(bid)) or {}).get("source", "")) == "active"
        }
        upper_lane_y = int(branch_exit[1]) - 1
        for detour_bid in sorted(active_detour_bids):
            detour_start = start_by_bot.get(int(detour_bid))
            detour_target = movement_target_by_bot.get(int(detour_bid))
            if detour_start is None or detour_target is None:
                continue
            if int(detour_start[1]) > upper_lane_y:
                continue

            for orbit_bid in sorted(orbit_bot_ids):
                orbit_bid = int(orbit_bid)
                if orbit_bid == int(detour_bid):
                    continue
                orbit_start = start_by_bot.get(orbit_bid)
                orbit_step_target = movement_target_by_bot.get(orbit_bid)
                if orbit_start is None or orbit_step_target is None:
                    continue
                if orbit_start != detour_target or orbit_step_target != detour_start:
                    continue
                if orbit_start in loop_index_by_cell:
                    continue

                orbit_bot = bot_by_id.get(orbit_bid)
                if orbit_bot is None:
                    continue
                orbit_active_matches = len(items_matching_active(orbit_bot, state))

                # Deliverers with active cargo have lane priority; let detour yield sideways.
                if orbit_bid in self._delivery_mode and orbit_active_matches > 0:
                    detour_snap = self.last_assignment_snapshot.get(int(detour_bid)) or {}
                    pickup_raw = detour_snap.get("pickup_pos")
                    pickup_cell: tuple[int, int] | None = None
                    if isinstance(pickup_raw, (list, tuple)) and len(pickup_raw) == 2:
                        pickup_cell = (int(pickup_raw[0]), int(pickup_raw[1]))
                    occupied_starts = {tuple(pos) for pos in start_by_bot.values()}
                    detour_side_steps: list[tuple[int, int, int, tuple[int, int]]] = []
                    for nx, ny in grid.neighbors(int(detour_start[0]), int(detour_start[1])):
                        step = (int(nx), int(ny))
                        if step == orbit_start or step == detour_target:
                            continue
                        if step in item_blocked or step in claimed_targets:
                            continue
                        if step in occupied_starts:
                            continue
                        if not grid.is_walkable(int(step[0]), int(step[1])):
                            continue
                        pickup_dist = (
                            abs(int(step[0]) - int(pickup_cell[0])) + abs(int(step[1]) - int(pickup_cell[1]))
                            if pickup_cell is not None
                            else 999
                        )
                        detour_side_steps.append((int(pickup_dist), int(step[0]), int(step[1]), step))
                    if detour_side_steps:
                        detour_side_steps.sort(key=lambda row: (int(row[0]), int(row[1]), int(row[2])))
                        chosen = detour_side_steps[0][3]
                        claimed_targets.discard(detour_target)
                        claimed_targets.add(chosen)
                        movement_target_by_bot[int(detour_bid)] = chosen
                        action_by_bot[int(detour_bid)] = BotActionCommand(
                            bot=int(detour_bid),
                            action=action_for_move(detour_start, chosen),
                        )
                        for idx, (mbid, mstart, _mtarget) in enumerate(move_plans):
                            if int(mbid) == int(detour_bid) and mstart == detour_start:
                                move_plans[idx] = (int(detour_bid), detour_start, chosen)
                                break
                        break

                active_matches = int(orbit_active_matches)
                full_non_active = len(getattr(orbit_bot, "inventory", []) or []) >= 3 and active_matches <= 0
                if not full_non_active:
                    continue

                side_candidates: list[tuple[int, int]] = []
                for dx in (-1, 1):
                    step = (int(orbit_start[0]) + int(dx), int(orbit_start[1]))
                    if step == detour_start or step == detour_target:
                        continue
                    if step in claimed_targets or step in item_blocked:
                        continue
                    if not grid.is_walkable(int(step[0]), int(step[1])):
                        continue
                    side_candidates.append(step)
                if not side_candidates:
                    continue

                side_candidates.sort(
                    key=lambda cell: (
                        abs(int(cell[0]) - int(branch_continue[0])) + abs(int(cell[1]) - int(branch_continue[1])),
                        int(cell[0]),
                        int(cell[1]),
                    )
                )
                chosen = side_candidates[0]
                claimed_targets.discard(orbit_step_target)
                claimed_targets.add(chosen)
                movement_target_by_bot[orbit_bid] = chosen
                action_by_bot[orbit_bid] = BotActionCommand(
                    bot=orbit_bid,
                    action=action_for_move(orbit_start, chosen),
                )
                for idx, (mbid, mstart, _mtarget) in enumerate(move_plans):
                    if int(mbid) == orbit_bid and mstart == orbit_start:
                        move_plans[idx] = (orbit_bid, orbit_start, chosen)
                        break
                break

        # Evict full non-active blockers sitting on active pickup cells.
        occupied_starts_by_cell: dict[tuple[int, int], int] = {
            tuple(pos): int(bid) for bid, pos in start_by_bot.items()
        }
        for detour_bid in sorted(active_detour_bids):
            detour_snap = self.last_assignment_snapshot.get(int(detour_bid)) or {}
            pickup_raw = detour_snap.get("pickup_pos")
            if not isinstance(pickup_raw, (list, tuple)) or len(pickup_raw) != 2:
                continue
            pickup_cell = (int(pickup_raw[0]), int(pickup_raw[1]))
            blocker_bid = occupied_starts_by_cell.get(pickup_cell)
            if blocker_bid is None or blocker_bid == int(detour_bid):
                continue
            if blocker_bid in self._delivery_mode or blocker_bid in self._return_mode:
                continue
            blocker_bot = bot_by_id.get(int(blocker_bid))
            if blocker_bot is None:
                continue
            blocker_active_hits = len(items_matching_active(blocker_bot, state))
            blocker_full_non_active = (
                len(getattr(blocker_bot, "inventory", []) or []) >= 3
                and blocker_active_hits <= 0
            )
            if not blocker_full_non_active:
                continue
            blocker_start = start_by_bot.get(int(blocker_bid))
            if blocker_start is None:
                continue
            blocker_target = movement_target_by_bot.get(int(blocker_bid))
            if blocker_target is not None and blocker_target != blocker_start:
                # Already moving away this round.
                continue

            detour_start = start_by_bot.get(int(detour_bid))
            side_steps: list[tuple[int, int]] = []
            for nx, ny in grid.neighbors(int(blocker_start[0]), int(blocker_start[1])):
                step = (int(nx), int(ny))
                if step == pickup_cell:
                    continue
                if detour_start is not None and step == detour_start:
                    continue
                if step in item_blocked or step in claimed_targets:
                    continue
                if step in occupied_starts_by_cell:
                    continue
                if not grid.is_walkable(int(step[0]), int(step[1])):
                    continue
                side_steps.append(step)
            if not side_steps:
                continue

            side_steps.sort(
                key=lambda cell: (
                    abs(int(cell[0]) - int(branch_continue[0])) + abs(int(cell[1]) - int(branch_continue[1])),
                    int(cell[0]),
                    int(cell[1]),
                )
            )
            chosen = side_steps[0]
            if blocker_target is not None:
                claimed_targets.discard(blocker_target)
            claimed_targets.add(chosen)
            movement_target_by_bot[int(blocker_bid)] = chosen
            action_by_bot[int(blocker_bid)] = BotActionCommand(
                bot=int(blocker_bid),
                action=action_for_move(blocker_start, chosen),
            )
            replaced = False
            for idx, (mbid, mstart, _mtarget) in enumerate(move_plans):
                if int(mbid) == int(blocker_bid) and mstart == blocker_start:
                    move_plans[idx] = (int(blocker_bid), blocker_start, chosen)
                    replaced = True
                    break
            if not replaced:
                move_plans.append((int(blocker_bid), blocker_start, chosen))

        # 6) Resolve one-tick move collisions.
        sanitized_move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        for bid, start, target in move_plans:
            if abs(int(start[0]) - int(target[0])) + abs(int(start[1]) - int(target[1])) != 1:
                action_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)
                movement_target_by_bot[bid] = None
                occupied_now.add(start)
                self._round_wait_reason_by_bot[bid] = "wait_due_to_no_assignment"
                assignment_reservation_conflicts += 1
                continue
            sanitized_move_plans.append((int(bid), start, target))
        move_plans = sanitized_move_plans

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
        spacing_guard_bot_ids = {
            int(bid)
            for bid in orbit_bot_ids
            if str(target_type_by_bot.get(int(bid), ORBIT_MODE)) == ORBIT_MODE
        }
        for _ in range(max(1, loop_len)):
            ring_state: list[tuple[int, int, bool]] = []
            for bid in spacing_guard_bot_ids:
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

        for bid, snap in self.last_assignment_snapshot.items():
            snap["mobility_state"] = self._mobility_state_for_bot(bot_id=int(bid), round_index=int(state.round))
            snap["cargo_class"] = cargo_class_by_bot.get(int(bid), CARGO_DEADWEIGHT)

        assignment_churn_count = 0
        mission_type_orbit = 0
        mission_type_pickup_detour = 0
        mission_type_pick_item = 0
        mission_type_deliver = 0
        mission_type_queue = 0
        mission_type_rejoin = 0
        next_prev_mission_type_by_bot: dict[int, str] = {}

        final_actions: list[BotActionCommand] = []
        for bot in bots_sorted:
            bid = int(bot.id)
            start = start_by_bot[bid]
            cmd = final_cmd_by_bot.get(bid, BotActionCommand(bot=bid, action=BotAction.WAIT))
            movement_target = final_target_by_bot.get(bid)
            mobility_state = self._mobility_state_for_bot(bot_id=bid, round_index=int(state.round))
            mission_type = target_type_by_bot.get(bid, "orbit_wall")
            next_prev_mission_type_by_bot[bid] = mission_type
            if mission_type == ORBIT_MODE:
                mission_type_orbit += 1
            elif mission_type == "pickup_detour":
                mission_type_pickup_detour += 1
            elif mission_type == "pick_item":
                mission_type_pick_item += 1
            elif mission_type == "deliver":
                mission_type_deliver += 1
            elif mission_type == "deliver_queue":
                mission_type_queue += 1
            elif mission_type == "return_ring":
                mission_type_rejoin += 1

            source_hint = str((self.last_assignment_snapshot.get(bid) or {}).get("source", ""))
            mission_value = 8.0
            if mission_type == "deliver":
                mission_value = 90.0
            elif mission_type == "deliver_queue":
                mission_value = 45.0
            elif mission_type == "pickup_detour":
                mission_value = 62.0 if source_hint == "active" else 24.0
            elif mission_type == "pick_item":
                mission_value = 55.0 if source_hint == "active" else 22.0
            elif mission_type == "return_ring":
                mission_value = 28.0
            elif mission_type == ORBIT_MODE:
                mission_value = 12.0
            if cmd.action == BotAction.WAIT:
                mission_value -= 4.0
            mission_value_by_bot[bid] = float(mission_value)

            prev_type = self._prev_mission_type_by_bot.get(bid, "")
            churn = 1.0 if prev_type and prev_type != mission_type else 0.0
            if churn > 0.0:
                assignment_churn_count += 1
            final_actions.append(cmd)
            self.last_pre_collision_actions[bid] = {
                "bot_id": bid,
                "start": [int(start[0]), int(start[1])],
                "action": str(cmd.action.value),
                "item_id": cmd.item_id,
                "target_type": mission_type,
                "mission_type": mission_type,
                "mission_value": float(mission_value),
                "assignment_churn": float(churn),
                "movement_target": [int(movement_target[0]), int(movement_target[1])] if movement_target is not None else None,
                "slot_idx": int(slot_idx_by_bot.get(bid, 0)),
                "phase": self._orbit_phase,
                "mobility_state": mobility_state,
                "cargo_class": cargo_class_by_bot.get(bid, CARGO_DEADWEIGHT),
            }
        self._prev_mission_type_by_bot = next_prev_mission_type_by_bot

        occupied_idx = [
            loop_index_by_cell[start_by_bot[bid]]
            for bid in spacing_guard_bot_ids
            if start_by_bot.get(bid) in loop_index_by_cell
        ]
        unique_idx = sorted(set(occupied_idx))

        min_gap = 0
        cyclic_gaps: list[int] = []
        if len(unique_idx) >= 2:
            for i, cur in enumerate(unique_idx):
                nxt = unique_idx[(i + 1) % len(unique_idx)]
                gap = (nxt - cur) % loop_len
                if gap == 0:
                    continue
                cyclic_gaps.append(gap)
            if cyclic_gaps:
                min_gap = min(cyclic_gaps)
        headway_gap1 = sum(1 for gap in cyclic_gaps if int(gap) == 1)
        headway_gap2 = sum(1 for gap in cyclic_gaps if int(gap) == 2)
        headway_gap3p = sum(1 for gap in cyclic_gaps if int(gap) >= 3)

        orbit_on_ring = [
            bid for bid in spacing_guard_bot_ids
            if start_by_bot.get(bid) in loop_index_by_cell
        ]
        slot_targets = {self._slot_by_bot[bid] for bid in spacing_guard_bot_ids if bid in self._slot_by_bot}
        orbit_slots_occupied = {
            loop_index_by_cell[start_by_bot[bid]]
            for bid in orbit_on_ring
        }
        formation_ready = bool(spacing_guard_bot_ids) and orbit_slots_occupied == slot_targets
        occupied_tokens = len(orbit_slots_occupied)
        vacant_tokens = max(0, int(len(slot_indices)) - int(occupied_tokens))
        reserved_rejoin_tokens = len(set(return_slot_by_bot.values()) | set(self._detour_rejoin_slot_by_bot.values()))
        orbit_floor_violations = max(0, int(orbit_target) - int(len(spacing_guard_bot_ids)))

        orbit_cw_moved_count = 0
        for bid in orbit_on_ring:
            start = start_by_bot.get(bid)
            tgt = final_target_by_bot.get(bid)
            if start is None or tgt is None:
                continue
            if start not in loop_index_by_cell or tgt not in loop_index_by_cell:
                continue
            cw_target = self._loop_points[(int(loop_index_by_cell[start]) + 1) % loop_len]
            if tgt == cw_target:
                orbit_cw_moved_count += 1

        phase_advanced_this_round = 0
        if spacing_guard_bot_ids:
            if len(orbit_on_ring) == len(spacing_guard_bot_ids):
                # Keep phase motion mostly continuous (throughput), but briefly
                # hold when ring compression is visible and clockwise progress is weak.
                compression_visible = min_gap <= 1 and orbit_cw_moved_count < max(1, int(len(orbit_on_ring) // 2))
                should_hold = compression_visible and self._phase_hold_ticks < 2
                if not should_hold:
                    self._advance_phase(loop_len=loop_len, steps=1)
                    self._phase_hold_ticks = 0
                    phase_advanced_this_round = 1
                else:
                    self._phase_hold_ticks += 1
            else:
                self._phase_hold_ticks += 1
                if self._phase_hold_ticks >= 3:
                    self._advance_phase(loop_len=loop_len, steps=1)
                    self._phase_hold_ticks = 0
                    phase_advanced_this_round = 1

        ring_ccw_violations = 0
        dropoff_occupancy = 0
        stop_line_occupancy = 0
        for bot in bots_sorted:
            bid = int(bot.id)
            start = start_by_bot[bid]
            tgt = final_target_by_bot.get(bid)
            end_pos = tgt if tgt is not None else start
            if end_pos == drop_off:
                dropoff_occupancy += 1
            if end_pos == topology.stop_line:
                stop_line_occupancy += 1
            if (
                tgt is not None
                and start in loop_index_by_cell
                and tgt in loop_index_by_cell
                and tgt != start
            ):
                cw = self._loop_points[(int(loop_index_by_cell[start]) + 1) % loop_len]
                if tgt != cw:
                    ring_ccw_violations += 1
        active_first_breach = 1 if preview_budget > 0 and active_remaining_total > 0 else 0
        queue_semantic_breach = int(max(0, dropoff_occupancy - 1) + max(0, stop_line_occupancy - 1))
        if self.debug and (ring_ccw_violations > 0 or queue_semantic_breach > 0 or active_first_breach > 0):
            print(
                "[orbit-wall][invariant]"
                f" round={int(state.round)}"
                f" ccw={ring_ccw_violations}"
                f" queue_breach={queue_semantic_breach}"
                f" active_first_breach={active_first_breach}"
            )

        drop_queue_len = max(0, len(self._delivery_mode) - 1)
        queue_ranks = [int(rank) for rank in queue_rank_by_bot.values()]
        avg_drop_wait = (sum(queue_ranks) / len(queue_ranks)) if queue_ranks else 0.0
        delivery_token_utilization = (
            float(len(self._delivery_mode)) / float(max(1, int(flow_delivery_cap)))
            if int(flow_delivery_cap) > 0
            else 0.0
        )

        return_to_orbit_eta_total = 0.0
        return_to_orbit_eta_count = 0
        for bot in returning_bots:
            bid = int(bot.id)
            start = start_by_bot.get(bid)
            if start is None:
                continue
            rslot = return_slot_by_bot.get(bid)
            if rslot is not None and 0 <= int(rslot) < len(self._loop_points):
                goal = self._loop_points[int(rslot)]
            else:
                goal = return_staging_target
            return_to_orbit_eta_total += float(abs(int(start[0]) - int(goal[0])) + abs(int(start[1]) - int(goal[1])))
            return_to_orbit_eta_count += 1
        return_to_orbit_eta = (
            return_to_orbit_eta_total / float(return_to_orbit_eta_count)
            if return_to_orbit_eta_count > 0
            else 0.0
        )

        remaining_active_need = Counter(active_need_working)
        unassigned_reachable_active_items = 0
        if remaining_active_need and orbit_bots:
            orbit_positions = [
                (int(bot.position[0]), int(bot.position[1]))
                for bot in orbit_bots
                if len(bot.inventory) < 3
            ]
            for item in state.items:
                item_id = str(item.id)
                if item_id in reserved_item_ids:
                    continue
                item_type = str(item.type)
                if remaining_active_need.get(item_type, 0) <= 0:
                    continue
                item_pos = (int(item.position[0]), int(item.position[1]))
                reachable = any(
                    abs(int(pos[0]) - int(item_pos[0])) + abs(int(pos[1]) - int(item_pos[1])) <= 6
                    for pos in orbit_positions
                )
                if not reachable:
                    continue
                remaining_active_need[item_type] -= 1
                unassigned_reachable_active_items += 1

        unassigned_due_to_orbit_floor = int(max(0, self._last_unassigned_due_to_orbit_floor))
        unassigned_due_to_reservation_conflict = int(max(0, assignment_reservation_conflicts))
        reservation_conflict_count = int(max(0, assignment_reservation_conflicts + blocked_moves + swaps_prevented))
        transition_event = 1 if int(state.round) == int(self._last_transition_round) else 0
        active_order_now = get_active_order(state)
        auto_delivered_on_transition = (
            int(len(active_order_now.items_delivered))
            if transition_event and active_order_now is not None
            else 0
        )

        flow_mode_code = 1.0
        if flow_mode == FLOW_BOOT_RELEASE:
            flow_mode_code = 0.0
        elif flow_mode == FLOW_FINISH_WAVE:
            flow_mode_code = 2.0

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
        wait_spacing = sum(
            1
            for reason in self._round_wait_reason_by_bot.values()
            if reason == "wait_due_to_spacing_guard"
        )

        self.last_collisions_avoided = blocked_moves
        self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
        quota_reason_names = {
            0: "none",
            1: "no_active_cargo",
            2: "active_cargo_present",
            3: "boot_release_carry_guard",
            4: "endgame_escalation",
            5: "finish_wave_boost",
        }
        self.last_round_debug = {
            "delivery_quota_reason": str(quota_reason_names.get(int(self._last_delivery_quota_reason_code), "unknown")),
            "active_stall_rounds": int(self._active_stall_rounds),
            "headway_histogram": {
                "gap1": int(headway_gap1),
                "gap2": int(headway_gap2),
                "gap3p": int(headway_gap3p),
            },
            "mission_type_by_bot": {str(bid): str(mtype) for bid, mtype in next_prev_mission_type_by_bot.items()},
            "mission_value_by_bot": {str(bid): float(val) for bid, val in mission_value_by_bot.items()},
            "A_need_by_type": {str(k): int(v) for k, v in ledger.active_need.items() if int(v) > 0},
            "A_reserved_by_type": {str(k): int(v) for k, v in ledger.active_reserved.items() if int(v) > 0},
            "A_committed_by_type": {str(k): int(v) for k, v in ledger.active_committed.items() if int(v) > 0},
            "P_need_by_type": {str(k): int(v) for k, v in ledger.preview_need.items() if int(v) > 0},
            "P_contingent_by_type": {str(k): int(v) for k, v in ledger.preview_contingent.items() if int(v) > 0},
            "occupied_tokens": int(occupied_tokens),
            "vacant_tokens": int(vacant_tokens),
            "reserved_rejoin_tokens": int(reserved_rejoin_tokens),
            "gate_admission_denied": int(gate_admission_denied),
            "rejoin_queue_len": int(len(returning_bots)),
            "orbit_floor_violations": int(orbit_floor_violations),
            "drop_queue_len": int(drop_queue_len),
            "transition_event": int(transition_event),
            "auto_delivered_on_transition": int(auto_delivered_on_transition),
            "ring_direction_violation": int(ring_ccw_violations),
            "illegal_reentry_attempt": int(illegal_reentry_attempts),
            "queue_semantics_violation": int(queue_semantic_breach),
            "preview_stole_active_capacity": int(active_first_breach),
            "reservation_conflict_count": int(reservation_conflict_count),
            "unassigned_reachable_active_items": int(unassigned_reachable_active_items),
            "unassigned_due_to_orbit_floor": int(unassigned_due_to_orbit_floor),
            "unassigned_due_to_reservation_conflict": int(unassigned_due_to_reservation_conflict),
            "branch_exit": [int(branch_exit[0]), int(branch_exit[1])],
            "branch_continue": [int(branch_continue[0]), int(branch_continue[1])],
            "delivery_entry": [int(delivery_entry[0]), int(delivery_entry[1])],
            "rejoin_branch": [int(rejoin_branch[0]), int(rejoin_branch[1])],
            "branch_exit_visits": int(branch_exit_visits),
            "branch_to_delivery": int(branch_to_delivery),
            "branch_continue_moves": int(branch_continue_moves),
            "branch_waits": int(branch_waits),
            "rejoin_branch_visits": int(rejoin_branch_visits),
            "rejoin_admissions": int(rejoin_admissions),
            "rejoin_denials": int(rejoin_denials),
            "orbit_cw_moved": int(orbit_cw_moved_count),
            "phase_advanced": int(phase_advanced_this_round),
        }
        self.last_round_telemetry = {
            "blocked_moves": float(blocked_moves),
            "swaps_prevented": float(swaps_prevented),
            "collisions_avoided": float(blocked_moves),
            "orbit_loop_size": float(loop_len),
            "orbit_spacing_target": float(spacing),
            "orbit_phase": float(self._orbit_phase),
            "orbit_formation_ready": 1.0 if formation_ready else 0.0,
            "orbit_cw_moved": float(orbit_cw_moved_count),
            "phase_advanced": float(phase_advanced_this_round),
            "flow_mode": float(flow_mode_code),
            "flow_orbit_target": float(orbit_target),
            "flow_deliver_target": float(flow_delivery_cap),
            "flow_delivery_tokens": float(delivery_cap),
            "flow_preview_budget": float(preview_budget),
            "flow_detour_limit": float(detour_limit),
            "migration_stage": float(self._migration_stage),
            "preview_stage5_enabled": 1.0 if self._enable_preview_stage5 else 0.0,
            "orbit_target": float(orbit_target),
            "deliver_target": float(flow_delivery_cap),
            "preview_budget": float(preview_budget),
            "active_stall_rounds": float(self._active_stall_rounds),
            "active_safety_stock": float(ledger.slot_safety_stock),
            "delivery_quota_reason_code": float(self._last_delivery_quota_reason_code),
            "delivery_quota_reason": float(self._last_delivery_quota_reason_code),
            "occupied_tokens": float(occupied_tokens),
            "vacant_tokens": float(vacant_tokens),
            "reserved_rejoin_tokens": float(reserved_rejoin_tokens),
            "headway_gap_1": float(headway_gap1),
            "headway_gap_2": float(headway_gap2),
            "headway_gap_3p": float(headway_gap3p),
            "gate_admission_denied": float(gate_admission_denied),
            "rejoin_queue_len": float(len(returning_bots)),
            "orbit_floor_violations": float(orbit_floor_violations),
            "active_need_total": float(active_need_total),
            "active_reserved_total": float(active_reserved_total),
            "active_committed_total": float(active_committed_total),
            "A_need": float(active_need_total),
            "A_reserved": float(active_reserved_total),
            "A_committed": float(active_committed_total),
            "active_deficit_total": float(sum(ledger.active_deficit.values())),
            "active_serviceable_total": float(active_remaining_total),
            "preview_need_total": float(preview_need_total),
            "preview_deficit_total": float(preview_need_total),
            "preview_reserved_total": float(preview_reserved_total),
            "preview_contingent_total": float(preview_contingent_total),
            "P_need": float(preview_need_total),
            "P_contingent": float(preview_contingent_total),
            "active_carried_total": float(sum(ledger.active_carried.values())),
            "preview_carried_total": float(sum(ledger.preview_carried.values())),
            "slot_safety_stock": float(ledger.slot_safety_stock),
            "preview_designated_bots": float(len(preview_designated_bots)),
            "active_to_preview_transition_recent": float(transition_recent),
            "transition_event": float(transition_event),
            "auto_delivered_on_transition": float(auto_delivered_on_transition),
            "orbit_pick_active": float(pick_active_count),
            "orbit_pick_preview": float(pick_preview_count),
            "orbit_detour_active": float(detour_active_assigned),
            "orbit_detour_preview": float(detour_preview_assigned),
            "branch_exit_x": float(branch_exit[0]),
            "branch_exit_y": float(branch_exit[1]),
            "branch_continue_x": float(branch_continue[0]),
            "branch_continue_y": float(branch_continue[1]),
            "delivery_entry_x": float(delivery_entry[0]),
            "delivery_entry_y": float(delivery_entry[1]),
            "rejoin_branch_x": float(rejoin_branch[0]),
            "rejoin_branch_y": float(rejoin_branch[1]),
            "branch_exit_visits": float(branch_exit_visits),
            "branch_to_delivery": float(branch_to_delivery),
            "branch_continue_moves": float(branch_continue_moves),
            "branch_waits": float(branch_waits),
            "rejoin_branch_visits": float(rejoin_branch_visits),
            "rejoin_admissions": float(rejoin_admissions),
            "rejoin_denials": float(rejoin_denials),
            "mission_orbit_count": float(mission_type_orbit),
            "mission_pickup_detour_count": float(mission_type_pickup_detour),
            "mission_pick_item_count": float(mission_type_pick_item),
            "mission_deliver_count": float(mission_type_deliver),
            "mission_queue_count": float(mission_type_queue),
            "mission_rejoin_count": float(mission_type_rejoin),
            "mission_value_avg": float(sum(mission_value_by_bot.values()) / max(1, len(mission_value_by_bot))),
            "assignment_churn_by_bot": float(assignment_churn_count),
            "unassigned_reachable_active_items": float(unassigned_reachable_active_items),
            "unassigned_due_to_orbit_floor": float(unassigned_due_to_orbit_floor),
            "unassigned_due_to_reservation_conflict": float(unassigned_due_to_reservation_conflict),
            "orbit_min_gap": float(min_gap),
            "orbit_bots": float(len(orbit_bot_ids)),
            "orbit_tokens": float(len(slot_indices)),
            "deliver_bots": float(len(self._delivery_mode)),
            "queue_depth": float(max(0, len(self._delivery_mode) - 1)),
            "rejoin_backlog": float(len(self._return_mode)),
            "ready_active_cargo_units": float(active_carried_total),
            "committed_active_cargo_units": float(active_committed_total),
            "drop_queue_len": float(drop_queue_len),
            "avg_drop_wait": float(avg_drop_wait),
            "delivery_token_utilization": float(delivery_token_utilization),
            "return_to_orbit_eta": float(return_to_orbit_eta),
            "inv_ring_ccw_violations": float(ring_ccw_violations),
            "inv_dropoff_over_occupancy": float(max(0, dropoff_occupancy - 1)),
            "inv_stop_line_over_occupancy": float(max(0, stop_line_occupancy - 1)),
            "inv_queue_semantic_breach": float(queue_semantic_breach),
            "inv_active_first_capacity_breach": float(active_first_breach),
            "ring_direction_violation": float(ring_ccw_violations),
            "illegal_reentry_attempt": float(illegal_reentry_attempts),
            "queue_semantics_violation": float(queue_semantic_breach),
            "preview_stole_active_capacity": float(active_first_breach),
            "reservation_conflict_count": float(reservation_conflict_count),
            "wait_due_to_no_assignment": float(wait_no_assignment),
            "wait_due_to_collision_block": float(wait_collision),
            "wait_due_to_spacing_guard": float(wait_spacing),
        }
        return RoundActions(actions=final_actions)


class ConveyorPlannerV2(WallOrbitEngine):
    """Ring conveyor planner: keep ring as movement prior, not as slot/phase/token controller."""

    def _advance_phase(self, *, loop_len: int, steps: int = 1) -> None:
        # Conveyor v2 does not use global phase advancement as a control primitive.
        return

    def _ensure_orbit_tokens(self, *, target_count: int, loop_len: int) -> list[int]:
        if loop_len <= 0:
            self._orbit_token_indices = []
            return []
        # Keep telemetry compatibility but expose the whole ring as prior lattice.
        self._orbit_token_indices = [idx for idx in range(loop_len)]
        return list(self._orbit_token_indices)

    def _reassign_slots(
        self,
        *,
        bots_orbit: list[Any],
        slot_indices: list[int],
        loop_index_by_cell: dict[tuple[int, int], int],
    ) -> None:
        if not self._loop_points or not bots_orbit:
            self._slot_by_bot = {}
            return
        loop_len = len(self._loop_points)
        assigned: dict[int, int] = {}

        # On-ring bots follow immediate clockwise flow; this keeps ring as transport prior.
        for bot in sorted(bots_orbit, key=lambda row: int(row.id)):
            bid = int(bot.id)
            bpos = (int(bot.position[0]), int(bot.position[1]))
            cur_idx = loop_index_by_cell.get(bpos)
            if cur_idx is None:
                continue
            assigned[bid] = (int(cur_idx) + 1) % loop_len

        for bot in sorted(bots_orbit, key=lambda row: int(row.id)):
            bid = int(bot.id)
            if bid in assigned:
                continue
            bpos = (int(bot.position[0]), int(bot.position[1]))
            choice = min(
                range(loop_len),
                key=lambda s: (
                    abs(int(bpos[0]) - int(self._loop_points[int(s)][0]))
                    + abs(int(bpos[1]) - int(self._loop_points[int(s)][1])),
                    int(s),
                ),
            )
            assigned[bid] = int(choice)

        self._slot_by_bot = dict(assigned)

    def _assign_return_slots(
        self,
        *,
        returning_bots: list[Any],
        slot_indices: list[int],
        occupied_ring_indices: set[int],
        loop_index_by_cell: dict[tuple[int, int], int],
        start_by_bot: dict[int, tuple[int, int]],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> dict[int, int]:
        if not returning_bots or not self._loop_points:
            self._return_slot_by_bot = {}
            return {}

        loop_len = len(self._loop_points)
        rejoin_branch = (int(self._rejoin_branch[0]), int(self._rejoin_branch[1]))
        admission_cell = (int(rejoin_branch[0]) + 1, int(rejoin_branch[1]))
        admission_idx = loop_index_by_cell.get(admission_cell)
        if admission_idx is None:
            admission_idx = min(
                range(loop_len),
                key=lambda idx: (
                    abs(int(self._loop_points[int(idx)][0]) - int(rejoin_branch[0]))
                    + abs(int(self._loop_points[int(idx)][1]) - int(rejoin_branch[1])),
                    int(idx),
                ),
            )
        admission_idx = int(admission_idx)
        if admission_idx in occupied_ring_indices:
            self._return_slot_by_bot = {}
            return {}

        ranked: list[tuple[int, int]] = []
        for bot in returning_bots:
            bid = int(bot.id)
            start = start_by_bot.get(bid)
            if start is None:
                continue
            dist = abs(int(start[0]) - int(rejoin_branch[0])) + abs(int(start[1]) - int(rejoin_branch[1]))
            ranked.append((int(dist), int(bid)))
        if not ranked:
            self._return_slot_by_bot = {}
            return {}

        ranked.sort(key=lambda row: (int(row[0]), int(row[1])))
        chosen_bid = int(ranked[0][1])
        assigned = {chosen_bid: admission_idx}
        self._return_slot_by_bot = dict(assigned)
        return assigned


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
        "--orbit-planner",
        type=str,
        default="conveyor_v2",
        choices=["conveyor_v2", "wall_v1"],
        help="Select orbit-wall planner implementation",
    )
    parser.add_argument(
        "--orbit-flow-hypothesis",
        action="store_true",
        help="Experimental hypothesis from answerAI.md: replacement orbit/pickup/delivery coordinator",
    )
    parser.add_argument(
        "--orbit-migration-stage",
        type=int,
        default=5,
        help="Migration stage for WallOrbitEngine (0..6). Stage >=5 enables preview budget path.",
    )
    parser.add_argument(
        "--orbit-shelf-ids",
        type=str,
        default="72,73,112,113",
        help="Comma-separated shelf IDs (top-left, top-right, bottom-left, bottom-right) used to derive orbit wall",
    )
    parser.add_argument(
        "--orbit-branch-exit",
        type=str,
        default="8,15",
        help="Fixed orbit exit branch cell x,y for delivery decision",
    )
    parser.add_argument(
        "--orbit-branch-continue",
        type=str,
        default="7,15",
        help="Clockwise continue cell x,y from exit branch when staying on orbit",
    )
    parser.add_argument(
        "--orbit-delivery-entry",
        type=str,
        default="8,16",
        help="Delivery corridor entry cell x,y from orbit branch",
    )
    parser.add_argument(
        "--orbit-rejoin-branch",
        type=str,
        default="3,15",
        help="Branch cell x,y for controlled rejoin into orbit",
    )
    parser.add_argument(
        "--orbit-dropoff",
        type=str,
        default="",
        help="Optional drop-off override x,y for orbit-wall (empty uses server state)",
    )
    parser.add_argument(
        "--orbit-return-buffer-cells",
        type=str,
        default="3,16;2,16",
        help="Semicolon-separated return buffer cells x,y;x,y used before rejoin",
    )
    parser.add_argument(
        "--orbit-delivery-quota-min",
        type=int,
        default=1,
        help="Minimum delivery admission quota in orbit-wall",
    )
    parser.add_argument(
        "--orbit-delivery-quota-max",
        type=int,
        default=3,
        help="Maximum delivery admission quota in orbit-wall",
    )
    parser.add_argument(
        "--orbit-forecast-buffer-cap",
        type=int,
        default=2,
        help="Max buffered forecast picks for non-active/non-preview horizon",
    )
    parser.add_argument(
        "--orbit-rejoin-slot-headroom",
        type=int,
        default=2,
        help="Extra orbit slot headroom reserved for return-to-ring admissions",
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
        "--demand-commitment-mode",
        type=str,
        default="auto",
        choices=["auto", "optimistic", "committed", "delivered_only"],
        help="Demand accounting mode: auto=committed on expert DecisionEngine, optimistic otherwise",
    )
    parser.add_argument(
        "--demand-commit-radius",
        type=int,
        default=2,
        help="Drop-off Manhattan radius used by committed demand accounting",
    )
    parser.add_argument(
        "--demand-preview-safety-slots",
        type=int,
        default=1,
        help="Inventory slots reserved for active demand before preview prefetch in committed mode",
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

    demand_mode = str(getattr(args, "demand_commitment_mode", "auto")).strip().lower()
    if demand_mode == "auto":
        if str(args.difficulty).strip().lower() == "expert" and not (args.orbit_wall or args.orbit_flow_hypothesis):
            demand_mode = "committed"
        else:
            demand_mode = "optimistic"

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
        demand_commitment_mode=demand_mode,
        demand_commit_radius=max(0, int(args.demand_commit_radius)),
        demand_preview_safety_slots=max(0, int(args.demand_preview_safety_slots)),
    )
    raw_params = getattr(args, "_raw_params_file", {})
    if isinstance(raw_params, dict) and raw_params:
        merged = cfg.to_dict()
        for key, value in raw_params.items():
            if key in merged:
                merged[key] = value
        cfg = DecisionConfig(**merged)
    order_forecast = _load_order_forecast(args, session=session)
    if args.orbit_flow_hypothesis:
        engine = OrbitFlowEngine(
            debug=args.debug,
            reservation_horizon=max(1, int(args.reservation_horizon)),
            shelf_ids=getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS),
        )
    elif args.orbit_wall:
        orbit_engine_cls = ConveyorPlannerV2 if str(getattr(args, "orbit_planner", "conveyor_v2")) == "conveyor_v2" else WallOrbitEngine
        engine = orbit_engine_cls(
            debug=args.debug,
            reservation_horizon=max(1, int(args.reservation_horizon)),
            shelf_ids=getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS),
            migration_stage=max(0, int(getattr(args, "orbit_migration_stage", 5))),
            branch_exit=getattr(args, "_orbit_branch_exit", ORBIT_FIXED_BRANCH_EXIT),
            branch_continue=getattr(args, "_orbit_branch_continue", ORBIT_FIXED_BRANCH_CONTINUE),
            delivery_entry=getattr(args, "_orbit_delivery_entry", ORBIT_FIXED_DELIVERY_ENTRY),
            rejoin_branch=getattr(args, "_orbit_rejoin_branch", ORBIT_FIXED_REJOIN_BRANCH),
            dropoff_override=getattr(args, "_orbit_dropoff", None),
            return_buffer_cells=getattr(args, "_orbit_return_buffer_cells", ORBIT_FIXED_RETURN_BUFFER),
            delivery_quota_min=max(0, int(getattr(args, "orbit_delivery_quota_min", 1))),
            delivery_quota_max=max(0, int(getattr(args, "orbit_delivery_quota_max", 3))),
            forecast_buffer_cap=max(0, int(getattr(args, "orbit_forecast_buffer_cap", 2))),
            rejoin_slot_headroom=max(0, int(getattr(args, "orbit_rejoin_slot_headroom", 2))),
            order_forecast=order_forecast,
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
                "round_debug": dict(getattr(engine, "last_round_debug", {}) or {}),
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

    if args.orbit_flow_hypothesis:
        engine_mode = "orbit_flow_hypothesis"
    elif args.orbit_wall:
        planner_name = str(getattr(args, "orbit_planner", "conveyor_v2"))
        engine_mode = f"orbit_wall_{planner_name}"
    else:
        engine_mode = "decision_engine"

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
                "orbit_flow_hypothesis": bool(args.orbit_flow_hypothesis),
                "orbit_wall": bool(args.orbit_wall),
                "orbit_planner": str(getattr(args, "orbit_planner", "conveyor_v2")),
                "engine_mode": engine_mode,
                "orbit_shelf_ids": list(getattr(args, "_orbit_shelf_ids", ORBIT_DEFAULT_SHELF_IDS)),
                "orbit_branch_exit": list(getattr(args, "_orbit_branch_exit", ORBIT_FIXED_BRANCH_EXIT)),
                "orbit_branch_continue": list(getattr(args, "_orbit_branch_continue", ORBIT_FIXED_BRANCH_CONTINUE)),
                "orbit_delivery_entry": list(getattr(args, "_orbit_delivery_entry", ORBIT_FIXED_DELIVERY_ENTRY)),
                "orbit_rejoin_branch": list(getattr(args, "_orbit_rejoin_branch", ORBIT_FIXED_REJOIN_BRANCH)),
                "orbit_dropoff": list(getattr(args, "_orbit_dropoff", ORBIT_FIXED_DROPOFF))
                if getattr(args, "_orbit_dropoff", None) is not None
                else None,
                "orbit_return_buffer_cells": [
                    [int(cell[0]), int(cell[1])]
                    for cell in getattr(args, "_orbit_return_buffer_cells", ORBIT_FIXED_RETURN_BUFFER)
                ],
                "orbit_delivery_quota_min": int(getattr(args, "orbit_delivery_quota_min", 1)),
                "orbit_delivery_quota_max": int(getattr(args, "orbit_delivery_quota_max", 3)),
                "orbit_forecast_buffer_cap": int(getattr(args, "orbit_forecast_buffer_cap", 2)),
                "orbit_rejoin_slot_headroom": int(getattr(args, "orbit_rejoin_slot_headroom", 2)),
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
                    f"orbit_flow_hypothesis={args.orbit_flow_hypothesis}",
                    f"orbit_wall={args.orbit_wall}",
                    f"orbit_planner={getattr(args, 'orbit_planner', 'conveyor_v2')}",
                    f"engine_mode={engine_mode}",
                    f"orbit_shelf_ids={getattr(args, '_orbit_shelf_ids', ORBIT_DEFAULT_SHELF_IDS)}",
                    f"orbit_branch_exit={getattr(args, '_orbit_branch_exit', ORBIT_FIXED_BRANCH_EXIT)}",
                    f"orbit_branch_continue={getattr(args, '_orbit_branch_continue', ORBIT_FIXED_BRANCH_CONTINUE)}",
                    f"orbit_delivery_entry={getattr(args, '_orbit_delivery_entry', ORBIT_FIXED_DELIVERY_ENTRY)}",
                    f"orbit_rejoin_branch={getattr(args, '_orbit_rejoin_branch', ORBIT_FIXED_REJOIN_BRANCH)}",
                    f"orbit_dropoff={getattr(args, '_orbit_dropoff', None)}",
                    f"orbit_return_buffer_cells={getattr(args, '_orbit_return_buffer_cells', ORBIT_FIXED_RETURN_BUFFER)}",
                    f"orbit_delivery_quota_min={int(getattr(args, 'orbit_delivery_quota_min', 1))}",
                    f"orbit_delivery_quota_max={int(getattr(args, 'orbit_delivery_quota_max', 3))}",
                    f"orbit_forecast_buffer_cap={int(getattr(args, 'orbit_forecast_buffer_cap', 2))}",
                    f"orbit_rejoin_slot_headroom={int(getattr(args, 'orbit_rejoin_slot_headroom', 2))}",
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
    if args.orbit_wall and args.orbit_flow_hypothesis:
        raise SystemExit("Choose only one of --orbit-wall or --orbit-flow-hypothesis")
    if str(args.difficulty).strip().lower() == "expert" and (args.orbit_wall or args.orbit_flow_hypothesis):
        print("[info] expert mode with orbit engine is diagnostic/experimental; DecisionEngine is the primary expert path.")
    if args.orbit_wall or args.orbit_flow_hypothesis:
        args._orbit_shelf_ids = _parse_orbit_shelf_ids(args.orbit_shelf_ids)
    else:
        args._orbit_shelf_ids = ORBIT_DEFAULT_SHELF_IDS
    if args.orbit_wall:
        args._orbit_branch_exit = _parse_xy(args.orbit_branch_exit, arg_name="--orbit-branch-exit")
        args._orbit_branch_continue = _parse_xy(args.orbit_branch_continue, arg_name="--orbit-branch-continue")
        args._orbit_delivery_entry = _parse_xy(args.orbit_delivery_entry, arg_name="--orbit-delivery-entry")
        args._orbit_rejoin_branch = _parse_xy(args.orbit_rejoin_branch, arg_name="--orbit-rejoin-branch")
        orbit_dropoff_raw = str(getattr(args, "orbit_dropoff", "") or "").strip()
        args._orbit_dropoff = _parse_xy(orbit_dropoff_raw, arg_name="--orbit-dropoff") if orbit_dropoff_raw else None
        return_buffer_raw = str(getattr(args, "orbit_return_buffer_cells", "") or "").strip()
        parsed_return_buffer = _parse_xy_list(return_buffer_raw, arg_name="--orbit-return-buffer-cells")
        args._orbit_return_buffer_cells = parsed_return_buffer or ORBIT_FIXED_RETURN_BUFFER
    else:
        args._orbit_branch_exit = ORBIT_FIXED_BRANCH_EXIT
        args._orbit_branch_continue = ORBIT_FIXED_BRANCH_CONTINUE
        args._orbit_delivery_entry = ORBIT_FIXED_DELIVERY_ENTRY
        args._orbit_rejoin_branch = ORBIT_FIXED_REJOIN_BRANCH
        args._orbit_dropoff = None
        args._orbit_return_buffer_cells = ORBIT_FIXED_RETURN_BUFFER
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


















