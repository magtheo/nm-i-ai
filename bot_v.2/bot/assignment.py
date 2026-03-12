"""Task assignment for multi-bot grocery routing."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from typing import Optional

from .grid import Grid
from .models import GameState, ItemInfo
from .orders import (
    COMMIT_MODE_COMMITTED,
    COMMIT_MODE_DELIVERED_ONLY,
    COMMIT_MODE_OPTIMISTIC,
    compute_needed_items,
    compute_preview_items,
    get_active_order,
    items_matching_active,
    should_prefetch_preview,
)
from .pathfinding import bfs_distance_map, find_all_pickup_positions

ORDER_BONUS = 5


@dataclass(frozen=True)
class AssignmentPolicy:
    lookahead_orders: int = 2
    active_weight: float = 10.0
    preview_weight: float = 4.0
    dropoff_completion_threshold: float = 0.67
    zone_penalty_weight: float = 0.0
    dist_weight: float = 1.0
    active_scarce_type_bonus: float = 0.0
    active_scarce_type_threshold: int = 5
    dropoff_dist_weight: float = 0.35
    congestion_weight: float = 1.0
    collision_risk_weight: float = 1.0
    replan_penalty_weight: float = 1.0
    carry_home_bias_weight: float = 0.0
    urgency_weight: float = 1.0
    trip_chain_bonus_weight: float = 0.0
    future_depth_decay: float = 1.0
    future_count_weight: float = 0.0
    future_prefetch_bonus: int = 0
    future_priority_mode: str = "depth"  # depth | flat
    prefetch_min_completion: float = 0.0
    prefetch_spare_slots: int = 0
    prefetch_nonmatching_cap: int = 3
    overflow_prefetch_when_active_assigned: bool = False
    overflow_prefetch_round_limit: int = 0
    strict_active_priority: bool = False
    strict_active_release_completion: float = 1.0
    prefetch_release_use_delivered_completion: bool = False
    force_dropoff_for_full_nonmatching: bool = False
    always_deliver_matching: bool = False
    avoid_dropoff_block_when_matching: bool = True
    max_concurrent_deliverers: int = 2
    adaptive_deliver_queue: bool = False
    deliver_queue_min: int = 1
    deliver_queue_max: int = 3
    assignment_strategy: str = "greedy"  # greedy | auction | hungarian
    auction_option_depth: int = 12
    auction_allow_skip: bool = True
    hungarian_option_depth: int = 18
    hungarian_fallback_to_greedy: bool = True
    hungarian_min_assignments: int = 2
    hungarian_active_only_when_needed: bool = True
    hungarian_active_only_remaining_threshold: int = 3
    hungarian_active_only_distinct_threshold: int = 2
    hungarian_preview_utility_discount_when_active_open: float = 0.75
    reservation_horizon: int = 2
    hysteresis_penalty: float = 1.0
    sticky_target_bonus: float = 0.0
    early_deliver_matching_count: int = 0
    early_deliver_inventory_threshold: int = 2
    endgame_disable_prefetch_rounds: int = 0
    endgame_force_deliver_rounds: int = 0
    endgame_strict_active: bool = False
    clear_adjacent_dropoff_lane: bool = False
    clear_lane_distance: int = 4
    allow_same_shelf_for_same_type: bool = False
    allow_same_shelf_for_active_duplicates: bool = False
    active_duplicate_same_shelf_min_gap: int = 2
    tie_break_seed: int = 0
    tie_break_dynamic: bool = False
    two_step_trip_weight: float = 0.0
    two_step_trip_min_gain: int = 2
    two_step_order_bonus_weight: float = 1.0
    two_step_max_extra_steps: int = 2
    two_step_completion_delay_threshold: int = 1
    predicted_dropoff_density_weight: float = 0.0
    predicted_corridor_density_weight: float = 0.0
    stall_round_threshold: int = 24
    dropoff_stop_line_enabled: bool = False
    dropoff_stop_line_k: int = 2
    dropoff_stop_line_radius: int = 2
    dropoff_stop_line_trigger_density: float = 0.67
    anti_no_assignment_enabled: bool = False
    secondary_assignment_enabled: bool = False
    secondary_duplicate_support: bool = True
    anti_starvation_enabled: bool = True
    anti_starvation_rounds: int = 4
    anti_starvation_bonus: float = 2.0
    secondary_max_distance: int = 10
    secondary_reposition_empty_only: bool = True
    secondary_delivered_need_soft_near_radius: int = 4
    secondary_delivered_need_soft_same_row_gap: int = 4
    secondary_delivered_need_soft_near_penalty: float = 0.55
    secondary_delivered_need_soft_same_row_penalty: float = 0.35
    transition_stash_enabled: bool = False
    transition_stash_completion_ratio: float = 0.85
    transition_stash_remaining_items: int = 2
    transition_stash_finisher_count: int = 2
    transition_stash_preview_bonus: float = 2.0
    demand_commitment_mode: str = COMMIT_MODE_OPTIMISTIC  # optimistic | committed | delivered_only
    demand_commit_radius: int = 2
    demand_preview_safety_slots: int = 0
    pipeline_budget_enabled: bool = False
    pipeline_secure_delivered_deficit_threshold: int = 2
    soft_pipeline_budget_enabled: bool = False
    soft_pipeline_secure_delivered_deficit_threshold: int = 2
    soft_pipeline_active_close_bonus: float = 1.5
    soft_pipeline_delivery_conversion_bonus: float = 1.25
    soft_pipeline_preview_preload_discount: float = 0.9
    soft_pipeline_transition_preview_bonus: float = 0.75
    soft_pipeline_fallback_penalty_open_tail: float = 0.6
    task_pool_admission_enabled: bool = False
    task_pool_critical_min_bots: int = 5
    task_pool_critical_max_bots: int = 8
    task_pool_tail_boost_bots: int = 2
    task_pool_preview_reserve_bots: int = 2
    etadlc_enabled: bool = False
    etadlc_converter_floor_min: int = 2
    etadlc_converter_floor_tail: int = 3
    etadlc_tail_remaining_threshold: int = 2
    etadlc_retrieval_eta_weight: float = 1.0
    etadlc_known_shelf_target_bonus: float = 1.5
    etadlc_local_courier_harvest_radius: int = 5
    critical_dispatch_overlay_enabled: bool = False
    critical_dispatch_max_slots: int = 2
    critical_dispatch_tail_remaining_threshold: int = 2
    critical_dispatch_eta_weight: float = 0.6
    critical_dispatch_known_shelf_bonus: float = 1.5
    critical_dispatch_preview_block_when_unsecured: bool = True
    critical_dispatch_non_tail_enabled: bool = True
    critical_dispatch_non_tail_min_order_age_rounds: int = 0
    critical_dispatch_non_tail_max_remaining_items: int = 999
    critical_dispatch_non_tail_type_limit: int = 1
    critical_dispatch_scarcity_weight: float = 0.0
    critical_dispatch_payload_close_bonus: float = 2.0
    critical_dispatch_payload_last_type_bonus: float = 1.0
    critical_dispatch_payload_two_item_bonus: float = 0.8
    critical_dispatch_converter_payload_weight: float = 0.9
    critical_dispatch_reliable_max_dropoff_dist: int = 4
    critical_dispatch_reliable_min_matching_ratio: float = 0.5
    critical_dispatch_focus_order_index_max: int = 50
    critical_dispatch_secondary_window_rounds: int = 240
    critical_dispatch_throughput_reserve_bots: int = 2


@dataclass
class Assignment:
    """What a bot should do this round."""

    target_type: str  # "pick_item" | "deliver" | "idle" | "pre_pick" | "secondary_reposition"
    item: Optional[ItemInfo] = None
    pickup_pos: Optional[tuple[int, int]] = None
    drop_off: Optional[tuple[int, int]] = None
    target_id: Optional[str] = None
    source: str = ""


@dataclass(frozen=True)
class _BotCandidate:
    item: ItemInfo
    target_type: str
    pickup_pos: tuple[int, int]
    rank: tuple
    utility_score: float
    critical_payload_bonus_applied: bool = False


def _inventory_after_active(state: GameState) -> list[str]:
    active = get_active_order(state)
    if active is None:
        out: list[str] = []
        for bot in state.bots:
            out.extend(bot.inventory)
        return out

    remaining = list(active.items_required)
    for delivered in active.items_delivered:
        if delivered in remaining:
            remaining.remove(delivered)

    carry: list[str] = []
    for bot in state.bots:
        for item_type in bot.inventory:
            if item_type in remaining:
                remaining.remove(item_type)
            else:
                carry.append(item_type)
    return carry


def _future_prefetch_types(
    *,
    state: GameState,
    order_forecast: dict[int, list[str]],
    active_order_index: int,
    lookahead_orders: int,
) -> tuple[list[str], dict[str, int]]:
    future_types: list[str] = []
    depth_by_type: dict[str, int] = {}
    for depth in range(1, max(1, lookahead_orders) + 1):
        items = order_forecast.get(active_order_index + depth)
        if items is None:
            break
        for item_type in items:
            future_types.append(item_type)
            depth_by_type.setdefault(item_type, depth)

    carry = _inventory_after_active(state)
    for item_type in carry:
        if item_type in future_types:
            future_types.remove(item_type)
    return future_types, depth_by_type


def _seed_rank(seed: int, bot_id: int, item_id: str, pickup_pos: tuple[int, int]) -> int:
    if seed <= 0:
        return 0
    key = f"{seed}:{bot_id}:{item_id}:{pickup_pos[0]}:{pickup_pos[1]}".encode("ascii", errors="ignore")
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big")


def _pickup_zone_columns(
    pickup_cache: dict[str, list[tuple[int, int]]],
) -> list[int]:
    return sorted({pickup_pos[0] for positions in pickup_cache.values() for pickup_pos in positions})


def _zone_center_x(
    *,
    bot_index: int,
    bot_count: int,
    pickup_columns: list[int],
    grid_width: int,
) -> int:
    if pickup_columns:
        if bot_count <= 1:
            return int(pickup_columns[len(pickup_columns) // 2])
        zone_idx = int(round((len(pickup_columns) - 1) * float(bot_index) / float(bot_count - 1)))
        zone_idx = max(0, min(len(pickup_columns) - 1, zone_idx))
        return int(pickup_columns[zone_idx])

    left = 1
    right = max(left, int(grid_width) - 2)
    if bot_count <= 1:
        return int((left + right) // 2)
    return int(round(left + (right - left) * float(bot_index) / float(bot_count - 1)))


def _hungarian_min_cost(cost: list[list[float]]) -> list[int]:
    """Solve rectangular min-cost assignment (n rows, m cols, n <= m).

    Returns assigned column index per row.
    """
    n = len(cost)
    if n <= 0:
        return []
    m = len(cost[0]) if cost[0] else 0
    if m <= 0:
        return [-1] * n
    if n > m:
        raise ValueError("hungarian requires n <= m")

    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(0, m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    out = [-1] * n
    for j in range(1, m + 1):
        i = p[j]
        if i > 0:
            out[i - 1] = j - 1
    return out


def assign_bots(
    state: GameState,
    grid: Grid,
    *,
    item_blocked: frozenset[tuple[int, int]] = frozenset(),
    blocked_item_ids: Optional[set[str]] = None,
    policy: AssignmentPolicy | None = None,
    sticky_targets: Optional[dict[int, str]] = None,
    order_forecast: Optional[dict[int, list[str]]] = None,
    active_order_index: Optional[int] = None,
    defer_deliver_bot_ids: Optional[set[int]] = None,
    force_active_only_bot_ids: Optional[set[int]] = None,
    primary_assignment_miss_streak_by_bot: Optional[dict[int, int]] = None,
    known_supply_by_type: Optional[dict[str, set[tuple[int, int]]]] = None,
) -> dict[int, Assignment]:
    """Greedy nearest assignment with deterministic tie-breaks."""
    cfg = policy or AssignmentPolicy()
    blocked_ids = blocked_item_ids or set()
    defer_deliver_ids = {int(bot_id) for bot_id in (defer_deliver_bot_ids or set())}
    force_active_only_ids = {int(bot_id) for bot_id in (force_active_only_bot_ids or set())}
    primary_miss_streak = {
        int(bot_id): max(0, int(streak))
        for bot_id, streak in (primary_assignment_miss_streak_by_bot or {}).items()
    }
    rank_seed = int(cfg.tie_break_seed)
    if cfg.tie_break_dynamic:
        rank_seed += int((state.active_order_index + 1) * 1009)
    assignments: dict[int, Assignment] = {}
    drop_off = (state.drop_off[0], state.drop_off[1])
    demand_mode = str(cfg.demand_commitment_mode).strip().lower()
    if demand_mode not in {
        COMMIT_MODE_OPTIMISTIC,
        COMMIT_MODE_COMMITTED,
        COMMIT_MODE_DELIVERED_ONLY,
    }:
        demand_mode = COMMIT_MODE_OPTIMISTIC
    demand_commit_radius = max(0, int(cfg.demand_commit_radius))
    demand_preview_safety_slots = max(0, int(cfg.demand_preview_safety_slots))

    needed_types = compute_needed_items(
        state,
        commitment_mode=demand_mode,
        commit_radius=demand_commit_radius,
    )
    needed_counter = Counter(needed_types)
    if demand_mode == COMMIT_MODE_OPTIMISTIC:
        carried_matching_counter: Counter[str] = Counter()
        for bot in state.bots:
            for item_type in items_matching_active(bot, state):
                carried_matching_counter[item_type] += 1
        uncovered_active_counter = needed_counter - carried_matching_counter
        remaining_active_items = int(sum(uncovered_active_counter.values()))
        active_covered_by_carry = all(
            carried_matching_counter.get(item_type, 0) >= count
            for item_type, count in needed_counter.items()
        )
    else:
        remaining_active_items = int(sum(needed_counter.values()))
        active_covered_by_carry = remaining_active_items <= 0
    active_order = get_active_order(state)
    active_total = len(active_order.items_required) if active_order is not None else 0
    completion_ratio = 1.0
    if active_total > 0:
        completion_ratio = (active_total - remaining_active_items) / active_total
    delivered_completion_ratio = 1.0
    if active_total > 0 and active_order is not None:
        delivered_completion_ratio = len(active_order.items_delivered) / active_total
    prefetch_release_ratio = (
        delivered_completion_ratio
        if bool(cfg.prefetch_release_use_delivered_completion)
        else completion_ratio
    )
    rounds_left = max(0, int(state.max_rounds) - int(state.round))
    remaining_inventory_slots = sum(max(0, 3 - len(bot.inventory)) for bot in state.bots)
    any_bot_has_matching = any(bool(items_matching_active(bot, state)) for bot in state.bots)
    prefetch_budget = 0
    preview_types: list[str] = []
    preview_depth_by_type: dict[str, int] = {}
    future_mode = bool(order_forecast) and active_order_index is not None
    endgame_strict_active = (
        bool(cfg.endgame_strict_active)
        and rounds_left <= max(0, int(cfg.endgame_disable_prefetch_rounds))
    )
    strict_active_now = bool(cfg.strict_active_priority or endgame_strict_active)
    strict_release_completion = max(0.0, min(1.0, float(cfg.strict_active_release_completion)))
    strict_prefetch_block = strict_active_now and prefetch_release_ratio < strict_release_completion
    if not bool(cfg.prefetch_release_use_delivered_completion):
        strict_prefetch_block = strict_prefetch_block and not active_covered_by_carry

    delivered_only_needed_types = compute_needed_items(
        state,
        commitment_mode=COMMIT_MODE_DELIVERED_ONLY,
        commit_radius=demand_commit_radius,
    )
    committed_needed_types = compute_needed_items(
        state,
        commitment_mode=COMMIT_MODE_COMMITTED,
        commit_radius=demand_commit_radius,
    )
    delivered_only_remaining_active_items = len(delivered_only_needed_types)
    committed_remaining_active_items = len(committed_needed_types)
    delivered_only_counter = Counter(str(item_type) for item_type in delivered_only_needed_types)
    committed_active_cargo = max(
        0,
        delivered_only_remaining_active_items - committed_remaining_active_items,
    )
    active_missing_distinct = len({item_type for item_type in delivered_only_needed_types})
    etadlc_tail_open = bool(
        delivered_only_remaining_active_items > 0
        and delivered_only_remaining_active_items <= max(1, int(cfg.etadlc_tail_remaining_threshold))
    )
    critical_overlay_tail_open = bool(
        delivered_only_remaining_active_items > 0
        and delivered_only_remaining_active_items
        <= max(1, int(cfg.critical_dispatch_tail_remaining_threshold))
        and active_missing_distinct <= 2
    )
    pipeline_active_secured = (
        delivered_only_remaining_active_items > 0
        and committed_active_cargo >= delivered_only_remaining_active_items
    )
    etadlc_active_secured = bool(
        delivered_only_remaining_active_items <= 0
        or (
            delivered_only_remaining_active_items > 0
            and committed_active_cargo >= delivered_only_remaining_active_items
            and any_bot_has_matching
        )
    )
    pipeline_mode = "off"
    active_close_budget = len(state.bots)
    preview_preload_budget_limit = len(state.bots)
    delivery_conversion_budget = max(1, int(cfg.max_concurrent_deliverers))
    pipeline_fallback_budget: int | None = None
    hard_secure_threshold = max(1, int(cfg.pipeline_secure_delivered_deficit_threshold))
    if bool(cfg.pipeline_budget_enabled):
        if delivered_only_remaining_active_items <= 0:
            pipeline_mode = "transition"
            active_close_budget = 4
            preview_preload_budget_limit = 2
            delivery_conversion_budget = 3
            pipeline_fallback_budget = 2
        elif pipeline_active_secured or delivered_only_remaining_active_items <= hard_secure_threshold:
            pipeline_mode = "secure"
            active_close_budget = 6
            preview_preload_budget_limit = 1
            delivery_conversion_budget = 3
            pipeline_fallback_budget = 2
        else:
            pipeline_mode = "build"
            active_close_budget = 8
            preview_preload_budget_limit = 0
            delivery_conversion_budget = 2
            pipeline_fallback_budget = 1
        preview_preload_budget_limit = max(
            0,
            min(
                int(preview_preload_budget_limit),
                max(0, len(state.bots) - int(active_close_budget)),
            ),
        )
        if pipeline_mode in {"secure", "transition"}:
            # In secure/transition, allow bounded preview preload based on explicit budgets.
            strict_prefetch_block = False

    soft_pipeline_mode = "off"
    soft_secure_threshold = max(1, int(cfg.soft_pipeline_secure_delivered_deficit_threshold))
    soft_active_close_bonus = 0.0
    soft_delivery_conversion_bonus = 0.0
    soft_preview_preload_discount = 0.0
    soft_transition_preview_bonus = 0.0
    soft_fallback_penalty_open_tail = 0.0
    if bool(cfg.soft_pipeline_budget_enabled):
        if delivered_only_remaining_active_items <= 0:
            soft_pipeline_mode = "transition"
            soft_delivery_conversion_bonus = max(
                0.0,
                float(cfg.soft_pipeline_delivery_conversion_bonus),
            )
            soft_transition_preview_bonus = max(
                0.0,
                float(cfg.soft_pipeline_transition_preview_bonus),
            )
        elif pipeline_active_secured or delivered_only_remaining_active_items <= soft_secure_threshold:
            soft_pipeline_mode = "secure"
            soft_active_close_bonus = max(0.0, float(cfg.soft_pipeline_active_close_bonus)) * 0.6
            soft_delivery_conversion_bonus = max(0.0, float(cfg.soft_pipeline_delivery_conversion_bonus))
            soft_preview_preload_discount = max(
                0.0,
                float(cfg.soft_pipeline_preview_preload_discount),
            ) * 0.45
            soft_fallback_penalty_open_tail = max(
                0.0,
                float(cfg.soft_pipeline_fallback_penalty_open_tail),
            ) * 0.6
        else:
            soft_pipeline_mode = "build"
            soft_active_close_bonus = max(0.0, float(cfg.soft_pipeline_active_close_bonus))
            soft_delivery_conversion_bonus = max(
                0.0,
                float(cfg.soft_pipeline_delivery_conversion_bonus),
            ) * 0.35
            soft_preview_preload_discount = max(
                0.0,
                float(cfg.soft_pipeline_preview_preload_discount),
            )
            soft_fallback_penalty_open_tail = max(
                0.0,
                float(cfg.soft_pipeline_fallback_penalty_open_tail),
            )

    if cfg.lookahead_orders > 1:
        if prefetch_release_ratio < max(0.0, min(1.0, cfg.prefetch_min_completion)):
            preview_types = []
            prefetch_budget = 0
            preview_depth_by_type = {}
        elif rounds_left <= max(0, int(cfg.endgame_disable_prefetch_rounds)):
            preview_types = []
            prefetch_budget = 0
            preview_depth_by_type = {}
        elif future_mode:
            preview_types, preview_depth_by_type = _future_prefetch_types(
                state=state,
                order_forecast=order_forecast or {},
                active_order_index=int(active_order_index),
                lookahead_orders=cfg.lookahead_orders,
            )
            if preview_types:
                prefetch_budget = max(
                    0,
                    remaining_inventory_slots
                    - remaining_active_items
                    - max(0, int(cfg.prefetch_spare_slots)),
                )
                prefetch_budget += max(0, int(cfg.future_prefetch_bonus))
                prefetch_budget = min(prefetch_budget, remaining_inventory_slots)
        elif should_prefetch_preview(
            state,
            commitment_mode=demand_mode,
            commit_radius=demand_commit_radius,
            preview_safety_slots=demand_preview_safety_slots,
        ):
            prefetch_budget = max(
                0,
                remaining_inventory_slots
                - remaining_active_items
                - max(0, int(cfg.prefetch_spare_slots)),
            )
            if prefetch_budget > 0:
                preview_types = compute_preview_items(
                    state,
                    commitment_mode=demand_mode,
                    commit_radius=demand_commit_radius,
                )

    if bool(cfg.pipeline_budget_enabled):
        prefetch_budget = min(int(prefetch_budget), int(preview_preload_budget_limit))
        if pipeline_mode == "build":
            preview_types = []
            prefetch_budget = 0

    available_items: list[ItemInfo] = sorted(state.items, key=lambda item: item.id)
    assigned_item_ids: set[str] = set()
    assigned_pickup_positions: set[tuple[int, int]] = set()
    blocked_set = set(item_blocked)
    dropoff_dist_map = bfs_distance_map(grid, drop_off, blocked=blocked_set)
    pickup_cache: dict[str, list[tuple[int, int]]] = {
        item.id: find_all_pickup_positions(grid, item.pos.as_tuple())
        for item in available_items
    }
    known_supply = known_supply_by_type or {}
    bot_count = max(1, len(state.bots))
    bot_index_by_id = {
        bot.id: idx
        for idx, bot in enumerate(sorted(state.bots, key=lambda row: row.id))
    }
    pickup_columns = _pickup_zone_columns(pickup_cache)
    dropoff_zone_now = float(
        sum(
            1
            for bot in state.bots
            if abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1]) <= 2
        )
    ) / float(bot_count)
    corridor_density_now = float(
        sum(1 for bot in state.bots if len(grid.neighbors(bot.pos.x, bot.pos.y)) <= 2)
    ) / float(bot_count)
    etadlc_converter_floor_target = 0
    etadlc_local_harvest_bot_ids: set[int] = set()
    active_cargo_bots = sum(1 for bot in state.bots if items_matching_active(bot, state))
    if bool(cfg.etadlc_enabled) and delivered_only_remaining_active_items > 0:
        floor_min = max(1, int(cfg.etadlc_converter_floor_min))
        floor_tail = max(floor_min, int(cfg.etadlc_converter_floor_tail))
        etadlc_converter_floor_target = floor_tail if etadlc_tail_open else floor_min
        nearest_drop_bots = sorted(
            state.bots,
            key=lambda bot: (
                dropoff_dist_map.get(bot.pos.as_tuple(), 999999),
                bot.id,
            ),
        )
        for bot in nearest_drop_bots:
            if len(etadlc_local_harvest_bot_ids) >= etadlc_converter_floor_target:
                break
            if bot.inventory:
                continue
            etadlc_local_harvest_bot_ids.add(int(bot.id))

    critical_overlay_reliable_committed_active_cargo = 0
    critical_overlay_reliable_active_cargo_bots = 0
    if bool(cfg.critical_dispatch_overlay_enabled) and delivered_only_counter:
        reliable_need = Counter(delivered_only_counter)
        reliable_max_dropoff_dist = max(1, int(cfg.critical_dispatch_reliable_max_dropoff_dist))
        reliable_min_matching_ratio = max(
            0.0,
            min(1.0, float(cfg.critical_dispatch_reliable_min_matching_ratio)),
        )
        for bot in sorted(state.bots, key=lambda row: row.id):
            inventory = [str(item_type) for item_type in bot.inventory]
            if not inventory:
                continue
            matching_items: list[str] = []
            tentative_need = Counter(reliable_need)
            for item_type in inventory:
                if tentative_need.get(item_type, 0) > 0:
                    matching_items.append(item_type)
                    tentative_need[item_type] -= 1
            if not matching_items:
                continue
            dist_to_drop = dropoff_dist_map.get(bot.pos.as_tuple(), 999999)
            matching_ratio = float(len(matching_items)) / float(max(1, len(inventory)))
            if dist_to_drop > reliable_max_dropoff_dist:
                continue
            if matching_ratio < reliable_min_matching_ratio:
                continue
            critical_overlay_reliable_active_cargo_bots += 1
            for item_type in matching_items:
                if reliable_need.get(item_type, 0) <= 0:
                    continue
                reliable_need[item_type] -= 1
                critical_overlay_reliable_committed_active_cargo += 1
            if sum(max(0, int(v)) for v in reliable_need.values()) <= 0:
                break

    critical_dispatch_converter_floor_target = 0
    critical_overlay_active_secured = bool(
        delivered_only_remaining_active_items <= 0
        or (
            delivered_only_remaining_active_items > 0
            and critical_overlay_reliable_committed_active_cargo >= delivered_only_remaining_active_items
            and critical_overlay_reliable_active_cargo_bots > 0
        )
    )
    critical_overlay_true_critical = bool(critical_overlay_tail_open and not critical_overlay_active_secured)
    critical_overlay_focus_active = bool(
        int(state.active_order_index or 0) <= max(0, int(cfg.critical_dispatch_focus_order_index_max))
        or critical_overlay_true_critical
    )
    if bool(cfg.critical_dispatch_overlay_enabled):
        if active_cargo_bots > 0 and critical_overlay_focus_active:
            critical_dispatch_converter_floor_target = 1
        if critical_overlay_focus_active and (
            critical_overlay_tail_open
            or (
                delivered_only_remaining_active_items > 0
                and critical_overlay_reliable_committed_active_cargo >= delivered_only_remaining_active_items
                and critical_overlay_reliable_active_cargo_bots > 0
            )
        ):
            critical_dispatch_converter_floor_target = max(critical_dispatch_converter_floor_target, 2)

    if bool(cfg.etadlc_enabled) and not etadlc_active_secured:
        preview_types = []
        prefetch_budget = 0
        strict_prefetch_block = True
    if (
        bool(cfg.critical_dispatch_overlay_enabled)
        and bool(cfg.critical_dispatch_preview_block_when_unsecured)
        and critical_overlay_focus_active
        and critical_overlay_tail_open
        and not critical_overlay_active_secured
    ):
        preview_types = []
        prefetch_budget = 0
        strict_prefetch_block = True

    etadlc_type_anchor_to_drop: dict[str, tuple[int, int]] = {}
    if bool(cfg.etadlc_enabled):
        target_types = set(needed_types) if needed_types else set(delivered_only_needed_types)
        for item_type in target_types:
            shelves = sorted(known_supply.get(str(item_type), set()))
            if not shelves:
                continue
            etadlc_type_anchor_to_drop[str(item_type)] = min(
                shelves,
                key=lambda pos: abs(int(pos[0]) - drop_off[0]) + abs(int(pos[1]) - drop_off[1]),
            )
    critical_dispatch_types: set[str] = set()
    critical_dispatch_anchor_by_type: dict[str, tuple[int, int]] = {}
    if bool(cfg.critical_dispatch_overlay_enabled):
        if delivered_only_remaining_active_items > 0:
            critical_counter = Counter(delivered_only_counter)
            if critical_overlay_tail_open:
                critical_dispatch_types = set(critical_counter.keys())
            elif critical_counter and bool(cfg.critical_dispatch_non_tail_enabled):
                non_tail_type_limit = max(1, int(cfg.critical_dispatch_non_tail_type_limit))
                scarcity_weight = max(0.0, float(cfg.critical_dispatch_scarcity_weight))
                ranked_types = sorted(
                    critical_counter.items(),
                    key=lambda row: (
                        -(
                            float(int(row[1]))
                            + scarcity_weight
                            * (
                                10.0
                                / float(max(1, len(known_supply.get(str(row[0]), set()))))
                            )
                        ),
                        row[0],
                    ),
                )
                critical_dispatch_types = {
                    str(item_type)
                    for item_type, _count in ranked_types[:non_tail_type_limit]
                }
        for item_type in sorted(critical_dispatch_types):
            shelves = sorted(known_supply.get(str(item_type), set()))
            if not shelves:
                continue
            critical_dispatch_anchor_by_type[str(item_type)] = min(
                shelves,
                key=lambda pos: abs(int(pos[0]) - drop_off[0]) + abs(int(pos[1]) - drop_off[1]),
            )

    def _overlay_payload_value_for_bot(bot) -> float:
        if not bool(cfg.critical_dispatch_overlay_enabled):
            return 0.0
        matching_now = [str(item_type) for item_type in items_matching_active(bot, state)]
        matching_count = len(matching_now)
        if matching_count <= 0:
            return 0.0
        distinct_matching = {str(item_type) for item_type in matching_now}
        closes_active = bool(
            delivered_only_remaining_active_items > 0
            and matching_count >= delivered_only_remaining_active_items
        )
        last_type_hits = sum(
            1
            for item_type in distinct_matching
            if delivered_only_counter.get(item_type, 0) == 1
        )
        payload_score = float(matching_count)
        payload_score += max(0.0, float(cfg.critical_dispatch_converter_payload_weight)) * float(
            max(0, matching_count - 1)
        )
        if closes_active:
            payload_score += max(0.0, float(cfg.critical_dispatch_payload_close_bonus))
        if last_type_hits > 0:
            payload_score += max(0.0, float(cfg.critical_dispatch_payload_last_type_bonus)) * float(
                last_type_hits
            )
        return float(payload_score)
    transition_stash_active = False
    transition_finisher_ids: set[int] = set()
    transition_stasher_ids: set[int] = set()
    if bool(cfg.transition_stash_enabled) and preview_types and not bool(cfg.pipeline_budget_enabled):
        transition_release_ratio = prefetch_release_ratio
        if bool(cfg.prefetch_release_use_delivered_completion):
            transition_stash_active = (
                remaining_active_items <= max(0, int(cfg.transition_stash_remaining_items))
                or transition_release_ratio
                >= max(0.0, min(1.0, float(cfg.transition_stash_completion_ratio)))
            )
        else:
            transition_stash_active = (
                active_covered_by_carry
                or remaining_active_items <= max(0, int(cfg.transition_stash_remaining_items))
                or transition_release_ratio
                >= max(0.0, min(1.0, float(cfg.transition_stash_completion_ratio)))
            )
        if transition_stash_active:
            finisher_limit = max(1, int(cfg.transition_stash_finisher_count))
            finisher_candidates: list[tuple[tuple, int]] = []
            for bot in sorted(state.bots, key=lambda cur: cur.id):
                matching_count = len(items_matching_active(bot, state))
                if matching_count <= 0:
                    continue
                finisher_candidates.append(
                    (
                        (
                            dropoff_dist_map.get(bot.pos.as_tuple(), 999999),
                            -matching_count,
                            bot.id,
                        ),
                        int(bot.id),
                    )
                )
            finisher_candidates.sort(key=lambda row: row[0])
            transition_finisher_ids = {
                bot_id
                for _priority, bot_id in finisher_candidates[:finisher_limit]
            }
            transition_stash_active = bool(transition_finisher_ids)
            if transition_stash_active:
                stash_radius = max(1, int(cfg.dropoff_stop_line_radius))
                for bot in state.bots:
                    if bot.id in transition_finisher_ids:
                        continue
                    if items_matching_active(bot, state):
                        continue
                    if abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1]) <= stash_radius:
                        continue
                    transition_stasher_ids.add(int(bot.id))

    unassigned_bots = sorted(state.bots, key=lambda b: b.id)

    # Phase 1: bots at drop-off with matching inventory deliver immediately.
    still_unassigned = []
    for bot in unassigned_bots:
        if bot.pos.as_tuple() == drop_off and items_matching_active(bot, state):
            assignments[bot.id] = Assignment(
                target_type="deliver",
                drop_off=drop_off,
                source="deliver_dropoff_ready",
            )
        else:
            still_unassigned.append(bot)
    unassigned_bots = still_unassigned

    # Phase 2: full inventory + matching items should deliver.
    max_deliverers = int(cfg.max_concurrent_deliverers)
    if cfg.adaptive_deliver_queue:
        q_min = max(1, int(cfg.deliver_queue_min))
        q_max = max(q_min, int(cfg.deliver_queue_max))
        if rounds_left <= max(20, int(cfg.endgame_force_deliver_rounds)):
            max_deliverers = q_max
        elif remaining_active_items <= 2:
            max_deliverers = q_max
        elif remaining_active_items >= 4 and completion_ratio < 0.5:
            max_deliverers = q_min
        else:
            max_deliverers = max(q_min, min(q_max, max_deliverers))
    if bool(cfg.pipeline_budget_enabled):
        max_deliverers = max(1, int(delivery_conversion_budget))
    if bool(cfg.etadlc_enabled):
        max_deliverers = max(max_deliverers, int(etadlc_converter_floor_target))
    if bool(cfg.critical_dispatch_overlay_enabled):
        max_deliverers = max(max_deliverers, int(critical_dispatch_converter_floor_target))
    if max_deliverers <= 0:
        max_deliverers = len(unassigned_bots) + 3
    deliver_slots_left = max(0, max_deliverers - len(assignments))
    deliver_candidates: list[tuple[tuple, object, float]] = []
    still_unassigned = []
    inventory_locked = remaining_inventory_slots == 0 and remaining_active_items > 0
    for bot in unassigned_bots:
        if bot.id in defer_deliver_ids:
            still_unassigned.append(bot)
            continue
        matching = items_matching_active(bot, state)
        matching_count = len(matching)
        overlay_payload_score = _overlay_payload_value_for_bot(bot)
        dist_to_drop = dropoff_dist_map.get(bot.pos.as_tuple(), 999999)
        deliver_reason = -1
        if len(bot.inventory) >= 3:
            if matching_count > 0:
                deliver_reason = 0
            elif cfg.force_dropoff_for_full_nonmatching:
                if cfg.avoid_dropoff_block_when_matching and any_bot_has_matching:
                    still_unassigned.append(bot)
                else:
                    deliver_reason = 3
            elif inventory_locked:
                if cfg.avoid_dropoff_block_when_matching and any_bot_has_matching:
                    still_unassigned.append(bot)
                else:
                    # All inventory slots are saturated while active order is incomplete.
                    # Force unloading to break deadlock on non-matching cargo.
                    deliver_reason = 3
            else:
                still_unassigned.append(bot)
        elif matching_count > 0 and cfg.always_deliver_matching:
            deliver_reason = 1
        elif (
            matching_count > 0
            and bool(cfg.etadlc_enabled)
            and delivered_only_remaining_active_items > 0
        ):
            deliver_reason = 1
        elif (
            matching_count > 0
            and float(cfg.carry_home_bias_weight) > 0.0
            and dist_to_drop >= max(2, int(round(8.0 / float(cfg.carry_home_bias_weight))))
        ):
            # If a bot is carrying active-matching cargo and drifts far from drop-off,
            # bias it toward returning home so other bots can continue nearby pickups.
            deliver_reason = 1
        elif matching_count > 0 and completion_ratio >= cfg.dropoff_completion_threshold:
            deliver_reason = 1
        elif (
            matching_count > 0
            and rounds_left <= max(0, int(cfg.endgame_force_deliver_rounds))
        ):
            deliver_reason = 1
        elif (
            matching_count >= max(1, int(cfg.early_deliver_matching_count))
            and len(bot.inventory) >= max(1, int(cfg.early_deliver_inventory_threshold))
            and cfg.early_deliver_matching_count > 0
        ):
            deliver_reason = 2
        else:
            still_unassigned.append(bot)
        if deliver_reason >= 0:
            soft_delivery_priority = 0.0
            if bool(cfg.soft_pipeline_budget_enabled) and matching_count > 0:
                soft_delivery_priority = soft_delivery_conversion_bonus
                if (
                    soft_pipeline_mode == "build"
                    and delivered_only_remaining_active_items > soft_secure_threshold
                ):
                    soft_delivery_priority *= 0.5
            deliver_candidates.append(
                (
                    (
                        deliver_reason,
                        -soft_delivery_priority,
                        -overlay_payload_score,
                        dist_to_drop,
                        -matching_count,
                        -len(bot.inventory),
                        bot.id,
                    )
                    if bool(cfg.critical_dispatch_overlay_enabled)
                    else (
                        deliver_reason,
                        -soft_delivery_priority,
                        dist_to_drop,
                        -matching_count,
                        -len(bot.inventory),
                        bot.id,
                    ),
                    bot,
                    float(overlay_payload_score),
                )
            )
    deliver_candidates.sort(key=lambda row: row[0])
    for _priority, bot, overlay_payload_score in deliver_candidates:
        if deliver_slots_left <= 0:
            still_unassigned.append(bot)
            continue
        deliver_source = "deliver_priority"
        if bool(cfg.etadlc_enabled) and etadlc_converter_floor_target > 0:
            deliver_source = "deliver_priority_etadlc_floor"
        elif (
            bool(cfg.critical_dispatch_overlay_enabled)
            and critical_dispatch_converter_floor_target > 0
        ):
            if overlay_payload_score >= 2.0:
                deliver_source = "deliver_priority_critical_overlay_floor_payload"
            else:
                deliver_source = "deliver_priority_critical_overlay_floor"
        assignments[bot.id] = Assignment(
            target_type="deliver",
            drop_off=drop_off,
            source=deliver_source,
        )
        deliver_slots_left -= 1
    unassigned_bots = still_unassigned
    task_pool_critical_bot_ids: set[int] = set()
    task_pool_critical_fallback_bot_ids: set[int] = set()
    task_pool_needed_types: list[str] = list(needed_types)
    if not task_pool_needed_types and delivered_only_needed_types:
        # Keep a completion-critical view even when committed-mode demand appears covered.
        task_pool_needed_types = list(delivered_only_needed_types)
    if (
        bool(cfg.task_pool_admission_enabled)
        and unassigned_bots
        and delivered_only_remaining_active_items > 0
        and task_pool_needed_types
    ):
        critical_min = max(0, int(cfg.task_pool_critical_min_bots))
        critical_max = max(critical_min, int(cfg.task_pool_critical_max_bots))
        tail_boost = max(0, int(cfg.task_pool_tail_boost_bots))
        preview_reserve = max(0, int(cfg.task_pool_preview_reserve_bots))
        active_missing_distinct = len({item_type for item_type in task_pool_needed_types})
        critical_target = delivered_only_remaining_active_items + active_missing_distinct
        critical_target = max(critical_min, min(critical_target, critical_max))
        if delivered_only_remaining_active_items <= 2:
            critical_target += tail_boost
        if pipeline_active_secured:
            critical_target = max(critical_min, critical_target - 1)
        if preview_types and len(unassigned_bots) > preview_reserve:
            critical_target = min(critical_target, len(unassigned_bots) - preview_reserve)
        critical_target = max(0, min(critical_target, len(unassigned_bots)))
        if critical_target > 0:
            task_pool_active_types = set(task_pool_needed_types)
            task_pool_pickups: list[tuple[int, int]] = []
            for item in available_items:
                if item.id in blocked_ids:
                    continue
                if item.type not in task_pool_active_types:
                    continue
                task_pool_pickups.extend(pickup_cache.get(item.id, []))
            bot_priorities: list[tuple[tuple, int]] = []
            for bot in sorted(unassigned_bots, key=lambda row: row.id):
                bpos = bot.pos.as_tuple()
                if task_pool_pickups:
                    best_dist = min(
                        abs(bpos[0] - pickup[0]) + abs(bpos[1] - pickup[1])
                        for pickup in task_pool_pickups
                    )
                else:
                    best_dist = 999999
                bot_priorities.append(
                    (
                        (
                            0 if items_matching_active(bot, state) else 1,
                            best_dist,
                            bot.id,
                        ),
                        int(bot.id),
                    )
                )
            bot_priorities.sort(key=lambda row: row[0])
            task_pool_critical_bot_ids = {
                bot_id for _priority, bot_id in bot_priorities[:critical_target]
            }

    critical_dispatch_bot_ids: set[int] = set()
    if (
        bool(cfg.critical_dispatch_overlay_enabled)
        and unassigned_bots
        and critical_dispatch_types
        and critical_overlay_focus_active
    ):
        dispatch_slots = max(1, int(cfg.critical_dispatch_max_slots))
        dispatch_slots = min(dispatch_slots, len(unassigned_bots))
        if not critical_overlay_tail_open:
            dispatch_slots = min(dispatch_slots, 1)
        throughput_reserve = max(0, int(cfg.critical_dispatch_throughput_reserve_bots))
        if throughput_reserve > 0:
            max_by_reserve = max(1, len(unassigned_bots) - throughput_reserve)
            dispatch_slots = min(dispatch_slots, max_by_reserve)
        if dispatch_slots > 0:
            priority_rows: list[tuple[tuple[float, float, int], int]] = []
            for bot in sorted(unassigned_bots, key=lambda row: row.id):
                bpos = bot.pos.as_tuple()
                dist_map = bfs_distance_map(grid, bpos, blocked=blocked_set)
                best_eta = float(999999)
                payload_priority = _overlay_payload_value_for_bot(bot)
                if (
                    delivered_only_remaining_active_items > 0
                    and len(items_matching_active(bot, state)) >= delivered_only_remaining_active_items
                ):
                    payload_priority += max(0.0, float(cfg.critical_dispatch_payload_close_bonus))
                for item_type in sorted(critical_dispatch_types):
                    anchor = critical_dispatch_anchor_by_type.get(item_type)
                    if anchor is None:
                        continue
                    for pickup_pos in find_all_pickup_positions(grid, anchor):
                        d_pick = dist_map.get(pickup_pos, 999999)
                        d_drop = dropoff_dist_map.get(pickup_pos, 999999)
                        if d_pick >= 999999 or d_drop >= 999999:
                            continue
                        eta = float(d_pick) + float(d_drop)
                        if eta < best_eta:
                            best_eta = eta
                priority_rows.append(((-payload_priority, best_eta, int(bot.id)), int(bot.id)))
            priority_rows.sort(key=lambda row: row[0])
            critical_dispatch_bot_ids = {
                bot_id for _priority, bot_id in priority_rows[:dispatch_slots]
            }

    def _candidates_for_bot(
        bot,
        *,
        cur_needed: list[str],
        cur_preview: list[str],
        cur_prefetch_budget: int,
    ) -> list[_BotCandidate]:
        if len(bot.inventory) >= 3:
            return []

        bpos = bot.pos.as_tuple()
        dist_map = bfs_distance_map(grid, bpos, blocked=blocked_set)
        is_transition_stasher = transition_stash_active and bot.id in transition_stasher_ids
        critical_dispatch_now = bool(cfg.critical_dispatch_overlay_enabled) and int(bot.id) in critical_dispatch_bot_ids
        local_prefetch_budget = cur_prefetch_budget
        if is_transition_stasher and cur_preview:
            local_prefetch_budget = max(local_prefetch_budget, 1)
        active_matching_now = len(items_matching_active(bot, state))
        nonmatching_now = max(0, len(bot.inventory) - active_matching_now)
        dist_bot_drop = dropoff_dist_map.get(bpos, 999999)
        needed_set = set(cur_needed)
        preview_set = set(cur_preview)
        two_step_cache: dict[tuple[int, int], dict[tuple[int, int], int]] = {}
        has_reachable_active = False
        if strict_active_now and needed_set:
            for cur_item in available_items:
                if cur_item.id in assigned_item_ids or cur_item.type not in needed_set:
                    continue
                for cur_pickup in pickup_cache[cur_item.id]:
                    if dist_map.get(cur_pickup, 999999) >= 999999:
                        continue
                    if dropoff_dist_map.get(cur_pickup, 999999) >= 999999:
                        continue
                    has_reachable_active = True
                    break
                if has_reachable_active:
                    break

        out: list[_BotCandidate] = []
        for item in available_items:
            if item.id in blocked_ids:
                continue

            target_type: str | None = None
            utility = 0.0
            critical_payload_bonus_applied = False
            if item.type in needed_set:
                target_type = "pick_item"
                utility = cfg.active_weight
                scarce_threshold = max(1, int(cfg.active_scarce_type_threshold))
                supply_count = len(known_supply.get(str(item.type), set()))
                if supply_count <= scarce_threshold:
                    scarcity_gap = float((scarce_threshold - supply_count) + 1)
                    utility += max(0.0, float(cfg.active_scarce_type_bonus)) * scarcity_gap
                if critical_dispatch_now and item.type not in critical_dispatch_types:
                    continue
            elif item.type in preview_set and local_prefetch_budget > 0:
                if critical_dispatch_now:
                    continue
                if strict_prefetch_block and not is_transition_stasher:
                    if bool(cfg.prefetch_release_use_delivered_completion) or has_reachable_active:
                        continue
                if nonmatching_now >= max(0, int(cfg.prefetch_nonmatching_cap)):
                    continue
                target_type = "pre_pick"
                depth = max(1, preview_depth_by_type.get(item.type, 1))
                if future_mode:
                    decay = max(0.0, float(cfg.future_depth_decay))
                    utility = cfg.preview_weight / float(depth**decay if decay > 0.0 else 1.0)
                    outstanding = cur_preview.count(item.type)
                    if cfg.future_count_weight > 0.0 and outstanding > 0:
                        utility += float(cfg.future_count_weight) * float(outstanding)
                else:
                    utility = cfg.preview_weight
                if is_transition_stasher:
                    utility += max(0.0, float(cfg.transition_stash_preview_bonus))
            if target_type is None:
                continue
            if item.id in assigned_item_ids:
                if target_type != "pick_item":
                    continue
                needed_count_for_type = cur_needed.count(item.type)
                if needed_count_for_type <= 0:
                    continue
                if not cfg.allow_same_shelf_for_same_type:
                    has_other_unassigned_same_type = any(
                        other.type == item.type and other.id != item.id and other.id not in assigned_item_ids
                        for other in available_items
                    )
                    allow_duplicate_override = False
                    if (
                        has_other_unassigned_same_type
                        and bool(cfg.allow_same_shelf_for_active_duplicates)
                        and needed_counter.get(item.type, 0) >= 2
                    ):
                        current_best_dist = min(
                            (
                                dist_map.get(pp, 999999)
                                for pp in pickup_cache.get(item.id, [])
                            ),
                            default=999999,
                        )
                        alt_best_dist = 999999
                        for other in available_items:
                            if other.id == item.id or other.id in assigned_item_ids:
                                continue
                            if other.type != item.type:
                                continue
                            for other_pp in pickup_cache.get(other.id, []):
                                alt_best_dist = min(alt_best_dist, dist_map.get(other_pp, 999999))
                        min_gap = max(0, int(cfg.active_duplicate_same_shelf_min_gap))
                        if current_best_dist < 999999 and (
                            alt_best_dist >= 999999
                            or (alt_best_dist - current_best_dist) >= min_gap
                        ):
                            allow_duplicate_override = True
                    if has_other_unassigned_same_type and not allow_duplicate_override:
                        # Prefer spreading bots across distinct shelves for this type when possible.
                        continue

            for pickup_pos in pickup_cache[item.id]:
                if (
                    pickup_pos in assigned_pickup_positions
                    and any(pp not in assigned_pickup_positions for pp in pickup_cache[item.id])
                ):
                    # Keep bots from chasing the same pickup lane when a safe alternative exists.
                    continue
                dist = dist_map.get(pickup_pos, 999999)
                if dist >= 999999:
                    continue
                dist_to_drop = dropoff_dist_map.get(pickup_pos, 999999)
                if dist_to_drop >= 999999:
                    continue
                if (
                    bool(cfg.etadlc_enabled)
                    and int(bot.id) in etadlc_local_harvest_bot_ids
                    and target_type == "pick_item"
                    and dist_to_drop > max(1, int(cfg.etadlc_local_courier_harvest_radius))
                ):
                    continue
                congestion = float(
                    sum(
                        1
                        for other in state.bots
                        if other.id != bot.id
                        and abs(other.pos.x - pickup_pos[0]) + abs(other.pos.y - pickup_pos[1]) <= 2
                    )
                )
                pickup_conflict = 1.0 if pickup_pos in assigned_pickup_positions else 0.0
                nearby_conflicts = float(
                    sum(
                        1
                        for pp in assigned_pickup_positions
                        if abs(pp[0] - pickup_pos[0]) + abs(pp[1] - pickup_pos[1]) == 1
                    )
                )
                collision_risk = pickup_conflict + 0.5 * nearby_conflicts
                zone_center_x = _zone_center_x(
                    bot_index=bot_index_by_id.get(bot.id, 0),
                    bot_count=bot_count,
                    pickup_columns=pickup_columns,
                    grid_width=grid.width,
                )
                zone_penalty = float(abs(pickup_pos[0] - zone_center_x))
                replan_penalty = 0.0
                if sticky_targets and sticky_targets.get(bot.id) not in (None, item.id):
                    replan_penalty = max(0.0, float(cfg.hysteresis_penalty))
                sticky_bonus = 0.0
                if sticky_targets and sticky_targets.get(bot.id) == item.id:
                    sticky_bonus = max(0.0, float(cfg.sticky_target_bonus))
                two_step_bonus = 0.0
                legacy_chain_bonus = 0.0
                spare_slots = max(0, 3 - len(bot.inventory))
                if (
                    (cfg.two_step_trip_weight > 0.0 or cfg.trip_chain_bonus_weight > 0.0)
                    and spare_slots >= 2
                    and needed_set
                    and target_type == "pick_item"
                ):
                    dist_from_pick = two_step_cache.get(pickup_pos)
                    if dist_from_pick is None:
                        dist_from_pick = bfs_distance_map(grid, pickup_pos, blocked=blocked_set)
                        two_step_cache[pickup_pos] = dist_from_pick
                    best_gain = 0.0
                    remaining_before = Counter(cur_needed)
                    one_step_need = Counter(remaining_before)
                    if one_step_need.get(item.type, 0) > 0:
                        one_step_need[item.type] -= 1
                    outstanding_after_one = int(sum(max(0, cnt) for cnt in one_step_need.values()))
                    for nxt in available_items:
                        if nxt.id == item.id or nxt.id in blocked_ids:
                            continue
                        if nxt.id in assigned_item_ids:
                            continue
                        if nxt.type not in needed_set:
                            continue
                        for nxt_pick in pickup_cache[nxt.id]:
                            d12 = dist_from_pick.get(nxt_pick, 999999)
                            if d12 >= 999999:
                                continue
                            d2drop = dropoff_dist_map.get(nxt_pick, 999999)
                            if d2drop >= 999999:
                                continue
                            d_drop_to_second = dropoff_dist_map.get(nxt_pick, 999999)
                            if d_drop_to_second >= 999999:
                                continue
                            plan_a = float(dist) + float(dist_to_drop) + float(d_drop_to_second) + float(d2drop)
                            plan_b = float(dist) + float(d12) + float(d2drop)
                            travel_gain = plan_a - plan_b
                            extra_steps = max(0.0, float(d12) + float(d2drop) - float(dist_to_drop))
                            two_step_need = Counter(one_step_need)
                            if two_step_need.get(nxt.type, 0) > 0:
                                two_step_need[nxt.type] -= 1
                            outstanding_after_two = int(sum(max(0, cnt) for cnt in two_step_need.values()))
                            completion_bonus = 0.0
                            if outstanding_after_one > 0 and outstanding_after_two <= 0:
                                completion_bonus = float(ORDER_BONUS) * float(cfg.two_step_order_bonus_weight)
                            gain = travel_gain + completion_bonus
                            max_extra = max(0, int(cfg.two_step_max_extra_steps))
                            # Legacy chain bonus only rewards clearly short, travel-saving
                            # follow-up pickups; it does not use completion-order bonuses.
                            if extra_steps <= 2.0 and travel_gain > legacy_chain_bonus:
                                legacy_chain_bonus = float(travel_gain)
                            if (
                                outstanding_after_one <= 0
                                and extra_steps > float(max(0, int(cfg.two_step_completion_delay_threshold)))
                            ):
                                continue
                            if extra_steps > float(max_extra) and gain <= 0.0:
                                continue
                            if gain > best_gain:
                                best_gain = gain
                    min_gain = max(0, int(cfg.two_step_trip_min_gain))
                    if best_gain >= float(min_gain):
                        two_step_bonus = float(best_gain)
                pickup_degree = len(grid.neighbors(pickup_pos[0], pickup_pos[1]))
                predicted_dropoff_penalty = max(0.0, dropoff_zone_now - 0.34) * max(0.0, 3.0 - float(dist_to_drop))
                predicted_corridor_penalty = corridor_density_now * (1.0 if pickup_degree <= 2 else 0.0)
                utility_score = (
                    cfg.urgency_weight * utility
                    + sticky_bonus
                    + float(cfg.trip_chain_bonus_weight) * legacy_chain_bonus
                    + float(cfg.two_step_trip_weight) * two_step_bonus
                    - cfg.dist_weight * float(dist)
                    - cfg.dropoff_dist_weight * float(dist_to_drop)
                    - cfg.congestion_weight * congestion
                    - cfg.collision_risk_weight * collision_risk
                    - cfg.zone_penalty_weight * zone_penalty
                    - cfg.replan_penalty_weight * replan_penalty
                    - float(cfg.predicted_dropoff_density_weight) * predicted_dropoff_penalty
                    - float(cfg.predicted_corridor_density_weight) * predicted_corridor_penalty
                )
                if bool(cfg.etadlc_enabled) and target_type == "pick_item":
                    eta_to_score = float(dist) + float(dist_to_drop)
                    utility_score -= max(0.0, float(cfg.etadlc_retrieval_eta_weight)) * eta_to_score
                    item_shelf_pos = item.pos.as_tuple()
                    anchor = etadlc_type_anchor_to_drop.get(str(item.type))
                    if anchor is not None and item_shelf_pos == anchor:
                        utility_score += max(0.0, float(cfg.etadlc_known_shelf_target_bonus))
                if bool(cfg.critical_dispatch_overlay_enabled) and critical_dispatch_now and target_type == "pick_item":
                    eta_to_score = float(dist) + float(dist_to_drop)
                    utility_score -= max(0.0, float(cfg.critical_dispatch_eta_weight)) * eta_to_score
                    dispatch_anchor = critical_dispatch_anchor_by_type.get(str(item.type))
                    if dispatch_anchor is not None and item.pos.as_tuple() == dispatch_anchor:
                        utility_score += max(0.0, float(cfg.critical_dispatch_known_shelf_bonus))
                    payload_bonus = 0.0
                    remaining_for_type = int(delivered_only_counter.get(str(item.type), 0))
                    if (
                        delivered_only_remaining_active_items > 0
                        and remaining_for_type > 0
                        and delivered_only_remaining_active_items <= 1
                    ):
                        payload_bonus += max(0.0, float(cfg.critical_dispatch_payload_close_bonus))
                    if (
                        active_missing_distinct <= 2
                        and remaining_for_type == 1
                    ):
                        payload_bonus += max(0.0, float(cfg.critical_dispatch_payload_last_type_bonus))
                    if active_matching_now >= 1 or two_step_bonus > 0.0:
                        payload_bonus += max(0.0, float(cfg.critical_dispatch_payload_two_item_bonus))
                    if payload_bonus > 0.0:
                        utility_score += payload_bonus
                        critical_payload_bonus_applied = True
                if active_matching_now > 0 and dist_bot_drop < 999999:
                    utility_score -= (
                        float(cfg.carry_home_bias_weight)
                        * float(active_matching_now)
                        * (float(dist_bot_drop) / 5.0)
                    )
                if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode != "off":
                    if target_type == "pick_item":
                        utility_score += soft_active_close_bonus
                        if (
                            delivered_only_remaining_active_items > 0
                            and delivered_only_remaining_active_items <= soft_secure_threshold
                            and committed_active_cargo <= 0
                        ):
                            utility_score += 0.5 * soft_active_close_bonus
                    elif target_type == "pre_pick":
                        utility_score -= soft_preview_preload_discount
                        if soft_pipeline_mode == "transition":
                            utility_score += soft_transition_preview_bonus
                if future_mode:
                    if target_type == "pick_item":
                        future_priority = 0
                    else:
                        depth_hint = max(1, preview_depth_by_type.get(item.type, 1))
                        if strict_prefetch_block:
                            if cfg.future_priority_mode == "flat":
                                future_priority = 1
                            else:
                                future_priority = depth_hint
                        else:
                            if cfg.future_priority_mode == "flat":
                                future_priority = 0
                            else:
                                future_priority = max(0, depth_hint - 1)
                    rank = (
                        future_priority,
                        -utility_score,
                        dist,
                        item.type,
                        pickup_pos,
                        bot.id,
                        _seed_rank(rank_seed, bot.id, item.id, pickup_pos),
                    )
                else:
                    rank = (
                        -utility_score,
                        dist,
                        item.type,
                        pickup_pos,
                        bot.id,
                        _seed_rank(rank_seed, bot.id, item.id, pickup_pos),
                    )
                out.append(
                    _BotCandidate(
                        item=item,
                        target_type=target_type,
                        pickup_pos=pickup_pos,
                        rank=rank,
                        utility_score=float(utility_score),
                        critical_payload_bonus_applied=bool(critical_payload_bonus_applied),
                    )
                )

        out.sort(key=lambda cand: cand.rank)
        return out

    def _best_candidate_for_bot(
        bot,
        *,
        cur_needed: list[str],
        cur_preview: list[str],
        cur_prefetch_budget: int,
    ) -> _BotCandidate | None:
        force_active_only = bot.id in force_active_only_ids
        critical_dispatch_force = bool(cfg.critical_dispatch_overlay_enabled) and int(bot.id) in critical_dispatch_bot_ids
        if critical_dispatch_force:
            critical_needed = [item_type for item_type in cur_needed if item_type in critical_dispatch_types]
            forced_options = _candidates_for_bot(
                bot,
                cur_needed=critical_needed,
                cur_preview=[],
                cur_prefetch_budget=0,
            )
            if forced_options:
                return forced_options[0]
        options = _candidates_for_bot(
            bot,
            cur_needed=cur_needed,
            cur_preview=[] if force_active_only else cur_preview,
            cur_prefetch_budget=0 if force_active_only else cur_prefetch_budget,
        )
        if not options:
            return None
        return options[0]

    def _select_diverse_options(options: list[_BotCandidate], depth: int) -> list[_BotCandidate]:
        limit = max(1, int(depth))
        if len(options) <= limit:
            return list(options)
        selected: list[_BotCandidate] = []
        used_pickups: set[tuple[int, int]] = set()
        used_types: set[str] = set()
        for cand in options:
            if len(selected) >= limit:
                break
            if cand.pickup_pos in used_pickups:
                continue
            if cand.item.type in used_types:
                continue
            selected.append(cand)
            used_pickups.add(cand.pickup_pos)
            used_types.add(cand.item.type)
        if len(selected) < limit:
            seen_keys = {(cand.item.id, cand.pickup_pos) for cand in selected}
            for cand in options:
                if len(selected) >= limit:
                    break
                key = (cand.item.id, cand.pickup_pos)
                if key in seen_keys:
                    continue
                selected.append(cand)
                seen_keys.add(key)
        return selected

    # Phase 3: item assignment (greedy or auction).
    if cfg.assignment_strategy == "auction":
        bot_ids = [
            bot.id
            for bot in sorted(
                unassigned_bots,
                key=lambda b: (0 if int(b.id) in critical_dispatch_bot_ids else 1, b.id),
            )
        ]
        options_by_bot: dict[int, list[_BotCandidate]] = {}
        option_depth = max(1, int(cfg.auction_option_depth))
        for bot in sorted(unassigned_bots, key=lambda b: b.id):
            force_active_only = bot.id in force_active_only_ids
            task_pool_force_active = bot.id in task_pool_critical_bot_ids
            options = _candidates_for_bot(
                bot,
                cur_needed=task_pool_needed_types if task_pool_force_active else needed_types,
                cur_preview=[] if (force_active_only or task_pool_force_active) else preview_types,
                cur_prefetch_budget=0 if (force_active_only or task_pool_force_active) else prefetch_budget,
            )
            if task_pool_force_active and not options:
                task_pool_critical_fallback_bot_ids.add(int(bot.id))
                options = _candidates_for_bot(
                    bot,
                    cur_needed=needed_types,
                    cur_preview=[] if force_active_only else preview_types,
                    cur_prefetch_budget=0 if force_active_only else prefetch_budget,
                )
            options_by_bot[bot.id] = _select_diverse_options(options, option_depth)

        needed_counts: dict[str, int] = {}
        for item_type in needed_types:
            needed_counts[item_type] = needed_counts.get(item_type, 0) + 1
        preview_counts: dict[str, int] = {}
        for item_type in preview_types:
            preview_counts[item_type] = preview_counts.get(item_type, 0) + 1

        best_plan: dict[int, _BotCandidate] = {}
        best_score: float | None = None
        best_assigned = -1
        best_key: tuple | None = None

        def _can_use_pickup(bot_id: int, cand: _BotCandidate, used_pickups: set[tuple[int, int]]) -> bool:
            if cand.pickup_pos not in used_pickups:
                return True
            for other in options_by_bot.get(bot_id, []):
                if other.item.id == cand.item.id:
                    continue
                if other.pickup_pos in used_pickups:
                    continue
                return False
            return True

        def _dfs(
            idx: int,
            used_item_ids: set[str],
            used_pickups: set[tuple[int, int]],
            need_left: dict[str, int],
            preview_left: dict[str, int],
            prefetch_left: int,
            total_score: float,
            assigned_count: int,
            plan: dict[int, _BotCandidate],
        ) -> None:
            nonlocal best_plan, best_score, best_assigned, best_key
            if idx >= len(bot_ids):
                key_parts: list[tuple] = []
                for bid in bot_ids:
                    cand = plan.get(bid)
                    if cand is None:
                        key_parts.append(("idle", "", (999, 999)))
                    else:
                        key_parts.append((cand.target_type, cand.item.id, cand.pickup_pos))
                key = tuple(key_parts)
                improved = False
                if best_score is None or total_score > best_score + 1e-9:
                    improved = True
                elif best_score is not None and abs(total_score - best_score) <= 1e-9:
                    if assigned_count > best_assigned:
                        improved = True
                    elif assigned_count == best_assigned and (best_key is None or key < best_key):
                        improved = True
                if improved:
                    best_score = total_score
                    best_assigned = assigned_count
                    best_key = key
                    best_plan = dict(plan)
                return

            bot_id = bot_ids[idx]
            opts = options_by_bot.get(bot_id, [])
            if not opts:
                _dfs(
                    idx + 1,
                    used_item_ids,
                    used_pickups,
                    need_left,
                    preview_left,
                    prefetch_left,
                    total_score,
                    assigned_count,
                    plan,
                )
                return

            if cfg.auction_allow_skip:
                _dfs(
                    idx + 1,
                    used_item_ids,
                    used_pickups,
                    need_left,
                    preview_left,
                    prefetch_left,
                    total_score,
                    assigned_count,
                    plan,
                )

            for cand in opts:
                item_id = cand.item.id
                item_type = cand.item.type
                if item_id in used_item_ids:
                    continue
                if not _can_use_pickup(bot_id, cand, used_pickups):
                    continue

                if cand.target_type == "pick_item":
                    if need_left.get(item_type, 0) <= 0:
                        continue
                elif cand.target_type == "pre_pick":
                    if prefetch_left <= 0:
                        continue
                    if preview_left.get(item_type, 0) <= 0:
                        continue

                next_need = dict(need_left)
                next_preview = dict(preview_left)
                next_prefetch = prefetch_left
                if cand.target_type == "pick_item":
                    next_need[item_type] = next_need.get(item_type, 0) - 1
                elif cand.target_type == "pre_pick":
                    next_preview[item_type] = next_preview.get(item_type, 0) - 1
                    next_prefetch -= 1

                used_item_ids.add(item_id)
                used_pickups.add(cand.pickup_pos)
                plan[bot_id] = cand
                _dfs(
                    idx + 1,
                    used_item_ids,
                    used_pickups,
                    next_need,
                    next_preview,
                    next_prefetch,
                    total_score + cand.utility_score,
                    assigned_count + 1,
                    plan,
                )
                plan.pop(bot_id, None)
                used_pickups.discard(cand.pickup_pos)
                used_item_ids.discard(item_id)

        _dfs(
            0,
            set(assigned_item_ids),
            set(assigned_pickup_positions),
            dict(needed_counts),
            dict(preview_counts),
            max(0, int(prefetch_budget)),
            0.0,
            0,
            {},
        )

        for bot_id, cand in sorted(best_plan.items(), key=lambda kv: kv[0]):
            source = "auction_plan"
            if bool(cfg.task_pool_admission_enabled) and bot_id in task_pool_critical_bot_ids:
                if bot_id in task_pool_critical_fallback_bot_ids or cand.target_type != "pick_item":
                    source = f"{source}_critical_pool_fallback"
                else:
                    source = f"{source}_critical_pool"
            if bool(cfg.pipeline_budget_enabled) and cand.target_type == "pre_pick" and pipeline_mode != "off":
                source = f"auction_plan_pipeline_{pipeline_mode}"
            if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode != "off":
                source = f"{source}_soft_pipeline_{soft_pipeline_mode}"
            if bool(cfg.etadlc_enabled) and cand.target_type == "pick_item":
                source = f"{source}_etadlc_eta"
            if (
                bool(cfg.critical_dispatch_overlay_enabled)
                and bot_id in critical_dispatch_bot_ids
                and cand.target_type == "pick_item"
            ):
                source = f"{source}_critical_dispatch"
                if cand.critical_payload_bonus_applied:
                    source = f"{source}_payload"
            assignments[bot_id] = Assignment(
                target_type=cand.target_type,
                item=cand.item,
                pickup_pos=cand.pickup_pos,
                target_id=cand.item.id,
                source=source,
            )
            assigned_item_ids.add(cand.item.id)
            assigned_pickup_positions.add(cand.pickup_pos)

        unassigned_bots = [bot for bot in unassigned_bots if bot.id not in assignments]
    elif cfg.assignment_strategy == "hungarian":
        active_only_remaining_threshold = max(1, int(cfg.hungarian_active_only_remaining_threshold))
        active_only_distinct_threshold = max(1, int(cfg.hungarian_active_only_distinct_threshold))
        active_only_gate_open = bool(
            cfg.hungarian_active_only_when_needed
            and delivered_only_remaining_active_items > 0
            and (
                delivered_only_remaining_active_items <= active_only_remaining_threshold
                or active_missing_distinct <= active_only_distinct_threshold
            )
        )
        delivered_only_needed_for_active_only = list(delivered_only_needed_types)
        active_open_for_hungarian = delivered_only_remaining_active_items > 0
        preview_discount = max(
            0.0,
            min(1.0, float(cfg.hungarian_preview_utility_discount_when_active_open)),
        )

        def _active_only_needed_types(cur_needed: list[str]) -> list[str]:
            if delivered_only_needed_for_active_only:
                return delivered_only_needed_for_active_only
            return cur_needed

        def _hungarian_effective_utility(cand: _BotCandidate) -> float:
            utility = float(cand.utility_score)
            if active_open_for_hungarian and cand.target_type == "pre_pick":
                utility *= preview_discount
            return utility

        def _filter_hungarian_active_only(
            *,
            options: list[_BotCandidate],
            needed_for_bot: list[str],
        ) -> tuple[list[_BotCandidate], bool]:
            if not active_only_gate_open or not needed_for_bot:
                return options, False
            needed_counter_for_bot = Counter(str(item_type) for item_type in needed_for_bot)
            active_only_options = [
                cand
                for cand in options
                if cand.target_type == "pick_item"
                and needed_counter_for_bot.get(str(cand.item.type), 0) > 0
            ]
            if active_only_options:
                return active_only_options, True
            return options, False

        bot_ids = [
            bot.id
            for bot in sorted(
                unassigned_bots,
                key=lambda b: (0 if int(b.id) in critical_dispatch_bot_ids else 1, b.id),
            )
        ]
        option_depth = max(1, int(cfg.hungarian_option_depth))
        options_by_bot: dict[int, list[_BotCandidate]] = {}
        hungarian_guardrail_fallback = False
        hungarian_active_only_round = False
        for bot in sorted(unassigned_bots, key=lambda b: b.id):
            force_active_only = bot.id in force_active_only_ids
            task_pool_force_active = bot.id in task_pool_critical_bot_ids
            needed_for_bot = task_pool_needed_types if task_pool_force_active else needed_types
            options = _candidates_for_bot(
                bot,
                cur_needed=needed_for_bot,
                cur_preview=[] if (force_active_only or task_pool_force_active) else preview_types,
                cur_prefetch_budget=0 if (force_active_only or task_pool_force_active) else prefetch_budget,
            )
            if task_pool_force_active and not options:
                task_pool_critical_fallback_bot_ids.add(int(bot.id))
                needed_for_bot = needed_types
                options = _candidates_for_bot(
                    bot,
                    cur_needed=needed_for_bot,
                    cur_preview=[] if force_active_only else preview_types,
                    cur_prefetch_budget=0 if force_active_only else prefetch_budget,
                )
            active_only_needed = _active_only_needed_types(needed_for_bot)
            options, active_only_applied = _filter_hungarian_active_only(
                options=options,
                needed_for_bot=active_only_needed,
            )
            if active_only_applied:
                hungarian_active_only_round = True
            options_by_bot[bot.id] = list(options[:option_depth])

        # Build global task universe (shared across bots) + idle dummies.
        task_keys: list[tuple[str, str, tuple[int, int]]] = []
        task_key_to_idx: dict[tuple[str, str, tuple[int, int]], int] = {}
        for bot_id in bot_ids:
            for cand in options_by_bot.get(bot_id, []):
                key = (cand.target_type, str(cand.item.id), cand.pickup_pos)
                if key in task_key_to_idx:
                    continue
                task_key_to_idx[key] = len(task_keys)
                task_keys.append(key)
        for bot_id in bot_ids:
            key = ("idle", f"idle_{bot_id}", (-1, -1))
            task_key_to_idx[key] = len(task_keys)
            task_keys.append(key)

        utility_by_bot_task: dict[tuple[int, int], float] = {}
        candidate_by_bot_task: dict[tuple[int, int], _BotCandidate] = {}
        max_utility = 0.0
        for bot_id in bot_ids:
            for cand in options_by_bot.get(bot_id, []):
                key = (cand.target_type, str(cand.item.id), cand.pickup_pos)
                idx = task_key_to_idx.get(key)
                if idx is None:
                    continue
                u = _hungarian_effective_utility(cand)
                utility_by_bot_task[(bot_id, idx)] = u
                candidate_by_bot_task[(bot_id, idx)] = cand
                if u > max_utility:
                    max_utility = u

        row_count = len(bot_ids)
        col_count = len(task_keys)
        if row_count > 0 and col_count >= row_count:
            impossible_cost = max_utility + 10_000.0
            cost_matrix: list[list[float]] = []
            for bot_id in bot_ids:
                row: list[float] = []
                for task_idx, task_key in enumerate(task_keys):
                    if task_key[0] == "idle":
                        row.append(max_utility + 1.0)
                        continue
                    util = utility_by_bot_task.get((bot_id, task_idx))
                    if util is None:
                        row.append(impossible_cost)
                    else:
                        row.append(max_utility - util)
                cost_matrix.append(row)

            chosen_cols = _hungarian_min_cost(cost_matrix)
        else:
            chosen_cols = [-1] * row_count

        # Constraint repair pass for demand/prefetch quotas.
        need_left: dict[str, int] = {}
        for item_type in needed_types:
            need_left[item_type] = need_left.get(item_type, 0) + 1
        preview_left: dict[str, int] = {}
        for item_type in preview_types:
            preview_left[item_type] = preview_left.get(item_type, 0) + 1
        prefetch_left = max(0, int(prefetch_budget))

        prelim: list[tuple[float, int, _BotCandidate]] = []
        for row_idx, bot_id in enumerate(bot_ids):
            col_idx = chosen_cols[row_idx] if row_idx < len(chosen_cols) else -1
            if col_idx < 0:
                continue
            cand = candidate_by_bot_task.get((bot_id, col_idx))
            if cand is None:
                continue
            prelim.append((_hungarian_effective_utility(cand), bot_id, cand))
        prelim.sort(key=lambda row: (-row[0], row[1]))

        accepted: dict[int, _BotCandidate] = {}
        used_item_ids_local: set[str] = set(assigned_item_ids)
        used_pickups_local: set[tuple[int, int]] = set(assigned_pickup_positions)
        for _util, bot_id, cand in prelim:
            item_id = str(cand.item.id)
            item_type = str(cand.item.type)
            if item_id in used_item_ids_local:
                continue
            if cand.pickup_pos in used_pickups_local:
                continue
            if cand.target_type == "pick_item":
                if need_left.get(item_type, 0) <= 0:
                    continue
            elif cand.target_type == "pre_pick":
                if prefetch_left <= 0:
                    continue
                if preview_left.get(item_type, 0) <= 0:
                    continue
            accepted[bot_id] = cand
            used_item_ids_local.add(item_id)
            used_pickups_local.add(cand.pickup_pos)
            if cand.target_type == "pick_item":
                need_left[item_type] = need_left.get(item_type, 0) - 1
            elif cand.target_type == "pre_pick":
                preview_left[item_type] = preview_left.get(item_type, 0) - 1
                prefetch_left -= 1

        # Fill dropped bots with feasible fallback options.
        for bot in sorted(unassigned_bots, key=lambda b: b.id):
            if bot.id in accepted:
                continue
            chosen = False
            for cand in options_by_bot.get(bot.id, []):
                item_id = str(cand.item.id)
                item_type = str(cand.item.type)
                if item_id in used_item_ids_local:
                    continue
                if cand.pickup_pos in used_pickups_local:
                    continue
                if cand.target_type == "pick_item":
                    if need_left.get(item_type, 0) <= 0:
                        continue
                elif cand.target_type == "pre_pick":
                    if prefetch_left <= 0:
                        continue
                    if preview_left.get(item_type, 0) <= 0:
                        continue
                accepted[bot.id] = cand
                used_item_ids_local.add(item_id)
                used_pickups_local.add(cand.pickup_pos)
                if cand.target_type == "pick_item":
                    need_left[item_type] = need_left.get(item_type, 0) - 1
                elif cand.target_type == "pre_pick":
                    preview_left[item_type] = preview_left.get(item_type, 0) - 1
                    prefetch_left -= 1
                chosen = True
                break

            if chosen or not bool(cfg.hungarian_fallback_to_greedy):
                continue

            # Safety net: use full greedy options for bots left unassigned by Hungarian pruning.
            force_active_only = bot.id in force_active_only_ids
            task_pool_force_active = bot.id in task_pool_critical_bot_ids
            full_options = _candidates_for_bot(
                bot,
                cur_needed=task_pool_needed_types if task_pool_force_active else needed_types,
                cur_preview=[] if (force_active_only or task_pool_force_active) else preview_types,
                cur_prefetch_budget=0 if (force_active_only or task_pool_force_active) else prefetch_budget,
            )
            if task_pool_force_active and not full_options:
                full_options = _candidates_for_bot(
                    bot,
                    cur_needed=needed_types,
                    cur_preview=[] if force_active_only else preview_types,
                    cur_prefetch_budget=0 if force_active_only else prefetch_budget,
                )
            for cand in full_options:
                item_id = str(cand.item.id)
                item_type = str(cand.item.type)
                if item_id in used_item_ids_local:
                    continue
                if cand.pickup_pos in used_pickups_local:
                    continue
                if cand.target_type == "pick_item":
                    if need_left.get(item_type, 0) <= 0:
                        continue
                elif cand.target_type == "pre_pick":
                    if prefetch_left <= 0:
                        continue
                    if preview_left.get(item_type, 0) <= 0:
                        continue
                accepted[bot.id] = cand
                used_item_ids_local.add(item_id)
                used_pickups_local.add(cand.pickup_pos)
                if cand.target_type == "pick_item":
                    need_left[item_type] = need_left.get(item_type, 0) - 1
                elif cand.target_type == "pre_pick":
                    preview_left[item_type] = preview_left.get(item_type, 0) - 1
                    prefetch_left -= 1
                chosen = True
                break
            if (
                not chosen
                and bool(cfg.hungarian_active_only_when_needed)
                and needed_types
                and full_options
            ):
                active_only_needed = _active_only_needed_types(needed_types)
                active_full_options, active_only_applied = _filter_hungarian_active_only(
                    options=list(full_options),
                    needed_for_bot=active_only_needed,
                )
                if active_only_applied:
                    hungarian_active_only_round = True
                for cand in active_full_options:
                    item_id = str(cand.item.id)
                    item_type = str(cand.item.type)
                    if item_id in used_item_ids_local:
                        continue
                    if cand.pickup_pos in used_pickups_local:
                        continue
                    if cand.target_type == "pick_item":
                        if need_left.get(item_type, 0) <= 0:
                            continue
                    elif cand.target_type == "pre_pick":
                        if prefetch_left <= 0:
                            continue
                        if preview_left.get(item_type, 0) <= 0:
                            continue
                    accepted[bot.id] = cand
                    used_item_ids_local.add(item_id)
                    used_pickups_local.add(cand.pickup_pos)
                    if cand.target_type == "pick_item":
                        need_left[item_type] = need_left.get(item_type, 0) - 1
                    elif cand.target_type == "pre_pick":
                        preview_left[item_type] = preview_left.get(item_type, 0) - 1
                        prefetch_left -= 1
                    chosen = True
                    break

        if bool(cfg.hungarian_fallback_to_greedy):
            min_assign = max(0, int(cfg.hungarian_min_assignments))
            if len(accepted) < min_assign:
                accepted = {}
                used_item_ids_local = set(assigned_item_ids)
                used_pickups_local = set(assigned_pickup_positions)
                need_left = {}
                for item_type in needed_types:
                    need_left[item_type] = need_left.get(item_type, 0) + 1
                preview_left = {}
                for item_type in preview_types:
                    preview_left[item_type] = preview_left.get(item_type, 0) + 1
                prefetch_left = max(0, int(prefetch_budget))
                for bot in sorted(
                    unassigned_bots,
                    key=lambda b: (0 if int(b.id) in critical_dispatch_bot_ids else 1, b.id),
                ):
                    force_active_only = bot.id in force_active_only_ids
                    task_pool_force_active = bot.id in task_pool_critical_bot_ids
                    needed_for_bot = task_pool_needed_types if task_pool_force_active else needed_types
                    options = _candidates_for_bot(
                        bot,
                        cur_needed=needed_for_bot,
                        cur_preview=[] if (force_active_only or task_pool_force_active) else preview_types,
                        cur_prefetch_budget=0 if (force_active_only or task_pool_force_active) else prefetch_budget,
                    )
                    if task_pool_force_active and not options:
                        needed_for_bot = needed_types
                        options = _candidates_for_bot(
                            bot,
                            cur_needed=needed_for_bot,
                            cur_preview=[] if force_active_only else preview_types,
                            cur_prefetch_budget=0 if force_active_only else prefetch_budget,
                        )
                    active_only_needed = _active_only_needed_types(needed_for_bot)
                    options, active_only_applied = _filter_hungarian_active_only(
                        options=options,
                        needed_for_bot=active_only_needed,
                    )
                    if active_only_applied:
                        hungarian_active_only_round = True
                    if not options:
                        continue
                    cand = options[0]
                    item_id = str(cand.item.id)
                    item_type = str(cand.item.type)
                    if item_id in used_item_ids_local:
                        continue
                    if cand.pickup_pos in used_pickups_local:
                        continue
                    if cand.target_type == "pick_item":
                        if need_left.get(item_type, 0) <= 0:
                            continue
                    elif cand.target_type == "pre_pick":
                        if prefetch_left <= 0:
                            continue
                        if preview_left.get(item_type, 0) <= 0:
                            continue
                    accepted[bot.id] = cand
                    used_item_ids_local.add(item_id)
                    used_pickups_local.add(cand.pickup_pos)
                    if cand.target_type == "pick_item":
                        need_left[item_type] = need_left.get(item_type, 0) - 1
                    elif cand.target_type == "pre_pick":
                        preview_left[item_type] = preview_left.get(item_type, 0) - 1
                        prefetch_left -= 1

        if bool(cfg.hungarian_fallback_to_greedy):
            # Guardrail: keep Hungarian only when it is at least as strong as round-local greedy.
            ref_needed = list(needed_types)
            ref_preview = list(preview_types)
            ref_prefetch = max(0, int(prefetch_budget))
            ref_used_ids = set(assigned_item_ids)
            ref_used_pickups = set(assigned_pickup_positions)
            greedy_ref: dict[int, _BotCandidate] = {}
            ordered_ref_bots = sorted(
                unassigned_bots,
                key=lambda b: (0 if int(b.id) in critical_dispatch_bot_ids else 1, b.id),
            )
            for bot in ordered_ref_bots:
                task_pool_force_active = bool(cfg.task_pool_admission_enabled) and bot.id in task_pool_critical_bot_ids
                task_pool_fallback = False
                force_active_only = bot.id in force_active_only_ids
                needed_for_bot = task_pool_needed_types if task_pool_force_active else ref_needed
                if task_pool_force_active:
                    options = _candidates_for_bot(
                        bot,
                        cur_needed=needed_for_bot,
                        cur_preview=[],
                        cur_prefetch_budget=0,
                    )
                    if not options:
                        task_pool_fallback = True
                        needed_for_bot = ref_needed
                        options = _candidates_for_bot(
                            bot,
                            cur_needed=needed_for_bot,
                            cur_preview=[] if force_active_only else ref_preview,
                            cur_prefetch_budget=0 if force_active_only else ref_prefetch,
                        )
                else:
                    options = _candidates_for_bot(
                        bot,
                        cur_needed=needed_for_bot,
                        cur_preview=[] if force_active_only else ref_preview,
                        cur_prefetch_budget=0 if force_active_only else ref_prefetch,
                    )
                active_only_needed = _active_only_needed_types(needed_for_bot)
                options, active_only_applied = _filter_hungarian_active_only(
                    options=options,
                    needed_for_bot=active_only_needed,
                )
                if active_only_applied:
                    hungarian_active_only_round = True
                if not options:
                    continue
                cand = options[0]
                item_id = str(cand.item.id)
                if item_id in ref_used_ids:
                    continue
                if cand.pickup_pos in ref_used_pickups:
                    continue
                greedy_ref[bot.id] = cand
                ref_used_ids.add(item_id)
                ref_used_pickups.add(cand.pickup_pos)
                if cand.target_type == "pick_item" and cand.item.type in ref_needed:
                    ref_needed.remove(cand.item.type)
                elif cand.target_type == "pre_pick" and cand.item.type in ref_preview:
                    ref_preview.remove(cand.item.type)
                    ref_prefetch = max(0, ref_prefetch - 1)
                if task_pool_force_active and task_pool_fallback:
                    task_pool_critical_fallback_bot_ids.add(int(bot.id))

            accepted_score = sum(_hungarian_effective_utility(cand) for cand in accepted.values())
            greedy_score = sum(_hungarian_effective_utility(cand) for cand in greedy_ref.values())
            accepted_active = sum(1 for cand in accepted.values() if cand.target_type == "pick_item")
            greedy_active = sum(1 for cand in greedy_ref.values() if cand.target_type == "pick_item")
            if (
                len(greedy_ref) > len(accepted)
                or greedy_active > accepted_active
                or greedy_score > accepted_score + 1e-9
            ):
                accepted = greedy_ref
                hungarian_guardrail_fallback = True

        for bot_id, cand in sorted(accepted.items(), key=lambda kv: kv[0]):
            source = "hungarian_plan_fallback_greedy" if hungarian_guardrail_fallback else "hungarian_plan"
            if hungarian_active_only_round:
                source = f"{source}_active_only"
            if bool(cfg.task_pool_admission_enabled) and bot_id in task_pool_critical_bot_ids:
                if bot_id in task_pool_critical_fallback_bot_ids or cand.target_type != "pick_item":
                    source = f"{source}_critical_pool_fallback"
                else:
                    source = f"{source}_critical_pool"
            if bool(cfg.pipeline_budget_enabled) and cand.target_type == "pre_pick" and pipeline_mode != "off":
                source = f"{source}_pipeline_{pipeline_mode}"
            if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode != "off":
                source = f"{source}_soft_pipeline_{soft_pipeline_mode}"
            if bool(cfg.etadlc_enabled) and cand.target_type == "pick_item":
                source = f"{source}_etadlc_eta"
            if (
                bool(cfg.critical_dispatch_overlay_enabled)
                and bot_id in critical_dispatch_bot_ids
                and cand.target_type == "pick_item"
            ):
                source = f"{source}_critical_dispatch"
                if cand.critical_payload_bonus_applied:
                    source = f"{source}_payload"
            assignments[bot_id] = Assignment(
                target_type=cand.target_type,
                item=cand.item,
                pickup_pos=cand.pickup_pos,
                target_id=cand.item.id,
                source=source,
            )
            assigned_item_ids.add(cand.item.id)
            assigned_pickup_positions.add(cand.pickup_pos)
            if cand.target_type == "pick_item" and cand.item.type in needed_types:
                needed_types.remove(cand.item.type)
            elif cand.target_type == "pre_pick" and cand.item.type in preview_types:
                preview_types.remove(cand.item.type)
                prefetch_budget = max(0, prefetch_budget - 1)

        unassigned_bots = [bot for bot in unassigned_bots if bot.id not in assignments]
    else:
        still_unassigned = []
        ordered_bots = sorted(
            unassigned_bots,
            key=lambda b: (0 if int(b.id) in critical_dispatch_bot_ids else 1, b.id),
        )
        for bot in ordered_bots:
            task_pool_force_active = bool(cfg.task_pool_admission_enabled) and bot.id in task_pool_critical_bot_ids
            task_pool_fallback = False
            if task_pool_force_active:
                cand = _best_candidate_for_bot(
                    bot,
                    cur_needed=task_pool_needed_types,
                    cur_preview=[],
                    cur_prefetch_budget=0,
                )
                if cand is None:
                    task_pool_fallback = True
                    task_pool_critical_fallback_bot_ids.add(int(bot.id))
                    cand = _best_candidate_for_bot(
                        bot,
                        cur_needed=needed_types,
                        cur_preview=preview_types,
                        cur_prefetch_budget=prefetch_budget,
                    )
            else:
                cand = _best_candidate_for_bot(
                    bot,
                    cur_needed=needed_types,
                    cur_preview=preview_types,
                    cur_prefetch_budget=prefetch_budget,
                )
            if cand is None:
                still_unassigned.append(bot)
                continue

            source = "greedy_candidate"
            if task_pool_force_active:
                if task_pool_fallback or cand.target_type != "pick_item":
                    source = f"{source}_critical_pool_fallback"
                else:
                    source = f"{source}_critical_pool"
            if bool(cfg.pipeline_budget_enabled) and cand.target_type == "pre_pick" and pipeline_mode != "off":
                source = f"greedy_candidate_pipeline_{pipeline_mode}"
            if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode != "off":
                source = f"{source}_soft_pipeline_{soft_pipeline_mode}"
            if bool(cfg.etadlc_enabled) and cand.target_type == "pick_item":
                source = f"{source}_etadlc_eta"
            if (
                bool(cfg.critical_dispatch_overlay_enabled)
                and int(bot.id) in critical_dispatch_bot_ids
                and cand.target_type == "pick_item"
            ):
                source = f"{source}_critical_dispatch"
                if cand.critical_payload_bonus_applied:
                    source = f"{source}_payload"
            assignments[bot.id] = Assignment(
                target_type=cand.target_type,
                item=cand.item,
                pickup_pos=cand.pickup_pos,
                target_id=cand.item.id,
                source=source,
            )
            assigned_item_ids.add(cand.item.id)
            assigned_pickup_positions.add(cand.pickup_pos)
            if cand.target_type == "pick_item" and cand.item.type in needed_types:
                needed_types.remove(cand.item.type)
            elif cand.target_type == "pre_pick" and cand.item.type in preview_types:
                preview_types.remove(cand.item.type)
                prefetch_budget = max(0, prefetch_budget - 1)

        unassigned_bots = still_unassigned

    # Phase 4: bots with matching inventory deliver (respect delivery queue cap).
    deliver_slots_left = max(
        0,
        max_deliverers - sum(1 for assign in assignments.values() if assign.target_type == "deliver"),
    )
    phase4_deliver: list[tuple[tuple, object, float]] = []
    still_unassigned = []
    for bot in unassigned_bots:
        if bot.id in defer_deliver_ids:
            still_unassigned.append(bot)
            continue
        matching_count = len(items_matching_active(bot, state))
        if bot.inventory and matching_count > 0:
            overlay_payload_score = _overlay_payload_value_for_bot(bot)
            phase4_deliver.append(
                (
                    (
                        -overlay_payload_score,
                        dropoff_dist_map.get(bot.pos.as_tuple(), 999999),
                        -matching_count,
                        bot.id,
                    )
                    if bool(cfg.critical_dispatch_overlay_enabled)
                    else (
                        dropoff_dist_map.get(bot.pos.as_tuple(), 999999),
                        -matching_count,
                        bot.id,
                    ),
                    bot,
                    float(overlay_payload_score),
                )
            )
        else:
            still_unassigned.append(bot)
    phase4_deliver.sort(key=lambda row: row[0])
    for _priority, bot, overlay_payload_score in phase4_deliver:
        if deliver_slots_left <= 0:
            still_unassigned.append(bot)
            continue
        deliver_source = "deliver_matching"
        if bool(cfg.critical_dispatch_overlay_enabled) and overlay_payload_score >= 2.0:
            deliver_source = "deliver_matching_critical_overlay_payload"
        assignments[bot.id] = Assignment(
            target_type="deliver",
            drop_off=drop_off,
            source=deliver_source,
        )
        deliver_slots_left -= 1
    unassigned_bots = still_unassigned

    # Phase 4.5: if active order is already fully covered by planned picks/inventory,
    # use otherwise-idle bots for preview items even before normal prefetch gating opens.
    if (
        cfg.overflow_prefetch_when_active_assigned
        and not needed_types
        and unassigned_bots
        and cfg.lookahead_orders > 1
        and rounds_left > max(0, int(cfg.endgame_disable_prefetch_rounds))
        and (
            int(cfg.overflow_prefetch_round_limit) <= 0
            or int(state.round) <= int(cfg.overflow_prefetch_round_limit)
        )
    ):
        overflow_preview_types = compute_preview_items(
            state,
            commitment_mode=demand_mode,
            commit_radius=demand_commit_radius,
        )
        for assign in assignments.values():
            if assign.target_type != "pre_pick" or assign.item is None:
                continue
            if assign.item.type in overflow_preview_types:
                overflow_preview_types.remove(assign.item.type)

        if overflow_preview_types:
            overflow_prefetch_budget = max(
                0,
                sum(max(0, 3 - len(bot.inventory)) for bot in unassigned_bots),
            )
            still_unassigned = []
            for bot in unassigned_bots:
                cand = _best_candidate_for_bot(
                    bot,
                    cur_needed=[],
                    cur_preview=overflow_preview_types,
                    cur_prefetch_budget=overflow_prefetch_budget,
                )
                if cand is None or cand.target_type != "pre_pick":
                    still_unassigned.append(bot)
                    continue

                assignments[bot.id] = Assignment(
                    target_type=cand.target_type,
                    item=cand.item,
                    pickup_pos=cand.pickup_pos,
                    target_id=cand.item.id,
                    source="overflow_prefetch",
                )
                assigned_item_ids.add(cand.item.id)
                assigned_pickup_positions.add(cand.pickup_pos)
                if cand.item.type in overflow_preview_types:
                    overflow_preview_types.remove(cand.item.type)
                overflow_prefetch_budget = max(0, overflow_prefetch_budget - 1)
            unassigned_bots = still_unassigned

    late_secondary_window_rounds = max(0, int(cfg.stall_round_threshold))
    if bool(cfg.anti_no_assignment_enabled) and bool(cfg.secondary_assignment_enabled):
        # Keep anti-no-assignment fallback available in late game before hard endgame.
        late_secondary_window_rounds = max(late_secondary_window_rounds, 120)
    if bool(cfg.critical_dispatch_overlay_enabled) and not critical_overlay_focus_active:
        # Overlay v2.1: once overlay focus phase ends, open support lane earlier.
        late_secondary_window_rounds = max(
            late_secondary_window_rounds,
            int(cfg.critical_dispatch_secondary_window_rounds),
        )
    late_secondary_window_active = rounds_left <= late_secondary_window_rounds

    # Phase 4.75: late-only anti-no-assignment secondary fallback.
    if (
        bool(cfg.anti_no_assignment_enabled)
        and bool(cfg.secondary_assignment_enabled)
        and bool(cfg.anti_starvation_enabled)
        and late_secondary_window_active
        and unassigned_bots
    ):
        secondary_max_dist = max(1, int(cfg.secondary_max_distance))
        anti_rounds = max(1, int(cfg.anti_starvation_rounds))
        anti_bonus = max(0.0, float(cfg.anti_starvation_bonus))
        avoid_dropoff_inner_radius = max(1, int(cfg.dropoff_stop_line_radius))
        required_by_type: Counter[str] = Counter()
        if active_order is not None:
            for item_type in active_order.items_required:
                required_by_type[str(item_type)] += 1

        remaining_active_need = Counter(
            compute_needed_items(
                state,
                commitment_mode=demand_mode,
                commit_radius=demand_commit_radius,
            )
        )
        for assign in assignments.values():
            if assign.target_type == "pick_item" and assign.item is not None:
                item_type = str(assign.item.type)
                if remaining_active_need.get(item_type, 0) > 0:
                    remaining_active_need[item_type] -= 1
        remaining_active_need += Counter()
        starvation_delivered_need_fallback = False
        delivered_need_assigned_pickups: list[tuple[int, int]] = []
        delivered_need_soft_near_radius = max(0, int(cfg.secondary_delivered_need_soft_near_radius))
        delivered_need_soft_same_row_gap = max(0, int(cfg.secondary_delivered_need_soft_same_row_gap))
        delivered_need_soft_near_penalty = max(0.0, float(cfg.secondary_delivered_need_soft_near_penalty))
        delivered_need_soft_same_row_penalty = max(0.0, float(cfg.secondary_delivered_need_soft_same_row_penalty))
        if not any(count > 0 for count in remaining_active_need.values()):
            # Starvation fallback: when committed demand appears covered but active
            # order is still incomplete, use delivered-only need for secondary routing.
            delivered_need = Counter(
                compute_needed_items(
                    state,
                    commitment_mode=COMMIT_MODE_DELIVERED_ONLY,
                    commit_radius=demand_commit_radius,
                )
            )
            delivered_need += Counter()
            if any(count > 0 for count in delivered_need.values()):
                remaining_active_need = delivered_need
                starvation_delivered_need_fallback = True
        duplicate_need_types = {
            item_type
            for item_type, count in remaining_active_need.items()
            if count > 0 and required_by_type.get(item_type, 0) >= 2
        }

        def _secondary_candidate_for_types(
            *,
            bot,
            allowed_types: set[str],
            starvation_boost: bool,
        ) -> tuple[tuple, str, tuple[int, int], bool, int, int] | None:
            if not allowed_types:
                return None
            if bool(cfg.secondary_reposition_empty_only) and bot.inventory:
                return None
            dist_map = bfs_distance_map(grid, bot.pos.as_tuple(), blocked=blocked_set)
            best: tuple[tuple, str, tuple[int, int], bool, int, int] | None = None
            for item in available_items:
                if item.id in blocked_ids:
                    continue
                item_type = str(item.type)
                if item_type not in allowed_types:
                    continue
                for pickup_pos in pickup_cache.get(item.id, []):
                    if pickup_pos in assigned_pickup_positions:
                        continue
                    if pickup_pos == bot.pos.as_tuple():
                        continue
                    dist = dist_map.get(pickup_pos, 999999)
                    if dist >= 999999:
                        continue
                    if dist > secondary_max_dist:
                        continue
                    dist_to_drop = dropoff_dist_map.get(pickup_pos, 999999)
                    if dist_to_drop >= 999999:
                        continue
                    if dist_to_drop <= avoid_dropoff_inner_radius:
                        continue
                    raw_dist = float(dist)
                    effective_dist = raw_dist
                    if starvation_boost:
                        effective_dist = max(0.0, raw_dist - anti_bonus)
                    soft_near_count = 0
                    soft_same_row_count = 0
                    if starvation_delivered_need_fallback and delivered_need_assigned_pickups:
                        for taken in delivered_need_assigned_pickups:
                            md = abs(pickup_pos[0] - taken[0]) + abs(pickup_pos[1] - taken[1])
                            if delivered_need_soft_near_radius > 0 and md <= delivered_need_soft_near_radius:
                                soft_near_count += 1
                            if (
                                delivered_need_soft_same_row_gap > 0
                                and pickup_pos[1] == taken[1]
                                and abs(pickup_pos[0] - taken[0]) <= delivered_need_soft_same_row_gap
                            ):
                                soft_same_row_count += 1
                    soft_penalty = (
                        float(soft_near_count) * delivered_need_soft_near_penalty
                        + float(soft_same_row_count) * delivered_need_soft_same_row_penalty
                    )
                    effective_dist += soft_penalty
                    if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode in {"build", "secure"}:
                        effective_dist += soft_fallback_penalty_open_tail
                    soft_applied = soft_penalty > 0.0
                    rank = (
                        effective_dist,
                        soft_same_row_count,
                        soft_near_count,
                        raw_dist,
                        item_type,
                        pickup_pos,
                        bot.id,
                    )
                    if best is None or rank < best[0]:
                        best = (
                            rank,
                            item_type,
                            pickup_pos,
                            soft_applied,
                            soft_near_count,
                            soft_same_row_count,
                        )
            return best

        def _secondary_source(
            *,
            base: str,
            soft_applied: bool,
            soft_near_count: int,
            soft_same_row_count: int,
        ) -> str:
            if not starvation_delivered_need_fallback:
                source = base
            else:
                source = f"{base}_delivered_need"
                if soft_applied:
                    if soft_same_row_count > 0:
                        source += "_soft_same_row"
                    elif soft_near_count > 0:
                        source += "_soft_near"
                    else:
                        source += "_soft"
            if bool(cfg.pipeline_budget_enabled) and pipeline_mode != "off":
                source += f"_pipeline_{pipeline_mode}"
            if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode != "off":
                source += f"_soft_pipeline_{soft_pipeline_mode}"
            return source

        ordered_unassigned = sorted(
            unassigned_bots,
            key=lambda bot: (
                -primary_miss_streak.get(int(bot.id), 0),
                bot.id,
            ),
        )
        still_unassigned = []
        fallback_budget_limit: int | None = None
        if bool(cfg.pipeline_budget_enabled) and pipeline_fallback_budget is not None:
            fallback_budget_limit = max(0, int(pipeline_fallback_budget))
        fallback_assigned = 0
        for bot in ordered_unassigned:
            if fallback_budget_limit is not None and fallback_assigned >= fallback_budget_limit:
                still_unassigned.append(bot)
                continue
            starved_for_secondary = primary_miss_streak.get(int(bot.id), 0) >= anti_rounds
            if not starved_for_secondary:
                still_unassigned.append(bot)
                continue
            assigned = False
            active_types = {
                item_type
                for item_type, count in remaining_active_need.items()
                if count > 0
            }
            active_support_types = set(active_types) - set(duplicate_need_types)
            if active_support_types:
                choice = _secondary_candidate_for_types(
                    bot=bot,
                    allowed_types=active_support_types,
                    starvation_boost=False,
                )
                if choice is not None:
                    (
                        _rank,
                        item_type,
                        pickup_pos,
                        soft_applied,
                        soft_near_count,
                        soft_same_row_count,
                    ) = choice
                    assignments[bot.id] = Assignment(
                        target_type="secondary_reposition",
                        pickup_pos=pickup_pos,
                        target_id=f"secondary:{item_type}:{pickup_pos[0]}:{pickup_pos[1]}",
                        source=_secondary_source(
                            base="secondary_active_support",
                            soft_applied=soft_applied,
                            soft_near_count=soft_near_count,
                            soft_same_row_count=soft_same_row_count,
                        ),
                    )
                    assigned_pickup_positions.add(pickup_pos)
                    if starvation_delivered_need_fallback:
                        delivered_need_assigned_pickups.append(pickup_pos)
                    if remaining_active_need.get(item_type, 0) > 0:
                        remaining_active_need[item_type] -= 1
                    assigned = True

            if (
                not assigned
                and bool(cfg.secondary_duplicate_support)
                and duplicate_need_types
            ):
                choice = _secondary_candidate_for_types(
                    bot=bot,
                    allowed_types=set(duplicate_need_types),
                    starvation_boost=False,
                )
                if choice is not None:
                    (
                        _rank,
                        item_type,
                        pickup_pos,
                        soft_applied,
                        soft_near_count,
                        soft_same_row_count,
                    ) = choice
                    assignments[bot.id] = Assignment(
                        target_type="secondary_reposition",
                        pickup_pos=pickup_pos,
                        target_id=f"secondary:{item_type}:{pickup_pos[0]}:{pickup_pos[1]}",
                        source=_secondary_source(
                            base="secondary_duplicate_support",
                            soft_applied=soft_applied,
                            soft_near_count=soft_near_count,
                            soft_same_row_count=soft_same_row_count,
                        ),
                    )
                    assigned_pickup_positions.add(pickup_pos)
                    if starvation_delivered_need_fallback:
                        delivered_need_assigned_pickups.append(pickup_pos)
                    if remaining_active_need.get(item_type, 0) > 0:
                        remaining_active_need[item_type] -= 1
                    assigned = True

            if (
                not assigned
                and bool(cfg.anti_starvation_enabled)
                and primary_miss_streak.get(int(bot.id), 0) >= anti_rounds
                and any(count > 0 for count in remaining_active_need.values())
            ):
                choice = _secondary_candidate_for_types(
                    bot=bot,
                    allowed_types={
                        item_type
                        for item_type, count in remaining_active_need.items()
                        if count > 0
                    },
                    starvation_boost=True,
                )
                if choice is not None:
                    (
                        _rank,
                        item_type,
                        pickup_pos,
                        soft_applied,
                        soft_near_count,
                        soft_same_row_count,
                    ) = choice
                    assignments[bot.id] = Assignment(
                        target_type="secondary_reposition",
                        pickup_pos=pickup_pos,
                        target_id=f"secondary:{item_type}:{pickup_pos[0]}:{pickup_pos[1]}",
                        source=_secondary_source(
                            base="secondary_starvation_support",
                            soft_applied=soft_applied,
                            soft_near_count=soft_near_count,
                            soft_same_row_count=soft_same_row_count,
                        ),
                    )
                    assigned_pickup_positions.add(pickup_pos)
                    if starvation_delivered_need_fallback:
                        delivered_need_assigned_pickups.append(pickup_pos)
                    if remaining_active_need.get(item_type, 0) > 0:
                        remaining_active_need[item_type] -= 1
                    assigned = True

            if assigned:
                fallback_assigned += 1
            else:
                still_unassigned.append(bot)
        unassigned_bots = sorted(still_unassigned, key=lambda row: row.id)

    if (
        cfg.dropoff_stop_line_enabled
        and dropoff_zone_now >= max(0.0, float(cfg.dropoff_stop_line_trigger_density))
    ):
        radius = max(1, int(cfg.dropoff_stop_line_radius))
        cap = max(1, int(cfg.dropoff_stop_line_k))
        bot_by_id = {bot.id: bot for bot in state.bots}
        near_deliverers: list[int] = []
        for bot_id, assign in assignments.items():
            if assign.target_type != "deliver":
                continue
            bot = bot_by_id.get(bot_id)
            if bot is None:
                continue
            dist_to_drop = abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1])
            if dist_to_drop <= radius:
                near_deliverers.append(bot_id)

        if len(near_deliverers) > cap:
            def _deliver_priority(bot_id: int) -> tuple:
                bot = bot_by_id[bot_id]
                dist_to_drop = abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1])
                matching_count = len(items_matching_active(bot, state))
                if bot.pos.as_tuple() == drop_off and matching_count > 0:
                    cls = 0  # DROP_OFF ready
                elif matching_count > 0:
                    cls = 1  # matching deliverers
                else:
                    cls = 2  # evictors / other non-matching
                return (cls, dist_to_drop, -matching_count, -len(bot.inventory), bot.id)

            keep = set(sorted(near_deliverers, key=_deliver_priority)[:cap])
            for bot_id in near_deliverers:
                if bot_id in keep:
                    continue
                assignments[bot_id] = Assignment(target_type="idle", source="dropoff_stopline")

    # Phase 5: idle fallback.
    idle_source = "idle_fallback"
    if bool(cfg.pipeline_budget_enabled) and pipeline_mode != "off":
        idle_source = f"idle_fallback_pipeline_{pipeline_mode}"
    if bool(cfg.soft_pipeline_budget_enabled) and soft_pipeline_mode != "off":
        idle_source = f"{idle_source}_soft_pipeline_{soft_pipeline_mode}"
    for bot in unassigned_bots:
        assignments[bot.id] = Assignment(target_type="idle", source=idle_source)

    return assignments
