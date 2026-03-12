from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, fields
import json
import math
from pathlib import Path
import time
from typing import Any

from .collision import action_for_move, resolve_collisions_with_stats
from .cooperative_path import plan_windowed_next_steps
from .decision_engine import DecisionConfig, DecisionEngine
from .grid import Grid
from .models import BotAction, BotActionCommand, GameState, RoundActions
from .orders import get_active_order, get_preview_order
from .pathfinding import bfs_distance_map, bfs_shortest_path, find_all_pickup_positions

INF = 999_999
WAIT_NO_ASSIGN = "wait_due_to_no_assignment"
WAIT_NO_TARGET = "wait_due_to_no_target"
WAIT_COLLISION = "wait_due_to_collision_block"


@dataclass(frozen=True)
class ExperimentalDispatchConfig:
    top_k_candidates_per_bot: int = 10
    eta_weight: float = 4.0
    drop_eta_weight: float = 0.7
    preview_weight_open: float = 0.0
    preview_weight_secured: float = 0.35
    forecast_weight: float = 0.2
    forecast_depth: int = 2
    reservation_horizon: int = 2
    path_window: int = 8
    tail_remaining_threshold: int = 3
    tail_distinct_threshold: int = 2
    converter_floor_max: int = 3
    converter_local_harvest_radius: int = 6
    reliable_drop_dist_max: int = 6
    reliable_wait_streak_max: int = 3
    harvest_commit_drop_dist_max: int = 12
    harvest_commit_wait_streak_max: int = 8
    secured_progress_window: int = 8
    secured_progress_min_points: int = 1
    critical_dispatch_slots_default: int = 1
    critical_dispatch_slots_tail: int = 2
    critical_dispatch_type_limit: int = 2
    min_active_retrievers_open: int = 6
    min_active_retrievers_tail: int = 4
    rescue_fallback_enabled: bool = True
    rescue_round_threshold: int = 70
    rescue_score_threshold: int = 8
    rescue_stagnation_window: int = 12
    rescue_params_file: str = "bundle_a_critical_dispatch_overlay_v2_iter11.json"
    rescue_secondary_params_file: str = "bundle_a_critical_dispatch_overlay_v2_iter11.json"
    rescue_profile_switch_round: int = 40
    rescue_profile_switch_score_min: int = 8

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any] | None = None) -> "ExperimentalDispatchConfig":
        src = dict(overrides or {})
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in src.items() if k in allowed})


@dataclass(frozen=True)
class _Task:
    key: str
    kind: str
    goal: tuple[int, int]
    score: float
    source: str
    critical: bool
    item_id: str | None = None
    item_type: str | None = None
    exclusive_item_id: str | None = None


def _load_bundle_a_rescue_config(preset_ref: str | None) -> DecisionConfig:
    default_cfg = DecisionConfig(
        strict_active_priority=True,
        transition_stash_enabled=True,
        prefetch_release_use_delivered_completion=True,
        demand_commitment_mode="committed",
        anti_no_assignment_enabled=True,
        secondary_assignment_enabled=True,
        startup_release_v3_enabled=True,
        always_deliver_matching=True,
        assignment_strategy="greedy",
        whca_enabled=False,
    )
    try:
        root = Path(__file__).resolve().parents[1]
        raw_ref = str(preset_ref or "").strip()
        if raw_ref:
            candidate = Path(raw_ref)
            preset_path = candidate if candidate.is_absolute() else (root / "configs" / "expert_coordination_presets" / raw_ref)
        else:
            preset_path = root / "configs" / "expert_coordination_presets" / "bundle_a_critical_dispatch_overlay_v2_iter11.json"
        if not preset_path.exists():
            return default_cfg
        payload = json.loads(preset_path.read_text(encoding="utf-8"))
        raw = dict(payload) if isinstance(payload, dict) else {}
        if isinstance(raw.get("params"), dict):
            raw = dict(raw["params"])
        elif isinstance(raw.get("config"), dict):
            raw = dict(raw["config"])
        merged = default_cfg.to_dict()
        for key, value in raw.items():
            if key in merged:
                merged[key] = value
        return DecisionConfig(**merged)
    except Exception:
        return default_cfg


