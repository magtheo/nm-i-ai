"""DecisionEngine — per-round orchestrator for the grocery bot swarm."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
import time
from typing import Optional

from .assignment import Assignment, AssignmentPolicy, assign_bots
from .collision import action_for_move, resolve_collisions_with_stats
from .grid import Grid
from .models import (
    BotAction,
    BotActionCommand,
    BotInfo,
    GameState,
    Pos,
    RoundActions,
)
from .orders import (
    compute_preview_items,
    get_active_order,
    compute_needed_items,
    items_matching_active,
)
from .pathfinding import astar_path, bfs_distance, bfs_shortest_path, find_all_pickup_positions


@dataclass(frozen=True)
class DecisionConfig:
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
    avoid_immediate_backtrack: bool = True
    backtrack_slack: int = 1
    wait_on_backtrack_conflict: bool = False
    collision_aggressiveness: str = "wait"  # wait | detour
    decision_soft_budget_ms: float = 20.0
    decision_hard_cap_ms: float = 50.0
    pickup_fail_blacklist_threshold: int = 2
    pickup_fail_blacklist_rounds: int = 40
    stall_round_threshold: int = 24
    stall_recovery_rounds: int = 40
    stall_recovery_preview_weight: float = 0.0
    stall_recovery_force_dropoff: bool = True
    stall_recovery_strict_active: bool = True
    clear_adjacent_dropoff_lane: bool = False
    clear_lane_distance: int = 4
    allow_same_shelf_for_same_type: bool = False
    stage_nonmatching_when_active_covered: bool = False
    stage_nonmatching_endgame_rounds: int = 0
    tie_break_seed: int = 0
    tie_break_dynamic: bool = False

    def to_dict(self) -> dict:
        return {
            "lookahead_orders": self.lookahead_orders,
            "active_weight": self.active_weight,
            "preview_weight": self.preview_weight,
            "dropoff_completion_threshold": self.dropoff_completion_threshold,
            "zone_penalty_weight": self.zone_penalty_weight,
            "dist_weight": self.dist_weight,
            "dropoff_dist_weight": self.dropoff_dist_weight,
            "congestion_weight": self.congestion_weight,
            "collision_risk_weight": self.collision_risk_weight,
            "replan_penalty_weight": self.replan_penalty_weight,
            "carry_home_bias_weight": self.carry_home_bias_weight,
            "urgency_weight": self.urgency_weight,
            "trip_chain_bonus_weight": self.trip_chain_bonus_weight,
            "future_depth_decay": self.future_depth_decay,
            "future_count_weight": self.future_count_weight,
            "future_prefetch_bonus": self.future_prefetch_bonus,
            "future_priority_mode": self.future_priority_mode,
            "prefetch_min_completion": self.prefetch_min_completion,
            "prefetch_spare_slots": self.prefetch_spare_slots,
            "prefetch_nonmatching_cap": self.prefetch_nonmatching_cap,
            "strict_active_priority": self.strict_active_priority,
            "strict_active_release_completion": self.strict_active_release_completion,
            "force_dropoff_for_full_nonmatching": self.force_dropoff_for_full_nonmatching,
            "always_deliver_matching": self.always_deliver_matching,
            "avoid_dropoff_block_when_matching": self.avoid_dropoff_block_when_matching,
            "max_concurrent_deliverers": self.max_concurrent_deliverers,
            "adaptive_deliver_queue": self.adaptive_deliver_queue,
            "deliver_queue_min": self.deliver_queue_min,
            "deliver_queue_max": self.deliver_queue_max,
            "assignment_strategy": self.assignment_strategy,
            "reservation_horizon": self.reservation_horizon,
            "hysteresis_penalty": self.hysteresis_penalty,
            "sticky_target_bonus": self.sticky_target_bonus,
            "early_deliver_matching_count": self.early_deliver_matching_count,
            "early_deliver_inventory_threshold": self.early_deliver_inventory_threshold,
            "endgame_disable_prefetch_rounds": self.endgame_disable_prefetch_rounds,
            "endgame_force_deliver_rounds": self.endgame_force_deliver_rounds,
            "endgame_strict_active": self.endgame_strict_active,
            "avoid_immediate_backtrack": self.avoid_immediate_backtrack,
            "backtrack_slack": self.backtrack_slack,
            "wait_on_backtrack_conflict": self.wait_on_backtrack_conflict,
            "collision_aggressiveness": self.collision_aggressiveness,
            "decision_soft_budget_ms": self.decision_soft_budget_ms,
            "decision_hard_cap_ms": self.decision_hard_cap_ms,
            "pickup_fail_blacklist_threshold": self.pickup_fail_blacklist_threshold,
            "pickup_fail_blacklist_rounds": self.pickup_fail_blacklist_rounds,
            "stall_round_threshold": self.stall_round_threshold,
            "stall_recovery_rounds": self.stall_recovery_rounds,
            "stall_recovery_preview_weight": self.stall_recovery_preview_weight,
            "stall_recovery_force_dropoff": self.stall_recovery_force_dropoff,
            "stall_recovery_strict_active": self.stall_recovery_strict_active,
            "clear_adjacent_dropoff_lane": self.clear_adjacent_dropoff_lane,
            "clear_lane_distance": self.clear_lane_distance,
            "allow_same_shelf_for_same_type": self.allow_same_shelf_for_same_type,
            "stage_nonmatching_when_active_covered": self.stage_nonmatching_when_active_covered,
            "stage_nonmatching_endgame_rounds": self.stage_nonmatching_endgame_rounds,
            "tie_break_seed": self.tie_break_seed,
            "tie_break_dynamic": self.tie_break_dynamic,
        }


class DecisionEngine:
    """Stateless per-round decision maker.

    Call ``decide(state)`` each round.  Returns ``RoundActions`` ready to send.
    """

    def __init__(
        self,
        *,
        use_astar: bool = False,
        debug: bool = False,
        verbose: bool = False,
        config: "DecisionConfig | None" = None,
        order_forecast: Optional[dict[int, list[str]]] = None,
    ):
        self.config = config or DecisionConfig()
        self.assignment_policy = AssignmentPolicy(
            lookahead_orders=self.config.lookahead_orders,
            active_weight=self.config.active_weight,
            preview_weight=self.config.preview_weight,
            dropoff_completion_threshold=self.config.dropoff_completion_threshold,
            zone_penalty_weight=self.config.zone_penalty_weight,
            dist_weight=self.config.dist_weight,
            dropoff_dist_weight=self.config.dropoff_dist_weight,
            congestion_weight=self.config.congestion_weight,
            collision_risk_weight=self.config.collision_risk_weight,
            replan_penalty_weight=self.config.replan_penalty_weight,
            carry_home_bias_weight=self.config.carry_home_bias_weight,
            urgency_weight=self.config.urgency_weight,
            trip_chain_bonus_weight=self.config.trip_chain_bonus_weight,
            future_depth_decay=self.config.future_depth_decay,
            future_count_weight=self.config.future_count_weight,
            future_prefetch_bonus=self.config.future_prefetch_bonus,
            future_priority_mode=self.config.future_priority_mode,
            prefetch_min_completion=self.config.prefetch_min_completion,
            prefetch_spare_slots=self.config.prefetch_spare_slots,
            prefetch_nonmatching_cap=self.config.prefetch_nonmatching_cap,
            strict_active_priority=self.config.strict_active_priority,
            strict_active_release_completion=self.config.strict_active_release_completion,
            force_dropoff_for_full_nonmatching=self.config.force_dropoff_for_full_nonmatching,
            always_deliver_matching=self.config.always_deliver_matching,
            avoid_dropoff_block_when_matching=self.config.avoid_dropoff_block_when_matching,
            max_concurrent_deliverers=self.config.max_concurrent_deliverers,
            adaptive_deliver_queue=self.config.adaptive_deliver_queue,
            deliver_queue_min=self.config.deliver_queue_min,
            deliver_queue_max=self.config.deliver_queue_max,
            assignment_strategy=self.config.assignment_strategy,
            reservation_horizon=self.config.reservation_horizon,
            hysteresis_penalty=self.config.hysteresis_penalty,
            sticky_target_bonus=self.config.sticky_target_bonus,
            early_deliver_matching_count=self.config.early_deliver_matching_count,
            early_deliver_inventory_threshold=self.config.early_deliver_inventory_threshold,
            endgame_disable_prefetch_rounds=self.config.endgame_disable_prefetch_rounds,
            endgame_force_deliver_rounds=self.config.endgame_force_deliver_rounds,
            endgame_strict_active=self.config.endgame_strict_active,
            clear_adjacent_dropoff_lane=self.config.clear_adjacent_dropoff_lane,
            clear_lane_distance=self.config.clear_lane_distance,
            allow_same_shelf_for_same_type=self.config.allow_same_shelf_for_same_type,
            tie_break_seed=self.config.tie_break_seed,
            tie_break_dynamic=self.config.tie_break_dynamic,
        )
        self.use_astar = use_astar
        self.debug = debug
        self.verbose = verbose  # extra detailed per-round output
        self.last_decision_ms: float = 0.0
        self.last_collisions_avoided: int = 0
        self.last_blocked_moves: int = 0
        self.last_swaps_prevented: int = 0
        self.last_replans: int = 0
        self.last_fallback_used: bool = False
        self._sticky_targets: dict[int, str] = {}
        self._position_history: dict[int, deque[tuple[int, int]]] = {}
        self._order_forecast: dict[int, list[str]] = order_forecast or {}
        self._prev_inventory_by_bot: dict[int, tuple[str, ...]] = {}
        self._last_pick_attempt_by_bot: dict[int, str] = {}
        self._pickup_fail_counts: dict[str, int] = {}
        self._blocked_pick_items_until_round: dict[str, int] = {}
        self._prev_score: int | None = None
        self._prev_active_order_index: int | None = None
        self._prev_active_delivered: int | None = None
        self._stall_rounds: int = 0
        self._stall_recovery_until_round: int = -1
        self._joint_signature_history: deque[tuple[tuple[int, tuple[int, int]], ...]] = deque(maxlen=12)

    # ── Public API ─────────────────────────────────────────────────────

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()
        self.last_collisions_avoided = 0
        self.last_blocked_moves = 0
        self.last_swaps_prevented = 0
        self.last_replans = 0
        self.last_fallback_used = False
        grid = Grid(state.grid)
        drop_off = (state.drop_off[0], state.drop_off[1])
        joint_sig = tuple((bot.id, bot.pos.as_tuple()) for bot in sorted(state.bots, key=lambda b: b.id))
        self._joint_signature_history.append(joint_sig)

        for bot in state.bots:
            history = self._position_history.setdefault(bot.id, deque(maxlen=4))
            pos = bot.pos.as_tuple()
            if not history or history[-1] != pos:
                history.append(pos)

        self._update_pickup_feedback(state)
        self._reconcile_order_forecast(state)
        blocked_pick_ids = self._blocked_pick_item_ids(state.round)
        policy = self._effective_policy(state)

        # Items on the map block movement but must NOT be merged into
        # grid._walls — that destroys pickup-position discovery.
        # Instead we thread item_positions as a "blocked" set through
        # pathfinding calls so BFS/A* won't route *through* items,
        # while walkable_neighbors_of() still reports floor tiles next
        # to item shelves correctly.
        item_positions = frozenset(
            (it.position[0], it.position[1]) for it in state.items
        )

        if self.verbose and state.round < 10:
            self._dump_state(state, grid)

        # Get assignments for all bots (clean grid — items NOT in walls)
        assignments = assign_bots(
            state,
            grid,
            item_blocked=item_positions,
            policy=policy,
            sticky_targets=self._sticky_targets,
            order_forecast=self._order_forecast,
            active_order_index=state.active_order_index,
            blocked_item_ids=blocked_pick_ids,
        )
        sticky_next: dict[int, str] = {}
        for bot in state.bots:
            assign = assignments.get(bot.id)
            if assign is None or assign.target_id is None:
                continue
            sticky_next[bot.id] = assign.target_id
            prev_target = self._sticky_targets.get(bot.id)
            if prev_target is not None and prev_target != assign.target_id:
                self.last_replans += 1
        self._sticky_targets = sticky_next

        if self._elapsed_ms(t0) >= self.config.decision_hard_cap_ms:
            self.last_fallback_used = True
            self._last_pick_attempt_by_bot = {}
            return self._fallback_actions(state, t0)

        # Convert assignments into concrete single-step actions
        actions: list[BotActionCommand] = []
        # Track planned next positions for collision resolution
        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        detour_preferred_bots: set[int] = set()
        stationary: set[tuple[int, int]] = set()
        active_pick_budget = Counter(compute_needed_items(state))
        planned_active_picks: Counter[str] = Counter()
        item_type_by_id = {item.id: item.type for item in state.items}

        for bot in sorted(state.bots, key=lambda b: b.id):
            bpos = bot.pos.as_tuple()
            assign = assignments.get(bot.id)
            if assign is None:
                actions.append(BotActionCommand(bot=bot.id, action=BotAction.WAIT))
                stationary.add(bpos)
                continue

            if self.verbose:
                item_info = ""
                if assign.item:
                    item_info = f" item={assign.item.id}({assign.item.type})@{assign.item.pos.as_tuple()}"
                    item_info += f" pickup_pos={assign.pickup_pos}"
                print(f"    Bot{bot.id}@{bpos} inv={bot.inventory} -> "
                      f"{assign.target_type}{item_info} drop_off={assign.drop_off}")

            cmd = self._execute_assignment(
                bot,
                assign,
                grid,
                state,
                effective_policy=policy,
                item_blocked=item_positions,
                active_pick_budget=active_pick_budget,
                planned_active_picks=planned_active_picks,
            )

            if cmd.action == BotAction.PICK_UP and cmd.item_id is not None:
                item_type = item_type_by_id.get(cmd.item_id)
                if item_type is not None and item_type in active_pick_budget:
                    remaining_budget = active_pick_budget[item_type] - planned_active_picks[item_type]
                    if remaining_budget <= 0:
                        cmd = BotActionCommand(bot=bot.id, action=BotAction.WAIT)
                    else:
                        planned_active_picks[item_type] += 1

            if self.verbose:
                print(f"      => action={cmd.action.value}"
                      f"{' item_id=' + cmd.item_id if cmd.item_id else ''}")

            if cmd.action in (BotAction.PICK_UP, BotAction.DROP_OFF, BotAction.WAIT):
                actions.append(cmd)
                stationary.add(bpos)
            else:
                # Movement — compute target cell for collision check
                target = self._move_target(bpos, cmd.action)
                if self._would_oscillate(bot.id, target):
                    actions.append(BotActionCommand(bot=bot.id, action=BotAction.WAIT))
                    stationary.add(bpos)
                    continue
                if grid.is_walkable(target[0], target[1]) and target not in item_positions:
                    move_plans.append((bot.id, bpos, target))
                    if assign.target_type == "deliver" and items_matching_active(bot, state):
                        detour_preferred_bots.add(bot.id)
                else:
                    # Can't move there - wait instead
                    if self.verbose:
                        print(f"      => BLOCKED at {target}, waiting instead")
                    actions.append(BotActionCommand(bot=bot.id, action=BotAction.WAIT))
                    stationary.add(bpos)

        # Resolve collisions among moving bots
        if move_plans:
            resolved, collision_stats = resolve_collisions_with_stats(
                move_plans,
                stationary,
                reservation_horizon=self.config.reservation_horizon,
            )
            self.last_collisions_avoided = collision_stats.blocked_moves
            self.last_blocked_moves = collision_stats.blocked_moves
            self.last_swaps_prevented = collision_stats.swaps_prevented
            try_unclog = self._should_try_unclog(
                state=state,
                blocked_moves=collision_stats.blocked_moves,
                total_moves=len(move_plans),
            )
            bot_positions = {bot.pos.as_tuple() for bot in state.bots}
            final_reserved = set(stationary)
            final_reserved.update(resolved.values())
            for bot_id, cur, desired in move_plans:
                actual = resolved[bot_id]
                if actual == cur:
                    if self.config.collision_aggressiveness == "detour" or bot_id in detour_preferred_bots:
                        detour = self._detour_step(cur, desired, grid, final_reserved | item_positions)
                        if detour != cur:
                            final_reserved.add(detour)
                            actions.append(
                                BotActionCommand(
                                    bot=bot_id,
                                    action=action_for_move(cur, detour),
                                )
                            )
                            continue
                    if try_unclog:
                        unclog = self._unclog_step(
                            start=cur,
                            desired=desired,
                            drop_off=drop_off,
                            grid=grid,
                            blocked=final_reserved | item_positions,
                            bot_positions=bot_positions,
                        )
                        if unclog != cur:
                            final_reserved.add(unclog)
                            actions.append(
                                BotActionCommand(
                                    bot=bot_id,
                                    action=action_for_move(cur, unclog),
                                )
                            )
                            continue
                    actions.append(BotActionCommand(bot=bot_id, action=BotAction.WAIT))
                else:
                    final_reserved.add(actual)
                    actions.append(BotActionCommand(
                        bot=bot_id,
                        action=action_for_move(cur, actual),
                    ))

        # Sort by bot id for clean output
        actions.sort(key=lambda a: a.bot)
        self._last_pick_attempt_by_bot = {
            a.bot: a.item_id
            for a in actions
            if a.action == BotAction.PICK_UP and a.item_id is not None
        }

        self.last_decision_ms = (time.perf_counter() - t0) * 1000
        if self.debug:
            action_str = ",".join(a.action.value for a in actions)
            print(f"  R{state.round:3d} score={state.score:3d} "
                  f"dt={self.last_decision_ms:.1f}ms "
                  f"actions=[{action_str}]")

        return RoundActions(actions=actions)

    def _elapsed_ms(self, t0: float) -> float:
        return (time.perf_counter() - t0) * 1000.0

    def _fallback_actions(self, state: GameState, t0: float) -> RoundActions:
        actions = [BotActionCommand(bot=bot.id, action=BotAction.WAIT) for bot in sorted(state.bots, key=lambda b: b.id)]
        self.last_decision_ms = self._elapsed_ms(t0)
        return RoundActions(actions=actions)

    def _would_oscillate(self, bot_id: int, target: tuple[int, int]) -> bool:
        _ = (bot_id, target)
        # Conservative: avoid hard movement blocks at this layer.
        # Target stickiness/replan penalties are handled in assignment.
        return False

    def _dump_state(self, state: GameState, grid: Grid) -> None:
        """Print a visual grid for first few rounds."""
        drop_off = (state.drop_off[0], state.drop_off[1])
        active = get_active_order(state)
        needed = compute_needed_items(state)

        print(f"\n  === Round {state.round} | Score {state.score} | "
              f"Grid {state.grid.width}x{state.grid.height} ===")
        print(f"  Drop-off: {drop_off}")
        print(f"  Active order: {active.id if active else 'None'} "
              f"needed={needed}")
        for b in state.bots:
            print(f"  Bot{b.id} @ {b.pos.as_tuple()} inv={b.inventory}")
        print(f"  Items on map: {len(state.items)}")
        for it in state.items[:8]:
            is_wall = grid.is_wall(it.position[0], it.position[1])
            neighbors = grid.walkable_neighbors_of(it.pos)
            print(f"    {it.id} ({it.type}) @ {it.pos.as_tuple()} "
                  f"is_wall={is_wall} walkable_neighbors={neighbors}")

        # Print ASCII grid
        print("  Grid:")
        for y in range(state.grid.height):
            row = "  "
            for x in range(state.grid.width):
                pos = (x, y)
                if any(b.pos.as_tuple() == pos for b in state.bots):
                    row += "B"
                elif pos == drop_off:
                    row += "D"
                elif any(it.pos.as_tuple() == pos for it in state.items):
                    row += "i"
                elif grid.is_wall(x, y):
                    row += "#"
                else:
                    row += "."
                row += " "
            print(row)

    # ── Internal helpers ───────────────────────────────────────────────

    def _execute_assignment(
        self,
        bot: BotInfo,
        assign: Assignment,
        grid: Grid,
        state: GameState,
        *,
        effective_policy: AssignmentPolicy | None = None,
        item_blocked: frozenset[tuple[int, int]] = frozenset(),
        active_pick_budget: Counter[str] | None = None,
        planned_active_picks: Counter[str] | None = None,
    ) -> BotActionCommand:
        bpos = bot.pos.as_tuple()
        drop_off = (state.drop_off[0], state.drop_off[1])
        policy = effective_policy or self.assignment_policy

        # ── OPPORTUNISTIC PICKUP ───────────────────────────────────
        # Before following assignment, check if bot is adjacent to ANY
        # needed item right now.  This catches edge cases where the
        # assignment picked a different item but we're next to one.
        if len(bot.inventory) < 3:
            needed = compute_needed_items(state)
            needed_counter = Counter(needed)
            if active_pick_budget is not None:
                needed_counter = Counter(active_pick_budget)
            if planned_active_picks is not None:
                for item_type, count in planned_active_picks.items():
                    if item_type in needed_counter:
                        needed_counter[item_type] -= count
            for item in sorted(state.items, key=lambda it: it.id):
                if needed_counter.get(item.type, 0) <= 0:
                    continue
                if self._is_pick_item_blocked(item.id, state.round):
                    continue
                ipos = item.pos.as_tuple()
                if self._can_pick_from(bpos, ipos):
                    return BotActionCommand(
                        bot=bot.id,
                        action=BotAction.PICK_UP,
                        item_id=item.id,
                    )

        # ── OPPORTUNISTIC DROP-OFF ─────────────────────────────────
        # If bot is standing ON drop-off with matching items, deliver now
        if bpos == drop_off and bot.inventory:
            matching = items_matching_active(bot, state)
            if matching:
                return BotActionCommand(bot=bot.id, action=BotAction.DROP_OFF)

        # Clear adjacent drop-off lane for bots carrying active-matching cargo.
        if (
            policy.clear_adjacent_dropoff_lane
            and
            abs(bpos[0] - drop_off[0]) + abs(bpos[1] - drop_off[1]) == 1
            and not items_matching_active(bot, state)
            and self._has_other_matching_carrier(
                state,
                drop_off,
                exclude_bot_id=bot.id,
                max_distance_to_dropoff=policy.clear_lane_distance,
            )
        ):
            sidestep = self._sidestep_from_dropoff(
                bot_id=bot.id,
                start=bpos,
                drop_off=drop_off,
                grid=grid,
                state=state,
                item_blocked=item_blocked,
            )
            if sidestep is None:
                sidestep = self._sidestep_from_dropoff(
                    bot_id=bot.id,
                    start=bpos,
                    drop_off=drop_off,
                    grid=grid,
                    state=state,
                    item_blocked=item_blocked,
                    ignore_bot_blockers=True,
                )
            if sidestep is not None:
                return sidestep

        # ── DELIVER ────────────────────────────────────────────────
        if assign.target_type == "deliver":
            if bpos == drop_off:
                # Check if we have matching items
                matching = items_matching_active(bot, state)
                if matching:
                    return BotActionCommand(bot=bot.id, action=BotAction.DROP_OFF)
                # No deliverable items on drop-off: vacate first to unblock traffic.
                vacate_cmd = self._vacate_dropoff(bot.id, bpos, grid, state, item_blocked=item_blocked)
                if vacate_cmd is not None:
                    return vacate_cmd
                # If we cannot vacate, optionally start pursuing active needs when inventory allows.
                needed = compute_needed_items(state)
                if needed and len(bot.inventory) < 3:
                    best_dist = 999999
                    best_pp = None
                    for item in sorted(state.items, key=lambda it: it.id):
                        if item.type in needed:
                            pps = find_all_pickup_positions(grid, item.pos.as_tuple())
                            for pp in pps:
                                d = bfs_distance(grid, bpos, pp, blocked=set(item_blocked))
                                if d < best_dist:
                                    best_dist = d
                                    best_pp = pp
                    if best_pp:
                        return self._move_toward(bot.id, bpos, best_pp, grid, state, item_blocked=item_blocked)
                return BotActionCommand(bot=bot.id, action=BotAction.WAIT)
            blocker_bot_id = self._dropoff_blocker_bot_id(state, drop_off)
            if (
                blocker_bot_id is not None
                and blocker_bot_id != bot.id
                and abs(bpos[0] - drop_off[0]) + abs(bpos[1] - drop_off[1]) == 1
            ):
                sidestep = self._sidestep_from_dropoff(
                    bot_id=bot.id,
                    start=bpos,
                    drop_off=drop_off,
                    grid=grid,
                    state=state,
                    item_blocked=item_blocked,
                )
                if sidestep is not None:
                    return sidestep
            # Move toward drop-off
            return self._move_toward(bot.id, bpos, drop_off, grid, state, item_blocked=item_blocked)

        if assign.target_type in ("pick_item", "pre_pick") and assign.item:
            if self._is_pick_item_blocked(assign.item.id, state.round):
                return BotActionCommand(bot=bot.id, action=BotAction.WAIT)
            item_pos = assign.item.pos.as_tuple()
            # Check if already adjacent → pick up
            if self._can_pick_from(bpos, item_pos):
                return BotActionCommand(
                    bot=bot.id,
                    action=BotAction.PICK_UP,
                    item_id=assign.item.id,
                )
            # Move toward pickup position (walkable cell adjacent to item shelf)
            target = assign.pickup_pos or bpos
            return self._move_toward(bot.id, bpos, target, grid, state, item_blocked=item_blocked)

        # ── IDLE — if bot has matching items, go deliver ──────────
        if bot.inventory and items_matching_active(bot, state):
            return self._move_toward(bot.id, bpos, drop_off, grid, state, item_blocked=item_blocked)
        # Bot idle with non-matching inventory (or empty) — go pick needed items
        if len(bot.inventory) < 3:
            needed = compute_needed_items(state)
            active_covered = self._active_needs_covered_by_team_inventory(state)
            rounds_left = max(0, int(state.max_rounds) - int(state.round))
            stage_endgame = rounds_left <= max(0, int(self.config.stage_nonmatching_endgame_rounds))
            if (
                active_covered
                and bot.inventory
                and bpos != drop_off
                and (
                    bool(self.config.stage_nonmatching_when_active_covered)
                    or stage_endgame
                )
            ):
                return self._move_toward(bot.id, bpos, drop_off, grid, state, item_blocked=item_blocked)
            target_types = list(needed) if needed and not active_covered else []
            if not target_types and active_covered:
                target_types = self._future_prefetch_fallback_types(state)
            if target_types:
                best_dist = 999999
                best_pp = None
                for item in sorted(state.items, key=lambda it: it.id):
                    if item.type in target_types:
                        pps = find_all_pickup_positions(grid, item.pos.as_tuple())
                        for pp in pps:
                            d = bfs_distance(grid, bpos, pp, blocked=set(item_blocked))
                            if d < best_dist:
                                best_dist = d
                                best_pp = pp
                if best_pp:
                    return self._move_toward(bot.id, bpos, best_pp, grid, state, item_blocked=item_blocked)
        if bpos == drop_off:
            vacate_cmd = self._vacate_dropoff(bot.id, bpos, grid, state, item_blocked=item_blocked)
            if vacate_cmd is not None:
                return vacate_cmd
        return BotActionCommand(bot=bot.id, action=BotAction.WAIT)

    def _active_needs_covered_by_team_inventory(self, state: GameState) -> bool:
        needed = Counter(compute_needed_items(state))
        if not needed:
            return True
        carried = Counter()
        for cur_bot in state.bots:
            for item_type in items_matching_active(cur_bot, state):
                carried[item_type] += 1
        return all(carried.get(item_type, 0) >= count for item_type, count in needed.items())

    def _future_prefetch_fallback_types(self, state: GameState) -> list[str]:
        lookahead = max(1, int(self.config.lookahead_orders))
        if self._order_forecast:
            out: list[str] = []
            start_idx = int(state.active_order_index)
            for depth in range(1, lookahead + 1):
                items = self._order_forecast.get(start_idx + depth)
                if not items:
                    break
                out.extend(items)
            if out:
                return out
        return compute_preview_items(state)

    def _reconcile_order_forecast(self, state: GameState) -> None:
        if not self._order_forecast:
            return
        mismatch_idx: int | None = None
        for order in state.orders:
            oid = str(order.id)
            if not oid.startswith("order_"):
                continue
            try:
                idx = int(oid.split("_", 1)[1])
            except (TypeError, ValueError):
                continue
            predicted = self._order_forecast.get(idx)
            if predicted is None:
                continue
            actual = [str(item_type) for item_type in order.items_required]
            if list(predicted) != actual:
                mismatch_idx = idx
                break
        if mismatch_idx is None:
            return
        self._order_forecast = {
            idx: items
            for idx, items in self._order_forecast.items()
            if idx < mismatch_idx
        }

    def _move_toward(
        self,
        bot_id: int,
        start: tuple[int, int],
        goal: tuple[int, int],
        grid: Grid,
        state: GameState,
        *,
        item_blocked: frozenset[tuple[int, int]] = frozenset(),
    ) -> BotActionCommand:
        """Compute one-step move toward *goal* using pathfinding."""
        if start == goal:
            return BotActionCommand(bot=bot_id, action=BotAction.WAIT)

        # Build blocked set from other bot positions + item positions
        blocked: set[tuple[int, int]] = set(item_blocked)
        for b in state.bots:
            if b.id != bot_id:
                blocked.add(b.pos.as_tuple())
        # Don't block the goal itself
        blocked.discard(goal)

        pathfn = astar_path if self.use_astar else bfs_shortest_path
        path = pathfn(grid, start, goal, blocked)

        if path is None or len(path) < 2:
            # Try without other-bot blocking (they might move), but keep items blocked
            path = pathfn(grid, start, goal, set(item_blocked) - {goal})
            if path is None or len(path) < 2:
                # Fallback: simple manhattan move
                return self._simple_move(bot_id, start, goal, grid, item_blocked=item_blocked)

        next_cell = path[1]
        if self.config.avoid_immediate_backtrack:
            history = self._position_history.get(bot_id)
            if history and len(history) >= 2 and history[-1] == start:
                prev_cell = history[-2]
                if next_cell == prev_cell:
                    alt = self._avoid_backtrack_step(
                        bot_id=bot_id,
                        start=start,
                        goal=goal,
                        reverse_step=prev_cell,
                        grid=grid,
                        state=state,
                        item_blocked=item_blocked,
                    )
                    if alt is not None:
                        next_cell = alt
        return BotActionCommand(
            bot=bot_id,
            action=action_for_move(start, next_cell),
        )

    def _avoid_backtrack_step(
        self,
        *,
        bot_id: int,
        start: tuple[int, int],
        goal: tuple[int, int],
        reverse_step: tuple[int, int],
        grid: Grid,
        state: GameState,
        item_blocked: frozenset[tuple[int, int]] = frozenset(),
    ) -> tuple[int, int] | None:
        blocked = set(item_blocked)
        for b in state.bots:
            if b.id != bot_id:
                blocked.add(b.pos.as_tuple())
        blocked_for_dist = set(item_blocked)
        blocked_for_dist.discard(goal)
        reverse_dist = bfs_distance(grid, reverse_step, goal, blocked=blocked_for_dist)
        candidates: list[tuple[int, int]] = []
        sx, sy = start
        for nx, ny in ((sx, sy - 1), (sx + 1, sy), (sx, sy + 1), (sx - 1, sy)):
            cell = (nx, ny)
            if cell == reverse_step:
                continue
            if not grid.is_walkable(nx, ny):
                continue
            if cell in blocked:
                continue
            candidates.append(cell)
        if not candidates:
            if self.config.wait_on_backtrack_conflict:
                return start
            return None
        ranked: list[tuple[int, tuple[int, int]]] = []
        for cell in candidates:
            dist = bfs_distance(grid, cell, goal, blocked=blocked_for_dist)
            ranked.append((dist, cell))
        ranked.sort(key=lambda row: (row[0], row[1][0], row[1][1]))
        best_dist, best_cell = ranked[0]
        if best_dist < 999999 and best_dist <= reverse_dist + max(0, int(self.config.backtrack_slack)):
            return best_cell
        if self.config.wait_on_backtrack_conflict:
            return start
        return None

    def _simple_move(
        self,
        bot_id: int,
        start: tuple[int, int],
        goal: tuple[int, int],
        grid: Grid,
        *,
        item_blocked: frozenset[tuple[int, int]] = frozenset(),
    ) -> BotActionCommand:
        """Fallback: take manhattan step toward goal, preferring bigger axis delta."""
        sx, sy = start
        gx, gy = goal
        dx, dy = gx - sx, gy - sy

        # Try the bigger axis first
        candidates = []
        if abs(dx) >= abs(dy):
            if dx > 0:
                candidates.append((sx + 1, sy, BotAction.MOVE_RIGHT))
            elif dx < 0:
                candidates.append((sx - 1, sy, BotAction.MOVE_LEFT))
            if dy > 0:
                candidates.append((sx, sy + 1, BotAction.MOVE_DOWN))
            elif dy < 0:
                candidates.append((sx, sy - 1, BotAction.MOVE_UP))
        else:
            if dy > 0:
                candidates.append((sx, sy + 1, BotAction.MOVE_DOWN))
            elif dy < 0:
                candidates.append((sx, sy - 1, BotAction.MOVE_UP))
            if dx > 0:
                candidates.append((sx + 1, sy, BotAction.MOVE_RIGHT))
            elif dx < 0:
                candidates.append((sx - 1, sy, BotAction.MOVE_LEFT))

        for nx, ny, action in candidates:
            if grid.is_walkable(nx, ny) and (nx, ny) not in item_blocked:
                return BotActionCommand(bot=bot_id, action=action)

        return BotActionCommand(bot=bot_id, action=BotAction.WAIT)

    def _detour_step(
        self,
        start: tuple[int, int],
        desired: tuple[int, int],
        grid: Grid,
        blocked: set[tuple[int, int]],
    ) -> tuple[int, int]:
        """Try a one-step local detour that avoids current reservations."""
        sx, sy = start
        candidates: list[tuple[int, int]] = []
        for nx, ny in ((sx, sy - 1), (sx + 1, sy), (sx, sy + 1), (sx - 1, sy)):
            if not grid.is_walkable(nx, ny):
                continue
            if (nx, ny) in blocked:
                continue
            candidates.append((nx, ny))
        if not candidates:
            return start
        candidates.sort(key=lambda pos: (abs(pos[0] - desired[0]) + abs(pos[1] - desired[1]), pos[0], pos[1]))
        return candidates[0]

    def _should_try_unclog(
        self,
        *,
        state: GameState,
        blocked_moves: int,
        total_moves: int,
    ) -> bool:
        if total_moves <= 1:
            return False
        if blocked_moves >= max(2, total_moves // 2):
            return True
        stall_half = max(3, int(self.config.stall_round_threshold) // 2)
        return self._stall_rounds >= stall_half and state.active_order_index is not None

    def _unclog_step(
        self,
        *,
        start: tuple[int, int],
        desired: tuple[int, int],
        drop_off: tuple[int, int],
        grid: Grid,
        blocked: set[tuple[int, int]],
        bot_positions: set[tuple[int, int]],
    ) -> tuple[int, int]:
        sx, sy = start
        ranked: list[tuple[int, int, int, int, int]] = []
        for nx, ny in ((sx, sy - 1), (sx + 1, sy), (sx, sy + 1), (sx - 1, sy)):
            cell = (nx, ny)
            if not grid.is_walkable(nx, ny):
                continue
            if cell in blocked:
                continue
            if cell == drop_off and desired != drop_off:
                continue
            dist_drop = abs(nx - drop_off[0]) + abs(ny - drop_off[1])
            dropoff_penalty = 1 if dist_drop <= 1 and desired != drop_off else 0
            local_crowd = sum(
                1
                for pos in bot_positions
                if pos != start and abs(pos[0] - nx) + abs(pos[1] - ny) <= 1
            )
            dist_desired = abs(nx - desired[0]) + abs(ny - desired[1])
            ranked.append((dropoff_penalty, local_crowd, dist_desired, nx, ny))
        if not ranked:
            return start
        ranked.sort()
        return (ranked[0][3], ranked[0][4])

    def _vacate_dropoff(
        self,
        bot_id: int,
        start: tuple[int, int],
        grid: Grid,
        state: GameState,
        *,
        item_blocked: frozenset[tuple[int, int]] = frozenset(),
    ) -> BotActionCommand | None:
        blocked = set(item_blocked)
        for other in state.bots:
            if other.id != bot_id:
                blocked.add(other.pos.as_tuple())
        sx, sy = start
        for nx, ny in ((sx, sy - 1), (sx + 1, sy), (sx - 1, sy), (sx, sy + 1)):
            if not grid.is_walkable(nx, ny):
                continue
            if (nx, ny) in blocked:
                continue
            return BotActionCommand(bot=bot_id, action=action_for_move(start, (nx, ny)))
        return None

    def _sidestep_from_dropoff(
        self,
        *,
        bot_id: int,
        start: tuple[int, int],
        drop_off: tuple[int, int],
        grid: Grid,
        state: GameState,
        item_blocked: frozenset[tuple[int, int]] = frozenset(),
        ignore_bot_blockers: bool = False,
    ) -> BotActionCommand | None:
        blocked = set(item_blocked)
        if not ignore_bot_blockers:
            for other in state.bots:
                if other.id != bot_id:
                    blocked.add(other.pos.as_tuple())

        candidates: list[tuple[int, int]] = []
        sx, sy = start
        for nx, ny in ((sx, sy - 1), (sx + 1, sy), (sx - 1, sy), (sx, sy + 1)):
            if (nx, ny) == drop_off:
                continue
            if not grid.is_walkable(nx, ny):
                continue
            if (nx, ny) in blocked:
                continue
            candidates.append((nx, ny))
        if not candidates:
            return None
        candidates.sort(
            key=lambda pos: (
                -(abs(pos[0] - drop_off[0]) + abs(pos[1] - drop_off[1])),
                pos[0],
                pos[1],
            )
        )
        target = candidates[0]
        return BotActionCommand(bot=bot_id, action=action_for_move(start, target))

    @staticmethod
    def _dropoff_blocker_bot_id(state: GameState, drop_off: tuple[int, int]) -> int | None:
        for bot in state.bots:
            if bot.pos.as_tuple() != drop_off:
                continue
            if items_matching_active(bot, state):
                return None
            return bot.id
        return None

    @staticmethod
    def _has_other_matching_carrier(
        state: GameState,
        drop_off: tuple[int, int],
        *,
        exclude_bot_id: int,
        max_distance_to_dropoff: int = 4,
    ) -> bool:
        for bot in state.bots:
            if bot.id == exclude_bot_id:
                continue
            if bot.pos.as_tuple() == drop_off:
                continue
            dist_to_drop = abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1])
            if dist_to_drop > max(1, int(max_distance_to_dropoff)):
                continue
            if items_matching_active(bot, state):
                return True
        return False

    @staticmethod
    def _move_target(pos: tuple[int, int], action: BotAction) -> tuple[int, int]:
        x, y = pos
        if action == BotAction.MOVE_UP:
            return (x, y - 1)
        if action == BotAction.MOVE_DOWN:
            return (x, y + 1)
        if action == BotAction.MOVE_LEFT:
            return (x - 1, y)
        if action == BotAction.MOVE_RIGHT:
            return (x + 1, y)
        return pos

    @staticmethod
    def _can_pick_from(bot_pos: tuple[int, int], item_pos: tuple[int, int]) -> bool:
        return abs(bot_pos[0] - item_pos[0]) + abs(bot_pos[1] - item_pos[1]) == 1

    def _update_pickup_feedback(self, state: GameState) -> None:
        present_item_ids = {item.id for item in state.items}
        for item_id in list(self._blocked_pick_items_until_round):
            if item_id not in present_item_ids:
                self._blocked_pick_items_until_round.pop(item_id, None)
                self._pickup_fail_counts.pop(item_id, None)

        for item_id, until_round in list(self._blocked_pick_items_until_round.items()):
            if until_round <= state.round:
                self._blocked_pick_items_until_round.pop(item_id, None)
                self._pickup_fail_counts.pop(item_id, None)

        for bot in state.bots:
            prev_inv = self._prev_inventory_by_bot.get(bot.id)
            attempted = self._last_pick_attempt_by_bot.get(bot.id)
            if attempted is not None and prev_inv is not None:
                cur_len = len(bot.inventory)
                prev_len = len(prev_inv)
                attempted_present = attempted in present_item_ids
                pickup_succeeded = (cur_len > prev_len) or (not attempted_present)
                if not pickup_succeeded:
                    fails = self._pickup_fail_counts.get(attempted, 0) + 1
                    self._pickup_fail_counts[attempted] = fails
                    threshold = max(1, int(self.config.pickup_fail_blacklist_threshold))
                    if fails >= threshold:
                        cooloff = max(1, int(self.config.pickup_fail_blacklist_rounds))
                        self._blocked_pick_items_until_round[attempted] = state.round + cooloff
                else:
                    self._pickup_fail_counts.pop(attempted, None)
                    self._blocked_pick_items_until_round.pop(attempted, None)
            self._prev_inventory_by_bot[bot.id] = tuple(bot.inventory)

    def _blocked_pick_item_ids(self, round_num: int) -> set[str]:
        return {
            item_id
            for item_id, until_round in self._blocked_pick_items_until_round.items()
            if until_round > round_num
        }

    def _is_pick_item_blocked(self, item_id: str, round_num: int) -> bool:
        return self._blocked_pick_items_until_round.get(item_id, 0) > round_num

    def _effective_policy(self, state: GameState) -> AssignmentPolicy:
        active = get_active_order(state)
        active_delivered = len(active.items_delivered) if active is not None else 0
        if self._prev_score is None:
            self._stall_rounds = 0
        else:
            progressed = (
                state.score > self._prev_score
                or state.active_order_index != self._prev_active_order_index
                or active_delivered > (self._prev_active_delivered or 0)
            )
            if progressed:
                self._stall_rounds = 0
            else:
                self._stall_rounds += 1

        self._prev_score = state.score
        self._prev_active_order_index = state.active_order_index
        self._prev_active_delivered = active_delivered

        threshold = max(1, int(self.config.stall_round_threshold))
        deadlock_cycle = False
        hist = list(self._joint_signature_history)
        if len(hist) >= 6 and hist[-1] == hist[-3] == hist[-5] and hist[-2] == hist[-4]:
            deadlock_cycle = True
            self._stall_rounds = max(self._stall_rounds, threshold)

        if self._stall_rounds >= threshold:
            rec = max(1, int(self.config.stall_recovery_rounds))
            new_until = state.round + rec
            if new_until > self._stall_recovery_until_round:
                # Recovery mode is a hard reset of short-term tactical memory.
                # This helps escape cascaded deadlocks/false pickup blacklists.
                self._sticky_targets.clear()
                self._pickup_fail_counts.clear()
                self._blocked_pick_items_until_round.clear()
                self._stall_recovery_until_round = new_until
                if deadlock_cycle:
                    self.last_replans += 1

        if state.round < self._stall_recovery_until_round:
            return replace(
                self.assignment_policy,
                preview_weight=max(0.0, float(self.config.stall_recovery_preview_weight)),
                force_dropoff_for_full_nonmatching=bool(self.config.stall_recovery_force_dropoff),
                strict_active_priority=bool(self.config.stall_recovery_strict_active),
                clear_adjacent_dropoff_lane=True,
                clear_lane_distance=max(2, int(self.config.clear_lane_distance)),
            )
        return self.assignment_policy
