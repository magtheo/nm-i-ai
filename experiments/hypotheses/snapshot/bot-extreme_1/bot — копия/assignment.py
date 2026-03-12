"""Task assignment for multi-bot grocery routing."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from typing import Optional

from .grid import Grid
from .models import GameState, ItemInfo
from .orders import (
    compute_needed_items,
    compute_preview_items,
    get_active_order,
    items_matching_active,
    should_prefetch_preview,
)
from .pathfinding import bfs_distance_map, find_all_pickup_positions


@dataclass(frozen=True)
class AssignmentPolicy:
    lookahead_orders: int = 2
    active_weight: float = 10.0
    preview_weight: float = 4.0
    dropoff_completion_threshold: float = 0.67
    zone_penalty_weight: float = 0.0
    dist_weight: float = 1.0
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
    strict_active_priority: bool = False
    strict_active_release_completion: float = 1.0
    force_dropoff_for_full_nonmatching: bool = False
    always_deliver_matching: bool = False
    avoid_dropoff_block_when_matching: bool = True
    max_concurrent_deliverers: int = 2
    adaptive_deliver_queue: bool = False
    deliver_queue_min: int = 1
    deliver_queue_max: int = 3
    assignment_strategy: str = "greedy"  # greedy | auction
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
    tie_break_seed: int = 0
    tie_break_dynamic: bool = False


@dataclass
class Assignment:
    """What a bot should do this round."""

    target_type: str  # "pick_item" | "deliver" | "idle" | "pre_pick"
    item: Optional[ItemInfo] = None
    pickup_pos: Optional[tuple[int, int]] = None
    drop_off: Optional[tuple[int, int]] = None
    target_id: Optional[str] = None


@dataclass(frozen=True)
class _BotCandidate:
    item: ItemInfo
    target_type: str
    pickup_pos: tuple[int, int]
    rank: tuple
    utility_score: float


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
) -> dict[int, Assignment]:
    """Greedy nearest assignment with deterministic tie-breaks."""
    cfg = policy or AssignmentPolicy()
    blocked_ids = blocked_item_ids or set()
    rank_seed = int(cfg.tie_break_seed)
    if cfg.tie_break_dynamic:
        rank_seed += int((state.active_order_index + 1) * 1009)
    assignments: dict[int, Assignment] = {}
    drop_off = (state.drop_off[0], state.drop_off[1])

    needed_types = compute_needed_items(state)
    needed_counter = Counter(needed_types)
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
    active_order = get_active_order(state)
    active_total = len(active_order.items_required) if active_order is not None else 0
    completion_ratio = 1.0
    if active_total > 0:
        completion_ratio = (active_total - remaining_active_items) / active_total
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
    strict_prefetch_block = (
        strict_active_now
        and completion_ratio < strict_release_completion
        and not active_covered_by_carry
    )

    if cfg.lookahead_orders > 1:
        if completion_ratio < max(0.0, min(1.0, cfg.prefetch_min_completion)):
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
        elif should_prefetch_preview(state):
            prefetch_budget = max(
                0,
                remaining_inventory_slots
                - remaining_active_items
                - max(0, int(cfg.prefetch_spare_slots)),
            )
            if prefetch_budget > 0:
                preview_types = compute_preview_items(state)

    available_items: list[ItemInfo] = sorted(state.items, key=lambda item: item.id)
    assigned_item_ids: set[str] = set()
    assigned_pickup_positions: set[tuple[int, int]] = set()
    blocked_set = set(item_blocked)
    dropoff_dist_map = bfs_distance_map(grid, drop_off, blocked=blocked_set)
    pickup_cache: dict[str, list[tuple[int, int]]] = {
        item.id: find_all_pickup_positions(grid, item.pos.as_tuple())
        for item in available_items
    }

    unassigned_bots = sorted(state.bots, key=lambda b: b.id)

    # Phase 1: bots at drop-off with matching inventory deliver immediately.
    still_unassigned = []
    for bot in unassigned_bots:
        if bot.pos.as_tuple() == drop_off and items_matching_active(bot, state):
            assignments[bot.id] = Assignment(target_type="deliver", drop_off=drop_off)
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
    if max_deliverers <= 0:
        max_deliverers = len(unassigned_bots) + 3
    deliver_slots_left = max(0, max_deliverers - len(assignments))
    deliver_candidates: list[tuple[tuple, object]] = []
    still_unassigned = []
    inventory_locked = remaining_inventory_slots == 0 and remaining_active_items > 0
    for bot in unassigned_bots:
        matching = items_matching_active(bot, state)
        matching_count = len(matching)
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
            deliver_candidates.append(
                (
                    (
                        deliver_reason,
                        dist_to_drop,
                        -matching_count,
                        -len(bot.inventory),
                        bot.id,
                    ),
                    bot,
                )
            )
    deliver_candidates.sort(key=lambda row: row[0])
    for _priority, bot in deliver_candidates:
        if deliver_slots_left <= 0:
            still_unassigned.append(bot)
            continue
        assignments[bot.id] = Assignment(target_type="deliver", drop_off=drop_off)
        deliver_slots_left -= 1
    unassigned_bots = still_unassigned

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
        active_matching_now = len(items_matching_active(bot, state))
        nonmatching_now = max(0, len(bot.inventory) - active_matching_now)
        dist_bot_drop = dropoff_dist_map.get(bpos, 999999)
        needed_set = set(cur_needed)
        preview_set = set(cur_preview)
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
            if item.type in needed_set:
                target_type = "pick_item"
                utility = cfg.active_weight
            elif item.type in preview_set and cur_prefetch_budget > 0:
                if strict_prefetch_block and has_reachable_active:
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
                    if has_other_unassigned_same_type:
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
                zone_center_x = 4 + bot.id * 4
                zone_penalty = float(abs(pickup_pos[0] - zone_center_x))
                replan_penalty = 0.0
                if sticky_targets and sticky_targets.get(bot.id) not in (None, item.id):
                    replan_penalty = max(0.0, float(cfg.hysteresis_penalty))
                sticky_bonus = 0.0
                if sticky_targets and sticky_targets.get(bot.id) == item.id:
                    sticky_bonus = max(0.0, float(cfg.sticky_target_bonus))
                trip_chain_bonus = 0.0
                if (
                    cfg.trip_chain_bonus_weight > 0.0
                    and len(bot.inventory) + 1 < 3
                    and needed_set
                ):
                    dist_from_pick = bfs_distance_map(grid, pickup_pos, blocked=blocked_set)
                    best_chain_saving = 0.0
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
                            saving = float(dist_to_drop) + float(d2drop) - float(d12)
                            if saving > best_chain_saving:
                                best_chain_saving = saving
                    trip_chain_bonus = max(0.0, best_chain_saving)
                utility_score = (
                    cfg.urgency_weight * utility
                    + sticky_bonus
                    + cfg.trip_chain_bonus_weight * trip_chain_bonus
                    - cfg.dist_weight * float(dist)
                    - cfg.dropoff_dist_weight * float(dist_to_drop)
                    - cfg.congestion_weight * congestion
                    - cfg.collision_risk_weight * collision_risk
                    - cfg.zone_penalty_weight * zone_penalty
                    - cfg.replan_penalty_weight * replan_penalty
                )
                if active_matching_now > 0 and dist_bot_drop < 999999:
                    utility_score -= (
                        float(cfg.carry_home_bias_weight)
                        * float(active_matching_now)
                        * (float(dist_bot_drop) / 5.0)
                    )
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
        options = _candidates_for_bot(
            bot,
            cur_needed=cur_needed,
            cur_preview=cur_preview,
            cur_prefetch_budget=cur_prefetch_budget,
        )
        if not options:
            return None
        return options[0]

    # Phase 3: item assignment (greedy or auction).
    if cfg.assignment_strategy == "auction":
        bot_ids = [bot.id for bot in sorted(unassigned_bots, key=lambda b: b.id)]
        options_by_bot: dict[int, list[_BotCandidate]] = {}
        for bot in sorted(unassigned_bots, key=lambda b: b.id):
            options = _candidates_for_bot(
                bot,
                cur_needed=needed_types,
                cur_preview=preview_types,
                cur_prefetch_budget=prefetch_budget,
            )
            options_by_bot[bot.id] = options[:12]

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
            assignments[bot_id] = Assignment(
                target_type=cand.target_type,
                item=cand.item,
                pickup_pos=cand.pickup_pos,
                target_id=cand.item.id,
            )
            assigned_item_ids.add(cand.item.id)
            assigned_pickup_positions.add(cand.pickup_pos)

        unassigned_bots = [bot for bot in unassigned_bots if bot.id not in assignments]
    else:
        still_unassigned = []
        for bot in unassigned_bots:
            cand = _best_candidate_for_bot(
                bot,
                cur_needed=needed_types,
                cur_preview=preview_types,
                cur_prefetch_budget=prefetch_budget,
            )
            if cand is None:
                still_unassigned.append(bot)
                continue

            assignments[bot.id] = Assignment(
                target_type=cand.target_type,
                item=cand.item,
                pickup_pos=cand.pickup_pos,
                target_id=cand.item.id,
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
    phase4_deliver: list[tuple[tuple, object]] = []
    still_unassigned = []
    for bot in unassigned_bots:
        matching_count = len(items_matching_active(bot, state))
        if bot.inventory and matching_count > 0:
            phase4_deliver.append(
                (
                    (
                        dropoff_dist_map.get(bot.pos.as_tuple(), 999999),
                        -matching_count,
                        bot.id,
                    ),
                    bot,
                )
            )
        else:
            still_unassigned.append(bot)
    phase4_deliver.sort(key=lambda row: row[0])
    for _priority, bot in phase4_deliver:
        if deliver_slots_left <= 0:
            still_unassigned.append(bot)
            continue
        assignments[bot.id] = Assignment(target_type="deliver", drop_off=drop_off)
        deliver_slots_left -= 1
    unassigned_bots = still_unassigned

    # Phase 5: idle fallback.
    for bot in unassigned_bots:
        assignments[bot.id] = Assignment(target_type="idle")

    return assignments