class ExperimentalDispatchEngine:
    def __init__(
        self,
        *,
        config: ExperimentalDispatchConfig | None = None,
        debug: bool = False,
        reservation_horizon: int = 2,
        order_forecast: dict[int, list[str]] | None = None,
    ) -> None:
        self.config = config or ExperimentalDispatchConfig(reservation_horizon=max(1, int(reservation_horizon)))
        self.debug = bool(debug)
        self._order_forecast = {int(k): [str(x) for x in v] for k, v in (order_forecast or {}).items() if isinstance(v, list)}

        self.last_decision_ms = 0.0
        self.last_collisions_avoided = 0
        self.last_swaps_prevented = 0
        self.last_replans = 0
        self.last_fallback_used = False
        self.last_round_telemetry: dict[str, float] = {}
        self.last_round_debug: dict[str, Any] = {}
        self.last_assignment_snapshot: dict[int, dict[str, object]] = {}
        self.last_pre_collision_actions: dict[int, dict[str, object]] = {}
        self.last_whca_used = False
        self.last_whca_ms = 0.0
        self._round_wait_reason_by_bot: dict[int, str] = {}

        self._known_supply_by_type: dict[str, set[tuple[int, int]]] = {}
        self._known_type_by_shelf: dict[tuple[int, int], str] = {}
        self._last_target_key_by_bot: dict[int, str] = {}
        self._wait_streak_by_bot: dict[int, int] = {}
        self._progress_window: deque[int] = deque(maxlen=16)
        self._last_active_id: str | None = None
        self._last_active_delivered = 0
        self._overlay_active_order_id: str | None = None
        self._overlay_active_order_start_round = 0

        rescue_cfg_base = _load_bundle_a_rescue_config(self.config.rescue_params_file)
        rescue_dict = rescue_cfg_base.to_dict()
        rescue_dict["reservation_horizon"] = max(1, int(self.config.reservation_horizon))
        rescue_dict["assignment_strategy"] = "greedy"
        rescue_dict["whca_enabled"] = False
        rescue_cfg = DecisionConfig(**rescue_dict)
        self._rescue_engine = DecisionEngine(
            config=rescue_cfg,
            debug=False,
            verbose=False,
            order_forecast=order_forecast,
            capture_debug=False,
        )
        self._rescue_engine_secondary: DecisionEngine | None = None
        secondary_ref = str(self.config.rescue_secondary_params_file or "").strip()
        primary_ref = str(self.config.rescue_params_file or "").strip()
        if secondary_ref and secondary_ref != primary_ref:
            secondary_cfg_base = _load_bundle_a_rescue_config(secondary_ref)
            secondary_dict = secondary_cfg_base.to_dict()
            secondary_dict["reservation_horizon"] = max(1, int(self.config.reservation_horizon))
            secondary_dict["assignment_strategy"] = "greedy"
            secondary_dict["whca_enabled"] = False
            secondary_cfg = DecisionConfig(**secondary_dict)
            self._rescue_engine_secondary = DecisionEngine(
                config=secondary_cfg,
                debug=False,
                verbose=False,
                order_forecast=order_forecast,
                capture_debug=False,
            )
        self._rescue_secondary_active = False
        self._rescue_active = False

    @staticmethod
    def _rem(order) -> Counter[str]:
        if order is None:
            return Counter()
        need = Counter(str(t) for t in order.items_required)
        for t in order.items_delivered:
            s = str(t)
            if need.get(s, 0) > 0:
                need[s] -= 1
        return Counter({k: int(v) for k, v in need.items() if int(v) > 0})

    @staticmethod
    def _sub(a: Counter[str], b: Counter[str]) -> Counter[str]:
        out: Counter[str] = Counter()
        for k in set(a) | set(b):
            v = int(a.get(k, 0)) - int(b.get(k, 0))
            if v > 0:
                out[k] = v
        return out

    @staticmethod
    def _bot_pos(bot) -> tuple[int, int]:
        return (int(bot.position[0]), int(bot.position[1]))

    def _update_supply(self, state: GameState) -> None:
        if self._known_supply_by_type:
            return
        by_type: dict[str, set[tuple[int, int]]] = {}
        by_shelf: dict[tuple[int, int], str] = {}
        for item in state.items:
            p = (int(item.position[0]), int(item.position[1]))
            t = str(item.type)
            by_type.setdefault(t, set()).add(p)
            by_shelf[p] = t
        self._known_supply_by_type = by_type
        self._known_type_by_shelf = by_shelf

    def _update_progress(self, active) -> None:
        if active is None:
            self._progress_window.append(0)
            self._last_active_id = None
            self._last_active_delivered = 0
            return
        oid = str(active.id)
        delivered = int(len(active.items_delivered))
        if oid != self._last_active_id:
            self._last_active_id = oid
            self._last_active_delivered = delivered
            self._progress_window.append(0)
            return
        delta = max(0, delivered - self._last_active_delivered)
        self._last_active_delivered = delivered
        self._progress_window.append(delta)

    def _forecast(self, active_idx: int) -> Counter[str]:
        out: Counter[str] = Counter()
        for d in range(1, max(0, int(self.config.forecast_depth)) + 1):
            seq = self._order_forecast.get(int(active_idx) + d)
            if not seq:
                continue
            for t in seq:
                out[str(t)] += 1
        return out

    def _apply_critical_dispatch_overlay(self, state: GameState, actions: RoundActions) -> tuple[RoundActions, dict[str, float]]:
        active = get_active_order(state)
        if active is None:
            return actions, {"critical_overlay_active": 0.0, "critical_overlay_overrides": 0.0}
        active_need = self._rem(active)
        if not active_need:
            return actions, {"critical_overlay_active": 0.0, "critical_overlay_overrides": 0.0}

        oid = str(active.id)
        if oid != self._overlay_active_order_id:
            self._overlay_active_order_id = oid
            self._overlay_active_order_start_round = int(state.round)
        active_age = max(0, int(state.round) - int(self._overlay_active_order_start_round))

        remaining_total = int(sum(active_need.values()))
        remaining_distinct = int(sum(1 for v in active_need.values() if int(v) > 0))
        if remaining_total > 2 or remaining_distinct > 2 or active_age < 12:
            return actions, {"critical_overlay_active": 0.0, "critical_overlay_overrides": 0.0}

        self._update_supply(state)
        grid = Grid(state.grid)
        drop = (int(state.drop_off[0]), int(state.drop_off[1]))
        drop_map = bfs_distance_map(grid, drop)
        actions_by_bot = {int(a.bot): a for a in actions.actions}
        bots = sorted(state.bots, key=lambda b: int(b.id))
        bots_by_id = {int(b.id): b for b in bots}

        wait_candidates: list[int] = []
        for b in bots:
            bid = int(b.id)
            cmd = actions_by_bot.get(bid)
            if cmd is None or cmd.action != BotAction.WAIT:
                continue
            inv = [str(t) for t in b.inventory]
            if len(inv) >= 3:
                continue
            if any(active_need.get(t, 0) > 0 for t in inv):
                continue
            wait_candidates.append(bid)
        if not wait_candidates:
            return actions, {"critical_overlay_active": 1.0, "critical_overlay_overrides": 0.0}

        visible_items_by_shelf_type: dict[tuple[tuple[int, int], str], list[str]] = {}
        for item in state.items:
            shelf = (int(item.position[0]), int(item.position[1]))
            t = str(item.type)
            visible_items_by_shelf_type.setdefault((shelf, t), []).append(str(item.id))

        dist_maps = {bid: bfs_distance_map(grid, self._bot_pos(bots_by_id[bid])) for bid in wait_candidates}
        missing_types = [t for t, c in active_need.items() if int(c) > 0]
        slots = 1
        if remaining_total == 1 and active_age >= 20:
            slots = 2
        elif active_age >= 40:
            slots = 2

        scored: list[tuple[float, int, str, tuple[int, int], tuple[int, int], str | None]] = []
        for t in missing_types:
            shelves = set(self._known_supply_by_type.get(t, set()))
            for (shelf, itype), _ids in visible_items_by_shelf_type.items():
                if itype == t:
                    shelves.add(shelf)
            if not shelves:
                continue
            for shelf in shelves:
                pickup_cells = tuple(find_all_pickup_positions(grid, shelf))
                if not pickup_cells:
                    continue
                best_drop = min(int(drop_map.get(cell, INF)) for cell in pickup_cells)
                for bid in wait_candidates:
                    dmap = dist_maps[bid]
                    best_cell = None
                    best_pick = INF
                    for cell in pickup_cells:
                        d = int(dmap.get(cell, INF))
                        if d < best_pick:
                            best_pick = d
                            best_cell = cell
                    if best_cell is None or best_pick >= INF:
                        continue
                    eta = float(best_pick) + 0.55 * float(best_drop)
                    if remaining_total == 1:
                        eta -= 1.0
                    item_ids = visible_items_by_shelf_type.get((shelf, t)) or []
                    item_id = item_ids[0] if item_ids else None
                    scored.append((eta, bid, t, shelf, best_cell, item_id))

        if not scored:
            return actions, {"critical_overlay_active": 1.0, "critical_overlay_overrides": 0.0}

        scored.sort(key=lambda row: (float(row[0]), int(row[1]), str(row[2]), int(row[3][1]), int(row[3][0])))
        used_bots: set[int] = set()
        type_assigned: Counter[str] = Counter()
        overrides = 0
        for eta, bid, t, shelf, best_cell, item_id in scored:
            if overrides >= slots:
                break
            if bid in used_bots:
                continue
            if type_assigned.get(t, 0) >= int(active_need.get(t, 0)):
                continue
            bot = bots_by_id[bid]
            pos = self._bot_pos(bot)
            if item_id is not None and abs(pos[0] - shelf[0]) + abs(pos[1] - shelf[1]) == 1 and len(bot.inventory) < 3:
                actions_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=item_id)
            else:
                path = bfs_shortest_path(grid, pos, best_cell)
                if not path or len(path) <= 1:
                    continue
                nxt = path[1]
                actions_by_bot[bid] = BotActionCommand(bot=bid, action=action_for_move(pos, nxt))
            used_bots.add(bid)
            type_assigned[t] += 1
            overrides += 1

        if overrides <= 0:
            return actions, {"critical_overlay_active": 1.0, "critical_overlay_overrides": 0.0}
        ordered = [actions_by_bot[int(b.id)] for b in bots]
        return RoundActions(actions=ordered), {"critical_overlay_active": 1.0, "critical_overlay_overrides": float(overrides)}

    def _decide_with_rescue_engine(self, state: GameState) -> RoundActions:
        if (
            not self._rescue_secondary_active
            and self._rescue_engine_secondary is not None
            and int(state.round) >= int(self.config.rescue_profile_switch_round)
            and int(state.score) < int(self.config.rescue_profile_switch_score_min)
        ):
            self._rescue_engine = self._rescue_engine_secondary
            self._rescue_secondary_active = True
        actions = self._rescue_engine.decide(state)
        actions, overlay_stats = self._apply_critical_dispatch_overlay(state, actions)
        self.last_decision_ms = float(getattr(self._rescue_engine, "last_decision_ms", 0.0))
        self.last_collisions_avoided = int(getattr(self._rescue_engine, "last_collisions_avoided", 0))
        self.last_swaps_prevented = int(getattr(self._rescue_engine, "last_swaps_prevented", 0))
        self.last_replans = int(getattr(self._rescue_engine, "last_replans", 0))
        self.last_fallback_used = bool(getattr(self._rescue_engine, "last_fallback_used", False))
        self.last_round_telemetry = dict(getattr(self._rescue_engine, "last_round_telemetry", {}) or {})
        self.last_round_telemetry["experimental_rescue_mode"] = 1.0
        self.last_round_telemetry["experimental_rescue_secondary_active"] = 1.0 if self._rescue_secondary_active else 0.0
        self.last_round_telemetry.update(overlay_stats)
        self.last_round_debug = dict(getattr(self._rescue_engine, "last_round_debug", {}) or {})
        self.last_assignment_snapshot = dict(getattr(self._rescue_engine, "last_assignment_snapshot", {}) or {})
        self.last_pre_collision_actions = dict(getattr(self._rescue_engine, "last_pre_collision_actions", {}) or {})
        self.last_whca_used = bool(getattr(self._rescue_engine, "last_whca_used", False))
        self.last_whca_ms = float(getattr(self._rescue_engine, "last_whca_ms", 0.0))
        self._round_wait_reason_by_bot = dict(getattr(self._rescue_engine, "_round_wait_reason_by_bot", {}) or {})
        return actions

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()
        self.last_collisions_avoided = 0
        self.last_swaps_prevented = 0
        self.last_replans = 0
        self.last_fallback_used = False
        self.last_round_telemetry = {}
        self.last_round_debug = {}
        self.last_assignment_snapshot = {}
        self.last_pre_collision_actions = {}
        self.last_whca_used = False
        self.last_whca_ms = 0.0
        self._round_wait_reason_by_bot = {}

        if self._rescue_active:
            return self._decide_with_rescue_engine(state)

        grid = Grid(state.grid)
        self._update_supply(state)
        bots = sorted(state.bots, key=lambda b: int(b.id))
        drop = (int(state.drop_off[0]), int(state.drop_off[1]))
        active = get_active_order(state)
        preview = get_preview_order(state)
        if active is None:
            self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
            return RoundActions(actions=[BotActionCommand(bot=int(b.id), action=BotAction.WAIT) for b in bots])

        self._update_progress(active)
        active_need = self._rem(active)
        preview_need = self._rem(preview)
        drop_map = bfs_distance_map(grid, drop)
        dist_by_bot = {int(b.id): bfs_distance_map(grid, self._bot_pos(b)) for b in bots}

        active_cargo_by_bot: dict[int, int] = {}
        carried_counter: Counter[str] = Counter()
        carried_remaining = Counter(active_need)
        for b in bots:
            bid = int(b.id)
            tmp = Counter(active_need)
            c = 0
            for t in b.inventory:
                s = str(t)
                if tmp.get(s, 0) > 0:
                    tmp[s] -= 1
                    c += 1
                if carried_remaining.get(s, 0) > 0:
                    carried_remaining[s] -= 1
                    carried_counter[s] += 1
            active_cargo_by_bot[bid] = c

        reliable = Counter()
        tmp_need = Counter(active_need)
        for b in bots:
            bid = int(b.id)
            if int(drop_map.get(self._bot_pos(b), INF)) > int(self.config.reliable_drop_dist_max):
                continue
            if int(self._wait_streak_by_bot.get(bid, 0)) > int(self.config.reliable_wait_streak_max):
                continue
            for t in b.inventory:
                s = str(t)
                if tmp_need.get(s, 0) > 0:
                    tmp_need[s] -= 1
                    reliable[s] += 1

        deficit = self._sub(active_need, reliable)
        harvest_deficit = self._sub(active_need, carried_counter)
        deficit_total = int(sum(deficit.values()))
        deficit_distinct = int(sum(1 for v in deficit.values() if int(v) > 0))
        harvest_deficit_total = int(sum(harvest_deficit.values()))
        harvest_deficit_distinct = int(sum(1 for v in harvest_deficit.values() if int(v) > 0))
        tail_open = (
            harvest_deficit_total > 0
            and harvest_deficit_total <= int(self.config.tail_remaining_threshold)
            and harvest_deficit_distinct <= int(self.config.tail_distinct_threshold)
        )

        recent_progress = int(sum(list(self._progress_window)[-max(1, int(self.config.secured_progress_window)):]))
        conversion_live = any(active_cargo_by_bot.get(int(b.id), 0) > 0 and int(drop_map.get(self._bot_pos(b), INF)) <= int(self.config.reliable_drop_dist_max) for b in bots)
        active_secured = deficit_total <= 0 and (conversion_live or recent_progress >= int(self.config.secured_progress_min_points))
        preview_allowed = bool(active_secured or (deficit_total <= 1 and conversion_live and recent_progress >= int(self.config.secured_progress_min_points)))

        cargo_total = int(sum(active_cargo_by_bot.values()))
        if cargo_total <= 0 and deficit_total > 3:
            conv_floor = 0
        elif cargo_total <= 0:
            conv_floor = 1
        elif tail_open or cargo_total >= 4 or deficit_total <= 2:
            conv_floor = min(2, int(self.config.converter_floor_max))
        else:
            conv_floor = 1

        carriers = sorted(bots, key=lambda b: (-int(active_cargo_by_bot.get(int(b.id), 0)), int(drop_map.get(self._bot_pos(b), INF)), int(b.id)))
        converters: list[int] = []
        for b in carriers:
            bid = int(b.id)
            if active_cargo_by_bot.get(bid, 0) <= 0:
                continue
            converters.append(bid)
            if len(converters) >= conv_floor:
                break
        if len(converters) < conv_floor:
            for b in sorted(bots, key=lambda b: (int(drop_map.get(self._bot_pos(b), INF)), int(b.id))):
                bid = int(b.id)
                if bid in converters:
                    continue
                converters.append(bid)
                if len(converters) >= conv_floor:
                    break
        converter_set = set(converters)

        critical_slots = 0
        if deficit_total > 0:
            critical_slots = int(self.config.critical_dispatch_slots_tail) if tail_open else int(self.config.critical_dispatch_slots_default)
        critical_types = [t for t, c in harvest_deficit.items() if int(c) > 0]
        critical_types.sort(key=lambda t: (-int(harvest_deficit.get(t, 0)), str(t)))
        if not critical_types:
            critical_types = [t for t, c in active_need.items() if int(c) > 0]
            critical_types.sort(key=lambda t: (-int(active_need.get(t, 0)), str(t)))
        critical_set = set(critical_types[: max(1, int(self.config.critical_dispatch_type_limit))])

        items_meta: list[dict[str, Any]] = []
        for item in state.items:
            shelf = (int(item.position[0]), int(item.position[1]))
            cells = tuple(find_all_pickup_positions(grid, shelf))
            if not cells:
                continue
            drop_eta = min(int(drop_map.get(c, INF)) for c in cells)
            items_meta.append({"id": str(item.id), "type": str(item.type), "shelf": shelf, "cells": cells, "drop_eta": drop_eta})
        item_meta_by_id = {m["id"]: m for m in items_meta}
        useful_targets = any(active_need.get(m["type"], 0) > 0 or (preview_allowed and preview_need.get(m["type"], 0) > 0) for m in items_meta)
        forecast = self._forecast(int(state.active_order_index))

        cx = int(round((grid.width - 1) / 2))
        cy = int(round((grid.height - 1) / 2))
        stage_cells = [
            cell
            for cell in sorted(
                list(grid.all_walkable()),
                key=lambda c: (
                    abs(int(c[0]) - cx) + abs(int(c[1]) - cy),
                    abs(int(c[0]) - int(drop[0])) + abs(int(c[1]) - int(drop[1])),
                    int(c[1]),
                    int(c[0]),
                ),
            )
            if cell != drop
        ]
        if not stage_cells:
            stage_cells = [self._bot_pos(b) for b in bots]

        pairs: list[tuple[float, int, _Task]] = []
        top_k = max(1, int(self.config.top_k_candidates_per_bot))
        for b in bots:
            bid = int(b.id)
            dmap = dist_by_bot[bid]
            inv_len = int(len(b.inventory))
            active_cargo = int(active_cargo_by_bot.get(bid, 0))
            local_only = bid in converter_set and active_cargo <= 0
            cand: list[_Task] = []

            force_deliver = (
                active_cargo > 0
                and (
                    active_cargo >= 2
                    or deficit_total <= 3
                    or inv_len >= 2
                )
            )

            if active_cargo > 0:
                ddrop = int(drop_map.get(self._bot_pos(b), INF))
                score = 540.0 + 170.0 * active_cargo - 14.0 * ddrop
                if deficit_total <= 2:
                    score += 120.0
                if active_cargo >= 2:
                    score += 85.0
                if force_deliver:
                    score += 260.0
                cand.append(_Task(key=f"deliver:{bid}", kind="deliver", goal=drop, score=score, source="deliver_active_cargo", critical=False))

            if force_deliver:
                m_iterable: list[dict[str, Any]] = []
            else:
                m_iterable = items_meta
            for m in m_iterable:
                best_cell = m["cells"][0]
                best_dist = int(dmap.get(best_cell, INF))
                for cell in m["cells"][1:]:
                    d = int(dmap.get(cell, INF))
                    if d < best_dist:
                        best_dist = d
                        best_cell = cell
                if best_dist >= INF:
                    continue
                if local_only and best_dist > int(self.config.converter_local_harvest_radius):
                    continue

                t = str(m["type"])
                critical = False
                if harvest_deficit.get(t, 0) > 0:
                    base = 260.0
                    critical = True
                    source = "active_deficit"
                    if harvest_deficit_total == 1 and int(harvest_deficit.get(t, 0)) == 1:
                        base += 160.0
                    if t in critical_set:
                        base += 85.0
                elif active_need.get(t, 0) > 0:
                    base = 190.0
                    source = "active_delivered_need"
                    if t in critical_set:
                        base += 55.0
                elif preview_allowed and preview_need.get(t, 0) > 0:
                    base = 70.0 + 100.0 * float(self.config.preview_weight_secured)
                    source = "preview_secured"
                    if t in critical_set:
                        base += 15.0
                elif preview_need.get(t, 0) > 0 and float(self.config.preview_weight_open) > 0.0:
                    base = 100.0 * float(self.config.preview_weight_open)
                    source = "preview_open"
                else:
                    continue

                score = float(base) + float(forecast.get(t, 0)) * 30.0 * float(self.config.forecast_weight)
                score -= float(self.config.eta_weight) * (float(best_dist) + float(self.config.drop_eta_weight) * float(m["drop_eta"]))
                if self._known_type_by_shelf.get(m["shelf"]) == t:
                    score += 16.0
                if active_cargo > 0 and active_need.get(t, 0) > 0:
                    score += 28.0
                if inv_len <= 1 and active_need.get(t, 0) > 0:
                    score += 10.0
                if local_only and harvest_deficit.get(t, 0) <= 0:
                    score -= 220.0
                if not math.isfinite(score):
                    continue

                cand.append(_Task(key=f"pickup:{m['id']}:{best_cell[0]}:{best_cell[1]}", kind="pickup", goal=best_cell, score=score, source=source, critical=bool(critical and t in critical_set), item_id=str(m["id"]), item_type=t, exclusive_item_id=str(m["id"])))

            stage = stage_cells[bid % max(1, len(stage_cells))]
            sdist = int(dmap.get(stage, INF))
            if sdist >= INF:
                stage = self._bot_pos(b)
                sdist = 0
            sscore = 20.0 - 2.0 * sdist - (12.0 if useful_targets else 0.0)
            if deficit_total > 0:
                sscore -= 60.0
            cand.append(_Task(key=f"stage:{bid}:{stage[0]}:{stage[1]}", kind="stage", goal=stage, score=sscore, source="stage_fallback", critical=False))

            cand.sort(key=lambda x: float(x.score), reverse=True)
            for task in cand[:top_k]:
                pairs.append((float(task.score), bid, task))

        pairs.sort(key=lambda r: (-float(r[0]), int(r[1]), str(r[2].key)))
        assigned: dict[int, _Task] = {}
        taken_items: set[str] = set()
        used_critical = 0
        for _sc, bid, task in pairs:
            if bid in assigned:
                continue
            if task.exclusive_item_id and task.exclusive_item_id in taken_items:
                continue
            if task.kind == "pickup" and task.critical and critical_slots > 0 and used_critical >= critical_slots:
                continue
            assigned[bid] = task
            if task.exclusive_item_id:
                taken_items.add(task.exclusive_item_id)
            if task.critical:
                used_critical += 1

        for b in bots:
            bid = int(b.id)
            if bid in assigned:
                continue
            stage = stage_cells[bid % max(1, len(stage_cells))]
            assigned[bid] = _Task(key=f"stage:{bid}:{stage[0]}:{stage[1]}", kind="stage", goal=stage, score=0.0, source="stage_default", critical=False)

        # Keep a minimum active retrieval lane open while active deficit is unresolved.
        if deficit_total > 0:
            min_active_retrievers = (
                max(1, int(self.config.min_active_retrievers_tail))
                if tail_open
                else max(1, int(self.config.min_active_retrievers_open))
            )
            current_active_pickers = {
                int(bid)
                for bid, task in assigned.items()
                if str(task.kind) == "pickup" and str(task.source).startswith("active")
            }
            missing = max(0, int(min_active_retrievers) - len(current_active_pickers))
            if missing > 0:
                for _sc, bid, task in pairs:
                    if missing <= 0:
                        break
                    bid = int(bid)
                    if bid in current_active_pickers:
                        continue
                    if str(task.kind) != "pickup" or not str(task.source).startswith("active"):
                        continue
                    if task.exclusive_item_id and task.exclusive_item_id in taken_items:
                        continue
                    prev_task = assigned.get(bid)
                    if prev_task is not None and prev_task.exclusive_item_id:
                        taken_items.discard(prev_task.exclusive_item_id)
                    assigned[bid] = task
                    if task.exclusive_item_id:
                        taken_items.add(task.exclusive_item_id)
                    current_active_pickers.add(bid)
                    missing -= 1

        next_target: dict[int, str] = {}
        for b in bots:
            bid = int(b.id)
            task = assigned[bid]
            next_target[bid] = task.key
            prev = self._last_target_key_by_bot.get(bid)
            if prev is not None and prev != task.key:
                self.last_replans += 1
            snap: dict[str, object] = {"target_type": task.kind, "source": task.source, "score": float(task.score), "critical": 1 if task.critical else 0, "goal": [int(task.goal[0]), int(task.goal[1])]}
            if task.item_id is not None:
                snap["item_id"] = task.item_id
            if task.item_type is not None:
                snap["item_type"] = task.item_type
            self.last_assignment_snapshot[bid] = snap
        self._last_target_key_by_bot = next_target

        actions_by_bot: dict[int, BotActionCommand] = {}
        goals_by_bot: dict[int, tuple[int, int]] = {}
        wait_reason: dict[int, str] = {}

        for b in bots:
            bid = int(b.id)
            pos = self._bot_pos(b)
            task = assigned[bid]

            if pos == drop and b.inventory:
                ac = int(active_cargo_by_bot.get(bid, 0))
                if ac > 0 or len(b.inventory) >= 3 or deficit_total <= 0:
                    actions_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.DROP_OFF)
                    self.last_pre_collision_actions[bid] = {"action": BotAction.DROP_OFF.value, "source": "drop_now"}
                    continue

            if task.kind == "pickup" and task.item_id is not None and len(b.inventory) < 3:
                m = item_meta_by_id.get(task.item_id)
                if m is not None:
                    shelf = m["shelf"]
                    if abs(pos[0] - shelf[0]) + abs(pos[1] - shelf[1]) == 1:
                        actions_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.PICK_UP, item_id=str(task.item_id))
                        self.last_pre_collision_actions[bid] = {"action": BotAction.PICK_UP.value, "item_id": str(task.item_id), "source": str(task.source)}
                        continue

            goals_by_bot[bid] = drop if task.kind == "deliver" else task.goal

        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        stationary: set[tuple[int, int]] = set()
        for b in bots:
            bid = int(b.id)
            pos = self._bot_pos(b)
            if bid in actions_by_bot:
                stationary.add(pos)
                continue
            goal = goals_by_bot.get(bid)
            if goal is None:
                wait_reason[bid] = WAIT_NO_ASSIGN if useful_targets else WAIT_NO_TARGET
                stationary.add(pos)
                continue
            if goal == pos:
                wait_reason[bid] = WAIT_NO_ASSIGN
                stationary.add(pos)
                continue
            path = bfs_shortest_path(grid, pos, goal)
            if not path or len(path) <= 1:
                wait_reason[bid] = WAIT_NO_TARGET
                stationary.add(pos)
                continue
            nxt = path[1]
            move_plans.append((bid, pos, nxt))
            self.last_pre_collision_actions[bid] = {"action": action_for_move(pos, nxt).value, "target": [int(nxt[0]), int(nxt[1])], "goal": [int(goal[0]), int(goal[1])], "source": str((self.last_assignment_snapshot.get(bid) or {}).get("source", ""))}

        if move_plans:
            coop_goals = {bid: goals_by_bot[bid] for bid, _c, _d in move_plans if bid in goals_by_bot}
            whca_t0 = time.perf_counter()
            coop = plan_windowed_next_steps(grid=grid, plans=move_plans, goals_by_bot=coop_goals, occupied=stationary, blocked=set(), window=max(1, int(self.config.path_window)), deliverer_ids=converter_set)
            self.last_whca_ms = (time.perf_counter() - whca_t0) * 1000.0
            self.last_whca_used = bool(coop)
            if coop:
                move_plans = [(bid, cur, tuple(coop.get(int(bid), des))) for bid, cur, des in move_plans]

            resolved, stats = resolve_collisions_with_stats(move_plans, stationary, reservation_horizon=max(1, int(self.config.reservation_horizon)))
            self.last_collisions_avoided = int(stats.blocked_moves)
            self.last_swaps_prevented = int(stats.swaps_prevented)
            for bid, cur, _des in move_plans:
                actual = resolved.get(int(bid), cur)
                if actual == cur:
                    wait_reason[int(bid)] = WAIT_COLLISION
                    actions_by_bot[int(bid)] = BotActionCommand(bot=int(bid), action=BotAction.WAIT)
                else:
                    actions_by_bot[int(bid)] = BotActionCommand(bot=int(bid), action=action_for_move(cur, actual))

        for b in bots:
            bid = int(b.id)
            if bid in actions_by_bot:
                continue
            r = wait_reason.get(bid)
            if r is None:
                r = WAIT_NO_ASSIGN if useful_targets else WAIT_NO_TARGET
                wait_reason[bid] = r
            actions_by_bot[bid] = BotActionCommand(bot=bid, action=BotAction.WAIT)

        self._round_wait_reason_by_bot = {}
        for b in bots:
            bid = int(b.id)
            cmd = actions_by_bot[bid]
            if cmd.action == BotAction.WAIT:
                r = wait_reason.get(bid, WAIT_NO_ASSIGN if useful_targets else WAIT_NO_TARGET)
                self._round_wait_reason_by_bot[bid] = str(r)
                self._wait_streak_by_bot[bid] = int(self._wait_streak_by_bot.get(bid, 0)) + 1
            else:
                self._wait_streak_by_bot[bid] = 0

        wait_no_assign = sum(1 for r in self._round_wait_reason_by_bot.values() if r == WAIT_NO_ASSIGN)
        wait_no_target = sum(1 for r in self._round_wait_reason_by_bot.values() if r == WAIT_NO_TARGET)
        wait_collision = sum(1 for r in self._round_wait_reason_by_bot.values() if r == WAIT_COLLISION)

        self.last_round_telemetry = {
            "active_remaining_delivered_only": float(sum(active_need.values())),
            "active_committed_reliable": float(sum(reliable.values())),
            "active_deficit": float(deficit_total),
            "active_deficit_distinct": float(deficit_distinct),
            "active_harvest_deficit": float(harvest_deficit_total),
            "active_harvest_deficit_distinct": float(harvest_deficit_distinct),
            "active_tail_open": 1.0 if tail_open else 0.0,
            "active_secured": 1.0 if active_secured else 0.0,
            "preview_allowed": 1.0 if preview_allowed else 0.0,
            "converter_floor_target": float(conv_floor),
            "converters_assigned": float(len(converter_set)),
            "critical_dispatch_slots": float(max(0, critical_slots)),
            "critical_dispatch_used": float(max(0, used_critical)),
            "forecast_signal_total": float(sum(forecast.values())),
            "items_visible": float(len(items_meta)),
            "known_supply_types": float(len(self._known_supply_by_type)),
            "wait_due_to_no_assignment": float(wait_no_assign),
            "wait_due_to_no_target": float(wait_no_target),
            "wait_due_to_collision_block": float(wait_collision),
            "collisions_avoided": float(self.last_collisions_avoided),
            "swaps_prevented": float(self.last_swaps_prevented),
            "replans": float(self.last_replans),
        }

        if (
            not self._rescue_active
            and bool(self.config.rescue_fallback_enabled)
            and int(state.round) >= int(self.config.rescue_round_threshold)
            and int(state.score) <= int(self.config.rescue_score_threshold)
            and recent_progress <= 0
        ):
            self._rescue_active = True
            return self._decide_with_rescue_engine(state)

        self.last_decision_ms = (time.perf_counter() - t0) * 1000.0
        return RoundActions(actions=[actions_by_bot[int(b.id)] for b in bots])
