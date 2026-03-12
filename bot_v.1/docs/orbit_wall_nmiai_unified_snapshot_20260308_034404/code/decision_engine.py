"""DecisionEngine — per-round orchestrator for the grocery bot swarm."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
import hashlib
import time
from typing import Optional

from .assignment import Assignment, AssignmentPolicy, _pickup_zone_columns, _zone_center_x, assign_bots
from .collision import action_for_move, resolve_collisions_with_stats
from .cooperative_path import largest_conflict_component, plan_windowed_next_steps
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
from .pathfinding import astar_path, bfs_distance, bfs_distance_map, bfs_shortest_path, find_all_pickup_positions

WAIT_REASON_KEYS = (
    "wait_due_to_stopline",
    "wait_due_to_oneway",
    "wait_due_to_whca_no_plan_or_budget",
    "wait_due_to_collision_block",
    "wait_due_to_invalid_move_target",
    "wait_due_to_no_assignment",
    "wait_due_to_vacate_dropoff_failed",
)


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
    overflow_prefetch_when_active_assigned: bool = False
    overflow_prefetch_round_limit: int = 0
    strict_active_priority: bool = False
    strict_active_release_completion: float = 1.0
    prefetch_release_use_delivered_completion: bool = False
    force_dropoff_for_full_nonmatching: bool = False
    always_deliver_matching: bool = False
    stall_delivery_breaker_enabled: bool = False
    stall_delivery_breaker_rounds: int = 7
    avoid_dropoff_block_when_matching: bool = True
    max_concurrent_deliverers: int = 2
    adaptive_deliver_queue: bool = False
    deliver_queue_min: int = 1
    deliver_queue_max: int = 3
    assignment_strategy: str = "greedy"  # greedy | auction
    auction_option_depth: int = 12
    auction_allow_skip: bool = True
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
    allow_same_shelf_for_active_duplicates: bool = False
    active_duplicate_same_shelf_min_gap: int = 2
    stage_nonmatching_when_active_covered: bool = False
    stage_nonmatching_endgame_rounds: int = 0
    tie_break_seed: int = 0
    tie_break_dynamic: bool = False
    exploration_epsilon: float = 0.0
    escape_mode_enabled: bool = False
    escape_tie_break_seed_offset: int = 1009
    escape_clear_lane_distance: int = 5
    whca_window: int = 8
    whca_enabled: bool = False
    whca_blocked_moves_trigger: int = 2
    whca_congestion_radius: int = 2
    whca_congestion_bots_trigger: int = 2
    whca_subset_conflicts_only: bool = True
    whca_conflict_component_min_size: int = 3
    whca_soft_budget_ms: float = 6.0
    congestion_auction_enabled: bool = False
    congestion_auction_dropoff_trigger: float = 0.67
    congestion_auction_corridor_trigger: float = 0.67
    congestion_auction_blocked_trigger: int = 2
    congestion_auction_option_depth: int = 9
    congestion_auction_dropoff_penalty: float = 0.75
    congestion_auction_corridor_penalty: float = 0.75
    one_way_aisle_enabled: bool = False
    one_way_aisle_trigger_density: float = 0.67
    one_way_aisle_blocked_trigger: int = 1
    two_step_trip_weight: float = 0.0
    two_step_trip_min_gain: int = 2
    two_step_order_bonus_weight: float = 1.0
    two_step_max_extra_steps: int = 2
    two_step_completion_delay_threshold: int = 1
    predicted_dropoff_density_weight: float = 0.0
    predicted_corridor_density_weight: float = 0.0
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
    startup_release_enabled: bool = False
    startup_release_rounds: int = 18
    startup_release_one_way_exempt: bool = False
    idle_stage_when_no_visible_targets: bool = False
    transition_excess_matching_hold_enabled: bool = False
    transition_excess_matching_hold_max_dropoff_dist: int = 6
    transition_excess_matching_hold_max_pickup_steps: int = 3
    transition_stash_enabled: bool = False
    transition_stash_completion_ratio: float = 0.85
    transition_stash_remaining_items: int = 2
    transition_stash_finisher_count: int = 2
    transition_stash_preview_bonus: float = 2.0
    startup_release_v3_enabled: bool = False
    startup_release_stuck_rounds: int = 2
    startup_release_max_bots_per_round: int = 2

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
            "overflow_prefetch_when_active_assigned": self.overflow_prefetch_when_active_assigned,
            "overflow_prefetch_round_limit": self.overflow_prefetch_round_limit,
            "strict_active_priority": self.strict_active_priority,
            "strict_active_release_completion": self.strict_active_release_completion,
            "prefetch_release_use_delivered_completion": self.prefetch_release_use_delivered_completion,
            "force_dropoff_for_full_nonmatching": self.force_dropoff_for_full_nonmatching,
            "always_deliver_matching": self.always_deliver_matching,
            "stall_delivery_breaker_enabled": self.stall_delivery_breaker_enabled,
            "stall_delivery_breaker_rounds": self.stall_delivery_breaker_rounds,
            "avoid_dropoff_block_when_matching": self.avoid_dropoff_block_when_matching,
            "max_concurrent_deliverers": self.max_concurrent_deliverers,
            "adaptive_deliver_queue": self.adaptive_deliver_queue,
            "deliver_queue_min": self.deliver_queue_min,
            "deliver_queue_max": self.deliver_queue_max,
            "assignment_strategy": self.assignment_strategy,
            "auction_option_depth": self.auction_option_depth,
            "auction_allow_skip": self.auction_allow_skip,
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
            "allow_same_shelf_for_active_duplicates": self.allow_same_shelf_for_active_duplicates,
            "active_duplicate_same_shelf_min_gap": self.active_duplicate_same_shelf_min_gap,
            "stage_nonmatching_when_active_covered": self.stage_nonmatching_when_active_covered,
            "stage_nonmatching_endgame_rounds": self.stage_nonmatching_endgame_rounds,
            "tie_break_seed": self.tie_break_seed,
            "tie_break_dynamic": self.tie_break_dynamic,
            "exploration_epsilon": self.exploration_epsilon,
            "escape_mode_enabled": self.escape_mode_enabled,
            "escape_tie_break_seed_offset": self.escape_tie_break_seed_offset,
            "escape_clear_lane_distance": self.escape_clear_lane_distance,
            "whca_window": self.whca_window,
            "whca_enabled": self.whca_enabled,
            "whca_blocked_moves_trigger": self.whca_blocked_moves_trigger,
            "whca_congestion_radius": self.whca_congestion_radius,
            "whca_congestion_bots_trigger": self.whca_congestion_bots_trigger,
            "whca_subset_conflicts_only": self.whca_subset_conflicts_only,
            "whca_conflict_component_min_size": self.whca_conflict_component_min_size,
            "whca_soft_budget_ms": self.whca_soft_budget_ms,
            "congestion_auction_enabled": self.congestion_auction_enabled,
            "congestion_auction_dropoff_trigger": self.congestion_auction_dropoff_trigger,
            "congestion_auction_corridor_trigger": self.congestion_auction_corridor_trigger,
            "congestion_auction_blocked_trigger": self.congestion_auction_blocked_trigger,
            "congestion_auction_option_depth": self.congestion_auction_option_depth,
            "congestion_auction_dropoff_penalty": self.congestion_auction_dropoff_penalty,
            "congestion_auction_corridor_penalty": self.congestion_auction_corridor_penalty,
            "one_way_aisle_enabled": self.one_way_aisle_enabled,
            "one_way_aisle_trigger_density": self.one_way_aisle_trigger_density,
            "one_way_aisle_blocked_trigger": self.one_way_aisle_blocked_trigger,
            "two_step_trip_weight": self.two_step_trip_weight,
            "two_step_trip_min_gain": self.two_step_trip_min_gain,
            "two_step_order_bonus_weight": self.two_step_order_bonus_weight,
            "two_step_max_extra_steps": self.two_step_max_extra_steps,
            "two_step_completion_delay_threshold": self.two_step_completion_delay_threshold,
            "predicted_dropoff_density_weight": self.predicted_dropoff_density_weight,
            "predicted_corridor_density_weight": self.predicted_corridor_density_weight,
            "dropoff_stop_line_enabled": self.dropoff_stop_line_enabled,
            "dropoff_stop_line_k": self.dropoff_stop_line_k,
            "dropoff_stop_line_radius": self.dropoff_stop_line_radius,
            "dropoff_stop_line_trigger_density": self.dropoff_stop_line_trigger_density,
            "anti_no_assignment_enabled": self.anti_no_assignment_enabled,
            "secondary_assignment_enabled": self.secondary_assignment_enabled,
            "secondary_duplicate_support": self.secondary_duplicate_support,
            "anti_starvation_enabled": self.anti_starvation_enabled,
            "anti_starvation_rounds": self.anti_starvation_rounds,
            "anti_starvation_bonus": self.anti_starvation_bonus,
            "secondary_max_distance": self.secondary_max_distance,
            "secondary_reposition_empty_only": self.secondary_reposition_empty_only,
            "startup_release_enabled": self.startup_release_enabled,
            "startup_release_rounds": self.startup_release_rounds,
            "startup_release_one_way_exempt": self.startup_release_one_way_exempt,
            "idle_stage_when_no_visible_targets": self.idle_stage_when_no_visible_targets,
            "transition_excess_matching_hold_enabled": self.transition_excess_matching_hold_enabled,
            "transition_excess_matching_hold_max_dropoff_dist": self.transition_excess_matching_hold_max_dropoff_dist,
            "transition_excess_matching_hold_max_pickup_steps": self.transition_excess_matching_hold_max_pickup_steps,
            "transition_stash_enabled": self.transition_stash_enabled,
            "transition_stash_completion_ratio": self.transition_stash_completion_ratio,
            "transition_stash_remaining_items": self.transition_stash_remaining_items,
            "transition_stash_finisher_count": self.transition_stash_finisher_count,
            "transition_stash_preview_bonus": self.transition_stash_preview_bonus,
            "startup_release_v3_enabled": self.startup_release_v3_enabled,
            "startup_release_stuck_rounds": self.startup_release_stuck_rounds,
            "startup_release_max_bots_per_round": self.startup_release_max_bots_per_round,
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
        capture_debug: bool = False,
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
            overflow_prefetch_when_active_assigned=self.config.overflow_prefetch_when_active_assigned,
            overflow_prefetch_round_limit=self.config.overflow_prefetch_round_limit,
            strict_active_priority=self.config.strict_active_priority,
            strict_active_release_completion=self.config.strict_active_release_completion,
            prefetch_release_use_delivered_completion=self.config.prefetch_release_use_delivered_completion,
            force_dropoff_for_full_nonmatching=self.config.force_dropoff_for_full_nonmatching,
            always_deliver_matching=self.config.always_deliver_matching,
            avoid_dropoff_block_when_matching=self.config.avoid_dropoff_block_when_matching,
            max_concurrent_deliverers=self.config.max_concurrent_deliverers,
            adaptive_deliver_queue=self.config.adaptive_deliver_queue,
            deliver_queue_min=self.config.deliver_queue_min,
            deliver_queue_max=self.config.deliver_queue_max,
            assignment_strategy=self.config.assignment_strategy,
            auction_option_depth=self.config.auction_option_depth,
            auction_allow_skip=self.config.auction_allow_skip,
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
            allow_same_shelf_for_active_duplicates=self.config.allow_same_shelf_for_active_duplicates,
            active_duplicate_same_shelf_min_gap=self.config.active_duplicate_same_shelf_min_gap,
            tie_break_seed=self.config.tie_break_seed,
            tie_break_dynamic=self.config.tie_break_dynamic,
            two_step_trip_weight=self.config.two_step_trip_weight,
            two_step_trip_min_gain=self.config.two_step_trip_min_gain,
            two_step_order_bonus_weight=self.config.two_step_order_bonus_weight,
            two_step_max_extra_steps=self.config.two_step_max_extra_steps,
            two_step_completion_delay_threshold=self.config.two_step_completion_delay_threshold,
            predicted_dropoff_density_weight=self.config.predicted_dropoff_density_weight,
            predicted_corridor_density_weight=self.config.predicted_corridor_density_weight,
            stall_round_threshold=self.config.stall_round_threshold,
            dropoff_stop_line_enabled=self.config.dropoff_stop_line_enabled,
            dropoff_stop_line_k=self.config.dropoff_stop_line_k,
            dropoff_stop_line_radius=self.config.dropoff_stop_line_radius,
            dropoff_stop_line_trigger_density=self.config.dropoff_stop_line_trigger_density,
            anti_no_assignment_enabled=self.config.anti_no_assignment_enabled,
            secondary_assignment_enabled=self.config.secondary_assignment_enabled,
            secondary_duplicate_support=self.config.secondary_duplicate_support,
            anti_starvation_enabled=self.config.anti_starvation_enabled,
            anti_starvation_rounds=self.config.anti_starvation_rounds,
            anti_starvation_bonus=self.config.anti_starvation_bonus,
            secondary_max_distance=self.config.secondary_max_distance,
            secondary_reposition_empty_only=self.config.secondary_reposition_empty_only,
            transition_stash_enabled=self.config.transition_stash_enabled,
            transition_stash_completion_ratio=self.config.transition_stash_completion_ratio,
            transition_stash_remaining_items=self.config.transition_stash_remaining_items,
            transition_stash_finisher_count=self.config.transition_stash_finisher_count,
            transition_stash_preview_bonus=self.config.transition_stash_preview_bonus,
        )
        self.use_astar = use_astar
        self.debug = debug
        self.verbose = verbose  # extra detailed per-round output
        self.capture_debug = capture_debug
        self.last_decision_ms: float = 0.0
        self.last_collisions_avoided: int = 0
        self.last_blocked_moves: int = 0
        self.last_swaps_prevented: int = 0
        self.last_replans: int = 0
        self.last_fallback_used: bool = False
        self.last_escape_mode_active: bool = False
        self.last_whca_used: bool = False
        self.last_whca_ms: float = 0.0
        self.last_transition_active: bool = False
        self.last_transition_hold_bot_ids: set[int] = set()
        self.last_round_telemetry: dict[str, float] = {}
        self.last_wait_reason_counts: dict[str, int] = {key: 0 for key in WAIT_REASON_KEYS}
        self.last_assignment_snapshot: dict[int, dict[str, object]] = {}
        self.last_pre_collision_actions: dict[int, dict[str, object]] = {}
        self.last_move_debug: dict[int, dict[str, object]] = {}
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
        self._blocked_moves_history: deque[int] = deque(maxlen=5)
        self._wait_reason_by_bot: dict[int, str] = {}
        self._round_wait_reason_by_bot: dict[int, str] = {}
        self._primary_assignment_miss_streak_by_bot: dict[int, int] = {}
        self._wait_no_assignment_streak_by_bot: dict[int, int] = {}
        self._startup_stack_origin: tuple[int, int] | None = None
        self._startup_max_exited: int = 0
        self._startup_stuck_rounds: int = 0

    # ── Public API ─────────────────────────────────────────────────────

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()
        self.last_collisions_avoided = 0
        self.last_blocked_moves = 0
        self.last_swaps_prevented = 0
        self.last_replans = 0
        self.last_fallback_used = False
        self.last_whca_used = False
        self.last_whca_ms = 0.0
        self.last_transition_active = False
        self.last_transition_hold_bot_ids = set()
        self.last_wait_reason_counts = {key: 0 for key in WAIT_REASON_KEYS}
        self.last_assignment_snapshot = {}
        self.last_pre_collision_actions = {}
        self.last_move_debug = {}
        self._wait_reason_by_bot = {}
        self._round_wait_reason_by_bot = {}
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
        dropoff_zone_density = self._dropoff_zone_density(state, drop_off)
        corridor_density = self._corridor_density(state, grid)
        prev_active_order_index = self._prev_active_order_index
        active_transition = (
            prev_active_order_index is not None
            and state.active_order_index is not None
            and int(state.active_order_index) != int(prev_active_order_index)
        )
        self.last_transition_active = bool(active_transition)
        policy = self._effective_policy(state)
        policy = self._apply_regime_overrides(
            policy=policy,
            dropoff_zone_density=dropoff_zone_density,
            corridor_density=corridor_density,
        )
        effective_collision_aggressiveness = self.config.collision_aggressiveness
        if self.last_escape_mode_active and self.config.escape_mode_enabled:
            effective_collision_aggressiveness = "detour"

        # Items on the map block movement but must NOT be merged into
        # grid._walls — that destroys pickup-position discovery.
        # Instead we thread item_positions as a "blocked" set through
        # pathfinding calls so BFS/A* won't route *through* items,
        # while walkable_neighbors_of() still reports floor tiles next
        # to item shelves correctly.
        item_positions = frozenset(
            (it.position[0], it.position[1]) for it in state.items
        )

        fastpath_actions = self._startup_release_v3_fastpath(
            state=state,
            grid=grid,
            drop_off=drop_off,
            item_blocked=item_positions,
            t0=t0,
        )
        if fastpath_actions is not None:
            return fastpath_actions

        if self.verbose and state.round < 10:
            self._dump_state(state, grid)

        # Get assignments for all bots (clean grid — items NOT in walls)
        transition_hold_bot_ids = self._transition_excess_matching_hold_bot_ids(
            state=state,
            grid=grid,
            item_blocked=item_positions,
            drop_off=drop_off,
            active_transition=active_transition,
        )
        self.last_transition_hold_bot_ids = set(transition_hold_bot_ids)
        primary_miss_streak_snapshot = dict(self._primary_assignment_miss_streak_by_bot)
        assignments = assign_bots(
            state,
            grid,
            item_blocked=item_positions,
            policy=policy,
            sticky_targets=self._sticky_targets,
            order_forecast=self._order_forecast,
            active_order_index=state.active_order_index,
            blocked_item_ids=blocked_pick_ids,
            defer_deliver_bot_ids=transition_hold_bot_ids,
            force_active_only_bot_ids=transition_hold_bot_ids,
            primary_assignment_miss_streak_by_bot=primary_miss_streak_snapshot,
        )
        primary_assigned_bot_ids = {
            int(bot_id)
            for bot_id, assign in assignments.items()
            if assign.target_type in ("pick_item", "pre_pick", "deliver")
        }
        secondary_assigned_bot_ids = {
            int(bot_id)
            for bot_id, assign in assignments.items()
            if str(assign.source).startswith("secondary_")
        }
        for bot in state.bots:
            bid = int(bot.id)
            if bid in primary_assigned_bot_ids:
                self._primary_assignment_miss_streak_by_bot[bid] = 0
            else:
                self._primary_assignment_miss_streak_by_bot[bid] = (
                    self._primary_assignment_miss_streak_by_bot.get(bid, 0) + 1
                )
        if self.capture_debug:
            self.last_assignment_snapshot = {
                int(bot_id): self._assignment_debug_payload(assign)
                for bot_id, assign in assignments.items()
            }
            for bot_id in transition_hold_bot_ids:
                payload = self.last_assignment_snapshot.setdefault(int(bot_id), {"target_type": "none"})
                payload["transition_hold_deliver"] = True
        startup_commands, startup_goals = self._startup_release_plan(
            state=state,
            grid=grid,
            item_blocked=item_positions,
            assignments=assignments,
            drop_off=drop_off,
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

        mean_dist_to_targets = self._mean_dist_to_targets(assignments, state, drop_off)
        assignment_churn = float(self.last_replans)

        if self._elapsed_ms(t0) >= self.config.decision_hard_cap_ms:
            self.last_fallback_used = True
            self._last_pick_attempt_by_bot = {}
            return self._fallback_actions(state, t0)

        # Convert assignments into concrete single-step actions
        actions: list[BotActionCommand] = []
        # Track planned next positions for collision resolution
        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        move_goals: dict[int, tuple[int, int]] = {}
        detour_preferred_bots: set[int] = set()
        stationary: set[tuple[int, int]] = set()
        active_pick_budget = Counter(compute_needed_items(state))
        planned_active_picks: Counter[str] = Counter()
        item_type_by_id = {item.id: item.type for item in state.items}
        distance_map_cache: dict[tuple[int, int], dict[tuple[int, int], int]] = {}

        for bot in sorted(state.bots, key=lambda b: b.id):
            bpos = bot.pos.as_tuple()
            assign = assignments.get(bot.id)
            if assign is None:
                wait_cmd = BotActionCommand(bot=bot.id, action=BotAction.WAIT)
                self._store_pre_collision_action(
                    bot=bot,
                    assign=None,
                    cmd=wait_cmd,
                    movement_target=None,
                )
                actions.append(wait_cmd)
                self._record_wait(bot.id, assign=None, explicit_reason="wait_due_to_no_assignment")
                stationary.add(bpos)
                continue

            if self.verbose:
                item_info = ""
                if assign.item:
                    item_info = f" item={assign.item.id}({assign.item.type})@{assign.item.pos.as_tuple()}"
                    item_info += f" pickup_pos={assign.pickup_pos}"
                print(f"    Bot{bot.id}@{bpos} inv={bot.inventory} -> "
                      f"{assign.target_type}{item_info} drop_off={assign.drop_off}")

            if bot.id in startup_commands:
                cmd = startup_commands[bot.id]
            else:
                cmd = self._execute_assignment(
                    bot,
                    assign,
                    grid,
                    state,
                    effective_policy=policy,
                    item_blocked=item_positions,
                    active_pick_budget=active_pick_budget,
                    planned_active_picks=planned_active_picks,
                    distance_map_cache=distance_map_cache,
                )
                cmd = self._maybe_explore_command(
                    bot=bot,
                    cmd=cmd,
                    state=state,
                    grid=grid,
                    item_blocked=item_positions,
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
                self._store_pre_collision_action(
                    bot=bot,
                    assign=assign,
                    cmd=cmd,
                    movement_target=None,
                )
                actions.append(cmd)
                if cmd.action == BotAction.WAIT:
                    self._record_wait(bot.id, assign=assign)
                stationary.add(bpos)
            else:
                # Movement — compute target cell for collision check
                target = self._move_target(bpos, cmd.action)
                if self._would_oscillate(bot.id, target):
                    wait_cmd = BotActionCommand(bot=bot.id, action=BotAction.WAIT)
                    self._store_pre_collision_action(
                        bot=bot,
                        assign=assign,
                        cmd=wait_cmd,
                        movement_target=None,
                    )
                    actions.append(wait_cmd)
                    self._record_wait(bot.id, assign=assign)
                    stationary.add(bpos)
                    continue
                if grid.is_walkable(target[0], target[1]) and target not in item_positions:
                    self._store_pre_collision_action(
                        bot=bot,
                        assign=assign,
                        cmd=cmd,
                        movement_target=target,
                    )
                    move_plans.append((bot.id, bpos, target))
                    if bot.id in startup_goals:
                        move_goals[bot.id] = startup_goals[bot.id]
                    elif assign.target_type == "deliver":
                        move_goals[bot.id] = drop_off
                    elif assign.target_type in ("pick_item", "pre_pick", "secondary_reposition") and assign.pickup_pos is not None:
                        move_goals[bot.id] = assign.pickup_pos
                    else:
                        move_goals[bot.id] = target
                    if assign.target_type == "deliver" and items_matching_active(bot, state):
                        detour_preferred_bots.add(bot.id)
                else:
                    # Can't move there - wait instead
                    if self.verbose:
                        print(f"      => BLOCKED at {target}, waiting instead")
                    wait_cmd = BotActionCommand(bot=bot.id, action=BotAction.WAIT)
                    self._store_pre_collision_action(
                        bot=bot,
                        assign=assign,
                        cmd=wait_cmd,
                        movement_target=None,
                    )
                    actions.append(wait_cmd)
                    self._record_wait(bot.id, assign=assign, explicit_reason="wait_due_to_invalid_move_target")
                    stationary.add(bpos)

        # Resolve collisions among moving bots
        if move_plans:
            whca_fallback_bot_ids: set[int] = set()
            startup_one_way_exempt_ids = (
                set(startup_commands)
                if self.config.startup_release_enabled and self.config.startup_release_one_way_exempt
                else set()
            )
            if self._should_apply_one_way_aisle(corridor_density=corridor_density):
                filtered_plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
                for bot_id, cur, desired in move_plans:
                    if (
                        bot_id not in startup_one_way_exempt_ids
                        and
                        bot_id not in detour_preferred_bots
                        and self._violates_one_way_aisle(start=cur, target=desired, grid=grid)
                    ):
                        self._mark_move_debug(bot_id, one_way_blocked=True)
                        actions.append(BotActionCommand(bot=bot_id, action=BotAction.WAIT))
                        self._record_wait(
                            bot_id,
                            assign=assignments.get(bot_id),
                            explicit_reason="wait_due_to_oneway",
                        )
                        stationary.add(cur)
                        continue
                    filtered_plans.append((bot_id, cur, desired))
                move_plans = filtered_plans
                for bot_id in startup_one_way_exempt_ids:
                    self._mark_move_debug(bot_id, startup_release_one_way_exempt=True)

            used_whca = False
            whca_subset_ids: set[int] = set()
            whca_next: dict[int, tuple[int, int]] = {}
            if move_plans and self._should_use_whca(state=state, drop_off=drop_off, total_moves=len(move_plans)):
                remaining_ms = self.config.decision_hard_cap_ms - self._elapsed_ms(t0)
                if remaining_ms >= 4.0:
                    whca_t0 = time.perf_counter()
                    try:
                        plans_for_whca = list(move_plans)
                        occupied_for_whca = set(stationary)
                        if self.config.whca_subset_conflicts_only:
                            component = largest_conflict_component(
                                grid=grid,
                                plans=move_plans,
                                goals_by_bot=move_goals,
                                blocked=item_positions,
                                window=max(1, int(self.config.whca_window)),
                            )
                            min_size = max(2, int(self.config.whca_conflict_component_min_size))
                            if len(component) >= min_size:
                                whca_subset_ids = set(component)
                                plans_for_whca = [row for row in move_plans if row[0] in whca_subset_ids]
                                occupied_for_whca.update(cur for bot_id, cur, _ in move_plans if bot_id not in whca_subset_ids)
                            else:
                                plans_for_whca = []
                        else:
                            whca_subset_ids = {bot_id for bot_id, _cur, _desired in move_plans}

                        if plans_for_whca:
                            for bot_id, _cur, _desired in plans_for_whca:
                                self._mark_move_debug(bot_id, whca_requested=True)
                            whca_next = plan_windowed_next_steps(
                                grid=grid,
                                plans=plans_for_whca,
                                goals_by_bot=move_goals,
                                occupied=occupied_for_whca,
                                blocked=item_positions,
                                window=max(1, int(self.config.whca_window)),
                                deliverer_ids=detour_preferred_bots,
                            )
                            whca_ms = (time.perf_counter() - whca_t0) * 1000.0
                            soft_budget = max(
                                1.0,
                                min(float(self.config.whca_soft_budget_ms), max(1.0, remaining_ms - 1.0)),
                            )
                            self.last_whca_ms = whca_ms
                            if whca_ms <= soft_budget:
                                self.last_whca_used = True
                                used_whca = True
                                for bot_id in whca_next:
                                    self._mark_move_debug(
                                        bot_id,
                                        whca_applied=True,
                                        whca_next=list(whca_next.get(bot_id, plans_for_whca[0][1])),
                                    )
                            else:
                                self.last_whca_used = False
                                whca_fallback_bot_ids = {bot_id for bot_id, _cur, _desired in move_plans}
                                for bot_id in whca_fallback_bot_ids:
                                    self._mark_move_debug(bot_id, whca_fallback=True)
                        else:
                            self.last_whca_used = False
                            self.last_whca_ms = 0.0
                    except Exception:
                        self.last_whca_used = False
                        self.last_whca_ms = 0.0
                        whca_fallback_bot_ids = {bot_id for bot_id, _cur, _desired in move_plans}
                        for bot_id in whca_fallback_bot_ids:
                            self._mark_move_debug(bot_id, whca_fallback=True)
                else:
                    whca_fallback_bot_ids = {bot_id for bot_id, _cur, _desired in move_plans}
                    for bot_id in whca_fallback_bot_ids:
                        self._mark_move_debug(bot_id, whca_fallback=True)

            if move_plans and used_whca:
                if not whca_subset_ids or len(whca_subset_ids) >= len(move_plans):
                    blocked_here = 0
                    for bot_id, cur, _desired in move_plans:
                        actual = whca_next.get(bot_id, cur)
                        if actual == cur:
                            blocked_here += 1
                            actions.append(BotActionCommand(bot=bot_id, action=BotAction.WAIT))
                            self._record_wait(
                                bot_id,
                                assign=assignments.get(bot_id),
                                explicit_reason="wait_due_to_collision_block",
                            )
                        else:
                            actions.append(BotActionCommand(bot=bot_id, action=action_for_move(cur, actual)))
                    self.last_collisions_avoided += blocked_here
                    self.last_blocked_moves += blocked_here
                else:
                    subset_blocked = 0
                    subset_reserved: set[tuple[int, int]] = set(stationary)
                    for bot_id, cur, _desired in move_plans:
                        if bot_id not in whca_subset_ids:
                            continue
                        actual = whca_next.get(bot_id, cur)
                        subset_reserved.add(actual)
                        if actual == cur:
                            subset_blocked += 1
                            actions.append(BotActionCommand(bot=bot_id, action=BotAction.WAIT))
                            self._record_wait(
                                bot_id,
                                assign=assignments.get(bot_id),
                                explicit_reason="wait_due_to_collision_block",
                            )
                        else:
                            actions.append(BotActionCommand(bot=bot_id, action=action_for_move(cur, actual)))
                    self.last_collisions_avoided += subset_blocked
                    self.last_blocked_moves += subset_blocked
                    remaining = [row for row in move_plans if row[0] not in whca_subset_ids]
                    if remaining:
                        actions.extend(
                            self._resolve_with_collision_rules(
                                move_plans=remaining,
                                stationary=subset_reserved,
                                grid=grid,
                                state=state,
                                drop_off=drop_off,
                                item_positions=item_positions,
                                detour_preferred_bots=detour_preferred_bots,
                                effective_collision_aggressiveness=effective_collision_aggressiveness,
                                assignments_by_bot=assignments,
                                whca_fallback_bot_ids=set(),
                            )
                        )
            elif move_plans:
                actions.extend(
                    self._resolve_with_collision_rules(
                        move_plans=move_plans,
                        stationary=stationary,
                        grid=grid,
                        state=state,
                        drop_off=drop_off,
                        item_positions=item_positions,
                        detour_preferred_bots=detour_preferred_bots,
                        effective_collision_aggressiveness=effective_collision_aggressiveness,
                        assignments_by_bot=assignments,
                        whca_fallback_bot_ids=whca_fallback_bot_ids,
                    )
                )

        # Sort by bot id for clean output
        actions.sort(key=lambda a: a.bot)
        move_actions = {
            BotAction.MOVE_UP,
            BotAction.MOVE_DOWN,
            BotAction.MOVE_LEFT,
            BotAction.MOVE_RIGHT,
        }
        secondary_moved_bot_ids: set[int] = set()
        for action in actions:
            bid = int(action.bot)
            if bid in secondary_assigned_bot_ids and action.action in move_actions:
                secondary_moved_bot_ids.add(bid)
        for bot in state.bots:
            bid = int(bot.id)
            wait_reason = self._round_wait_reason_by_bot.get(bid, "")
            if wait_reason == "wait_due_to_no_assignment":
                self._wait_no_assignment_streak_by_bot[bid] = (
                    self._wait_no_assignment_streak_by_bot.get(bid, 0) + 1
                )
            else:
                self._wait_no_assignment_streak_by_bot[bid] = 0
        self._last_pick_attempt_by_bot = {
            a.bot: a.item_id
            for a in actions
            if a.action == BotAction.PICK_UP and a.item_id is not None
        }

        self.last_decision_ms = (time.perf_counter() - t0) * 1000
        self._blocked_moves_history.append(int(self.last_blocked_moves))
        self.last_round_telemetry = {
            "blocked_moves": float(self.last_blocked_moves),
            "swaps_prevented": float(self.last_swaps_prevented),
            "collisions_avoided": float(self.last_collisions_avoided),
            "dropoff_zone_density": float(dropoff_zone_density),
            "corridor_density": float(corridor_density),
            "stall_streak": float(self._stall_rounds),
            "assignment_churn": float(assignment_churn),
            "mean_dist_to_targets": float(mean_dist_to_targets),
            "escape_mode_active": float(1.0 if self.last_escape_mode_active else 0.0),
            "whca_used": float(1.0 if self.last_whca_used else 0.0),
            "whca_ms": float(self.last_whca_ms),
            "fallback_used": float(1.0 if self.last_fallback_used else 0.0),
            "transition_active": float(1.0 if self.last_transition_active else 0.0),
            "transition_hold_bots": float(len(self.last_transition_hold_bot_ids)),
            "secondary_assignment_used": float(1.0 if secondary_assigned_bot_ids else 0.0),
            "secondary_move_count": float(len(secondary_moved_bot_ids)),
            "bots_without_primary_assignment": float(
                max(0, len(state.bots) - len(primary_assigned_bot_ids))
            ),
            "primary_assignment_miss_streak_max": float(
                max(
                    (self._primary_assignment_miss_streak_by_bot.get(int(bot.id), 0) for bot in state.bots),
                    default=0,
                )
            ),
            "primary_assignment_miss_streak_mean": float(
                sum(self._primary_assignment_miss_streak_by_bot.get(int(bot.id), 0) for bot in state.bots)
                / float(max(1, len(state.bots)))
            ),
            "wait_due_to_no_assignment_streak_max": float(
                max(
                    (self._wait_no_assignment_streak_by_bot.get(int(bot.id), 0) for bot in state.bots),
                    default=0,
                )
            ),
            "wait_due_to_no_assignment_streak_mean": float(
                sum(self._wait_no_assignment_streak_by_bot.get(int(bot.id), 0) for bot in state.bots)
                / float(max(1, len(state.bots)))
            ),
            "secondary_assigned_bots": float(len(secondary_assigned_bot_ids)),
            "secondary_moved_bots": float(len(secondary_moved_bot_ids)),
            "no_assignment_wait_bots": float(
                len(
                    {
                        int(bot_id)
                        for bot_id, reason in self._round_wait_reason_by_bot.items()
                        if reason == "wait_due_to_no_assignment"
                    }
                )
            ),
            "secondary_assigned_mask": float(self._bot_id_mask(secondary_assigned_bot_ids)),
            "secondary_moved_mask": float(self._bot_id_mask(secondary_moved_bot_ids)),
            "no_assignment_wait_mask": float(
                self._bot_id_mask(
                    {
                        int(bot_id)
                        for bot_id, reason in self._round_wait_reason_by_bot.items()
                        if reason == "wait_due_to_no_assignment"
                    }
                )
            ),
            "primary_assigned_mask": float(self._bot_id_mask(primary_assigned_bot_ids)),
        }
        for key in WAIT_REASON_KEYS:
            self.last_round_telemetry[key] = float(self.last_wait_reason_counts.get(key, 0))
        if self.debug:
            action_str = ",".join(a.action.value for a in actions)
            print(f"  R{state.round:3d} score={state.score:3d} "
                  f"dt={self.last_decision_ms:.1f}ms "
                  f"actions=[{action_str}]")

        return RoundActions(actions=actions)

    @staticmethod
    def _bot_id_mask(bot_ids: set[int]) -> int:
        mask = 0
        for bot_id in bot_ids:
            bid = int(bot_id)
            if bid < 0:
                continue
            if bid >= 62:
                continue
            mask |= (1 << bid)
        return int(mask)

    def _elapsed_ms(self, t0: float) -> float:
        return (time.perf_counter() - t0) * 1000.0

    def _fallback_actions(self, state: GameState, t0: float) -> RoundActions:
        actions = [BotActionCommand(bot=bot.id, action=BotAction.WAIT) for bot in sorted(state.bots, key=lambda b: b.id)]
        self._blocked_moves_history.append(0)
        drop_off = (state.drop_off[0], state.drop_off[1])
        grid = Grid(state.grid)
        self.last_round_telemetry = {
            "blocked_moves": 0.0,
            "swaps_prevented": 0.0,
            "collisions_avoided": 0.0,
            "dropoff_zone_density": float(self._dropoff_zone_density(state, drop_off)),
            "corridor_density": float(self._corridor_density(state, grid)),
            "stall_streak": float(self._stall_rounds),
            "assignment_churn": 0.0,
            "mean_dist_to_targets": 0.0,
            "escape_mode_active": float(1.0 if self.last_escape_mode_active else 0.0),
            "whca_used": 0.0,
            "whca_ms": 0.0,
            "fallback_used": 1.0,
            "transition_active": float(1.0 if self.last_transition_active else 0.0),
            "transition_hold_bots": float(len(self.last_transition_hold_bot_ids)),
            "secondary_assignment_used": 0.0,
            "secondary_move_count": 0.0,
            "bots_without_primary_assignment": float(len(state.bots)),
            "primary_assignment_miss_streak_max": float(
                max(
                    (self._primary_assignment_miss_streak_by_bot.get(int(bot.id), 0) for bot in state.bots),
                    default=0,
                )
            ),
            "primary_assignment_miss_streak_mean": float(
                sum(self._primary_assignment_miss_streak_by_bot.get(int(bot.id), 0) for bot in state.bots)
                / float(max(1, len(state.bots)))
            ),
            "wait_due_to_no_assignment_streak_max": float(
                max(
                    (self._wait_no_assignment_streak_by_bot.get(int(bot.id), 0) for bot in state.bots),
                    default=0,
                )
            ),
            "wait_due_to_no_assignment_streak_mean": float(
                sum(self._wait_no_assignment_streak_by_bot.get(int(bot.id), 0) for bot in state.bots)
                / float(max(1, len(state.bots)))
            ),
            "secondary_assigned_bots": 0.0,
            "secondary_moved_bots": 0.0,
            "no_assignment_wait_bots": 0.0,
            "secondary_assigned_mask": 0.0,
            "secondary_moved_mask": 0.0,
            "no_assignment_wait_mask": 0.0,
            "primary_assigned_mask": 0.0,
        }
        for key in WAIT_REASON_KEYS:
            self.last_round_telemetry[key] = float(self.last_wait_reason_counts.get(key, 0))
        self.last_decision_ms = self._elapsed_ms(t0)
        return RoundActions(actions=actions)

    def _assignment_debug_payload(self, assign: Assignment | None) -> dict[str, object]:
        if assign is None:
            return {"target_type": "none"}
        payload: dict[str, object] = {
            "target_type": str(assign.target_type),
            "target_id": assign.target_id,
            "pickup_pos": list(assign.pickup_pos) if assign.pickup_pos is not None else None,
            "drop_off": list(assign.drop_off) if assign.drop_off is not None else None,
            "source": str(assign.source),
        }
        if assign.item is not None:
            payload["item_id"] = str(assign.item.id)
            payload["item_type"] = str(assign.item.type)
            payload["item_pos"] = [int(assign.item.pos.x), int(assign.item.pos.y)]
        return payload

    def _transition_excess_matching_hold_bot_ids(
        self,
        *,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
        drop_off: tuple[int, int],
        active_transition: bool,
    ) -> set[int]:
        if not active_transition or not self.config.transition_excess_matching_hold_enabled:
            return set()
        if self.last_escape_mode_active:
            return set()
        if self._blocked_moves_history:
            recent = self._blocked_moves_history[-1]
            avg_recent = sum(self._blocked_moves_history) / len(self._blocked_moves_history)
            if recent > 0 or avg_recent >= 1.0:
                return set()

        active = get_active_order(state)
        if active is None:
            return set()
        active_needed = Counter(str(item_type) for item_type in active.items_required)
        for item_type in active.items_delivered:
            if active_needed.get(item_type, 0) > 0:
                active_needed[item_type] -= 1
        if not active_needed:
            return set()

        blocked_set = set(item_blocked)
        dropoff_dist_map = bfs_distance_map(grid, drop_off, blocked=blocked_set)
        hold_ids: set[int] = set()
        max_dropoff_dist = max(1, int(self.config.transition_excess_matching_hold_max_dropoff_dist))
        max_pickup_steps = max(1, int(self.config.transition_excess_matching_hold_max_pickup_steps))

        for bot in sorted(state.bots, key=lambda cur: cur.id):
            if len(bot.inventory) != 1:
                continue
            matching = list(items_matching_active(bot, state))
            if len(matching) != 1:
                continue
            if dropoff_dist_map.get(bot.pos.as_tuple(), 999999) <= max_dropoff_dist:
                continue
            if not self._active_order_covered_without_bot(state=state, active_needed=active_needed, exclude_bot_id=bot.id):
                continue
            if not self._has_nearby_active_pickup(
                state=state,
                grid=grid,
                item_blocked=item_blocked,
                start=bot.pos.as_tuple(),
                active_needed=active_needed,
                max_pickup_steps=max_pickup_steps,
            ):
                continue
            hold_ids.add(int(bot.id))
        return hold_ids

    def _active_order_covered_without_bot(
        self,
        *,
        state: GameState,
        active_needed: Counter[str],
        exclude_bot_id: int,
    ) -> bool:
        remaining = Counter(active_needed)
        for bot in state.bots:
            if int(bot.id) == int(exclude_bot_id):
                continue
            for item_type in bot.inventory:
                if remaining.get(item_type, 0) > 0:
                    remaining[item_type] -= 1
        return all(count <= 0 for count in remaining.values())

    def _has_nearby_active_pickup(
        self,
        *,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
        start: tuple[int, int],
        active_needed: Counter[str],
        max_pickup_steps: int,
    ) -> bool:
        blocked = set(item_blocked)
        dist_map = bfs_distance_map(grid, start, blocked=blocked)
        for item in sorted(state.items, key=lambda cur: cur.id):
            if active_needed.get(item.type, 0) <= 0:
                continue
            for pickup_pos in find_all_pickup_positions(grid, item.pos.as_tuple()):
                dist = dist_map.get(pickup_pos, 999999)
                if dist <= max_pickup_steps:
                    return True
        return False

    def _set_wait_reason(self, bot_id: int, reason: str) -> None:
        if reason in WAIT_REASON_KEYS:
            self._wait_reason_by_bot[int(bot_id)] = reason

    def _record_wait(
        self,
        bot_id: int,
        *,
        assign: Assignment | None,
        explicit_reason: str | None = None,
    ) -> None:
        reason = explicit_reason or self._wait_reason_by_bot.pop(int(bot_id), "")
        if not reason and assign is not None and assign.target_type == "idle":
            if assign.source == "dropoff_stopline":
                reason = "wait_due_to_stopline"
            else:
                reason = "wait_due_to_no_assignment"
        if not reason and assign is None:
            reason = "wait_due_to_no_assignment"
        if reason in WAIT_REASON_KEYS:
            self._round_wait_reason_by_bot[int(bot_id)] = reason
            self.last_wait_reason_counts[reason] = self.last_wait_reason_counts.get(reason, 0) + 1

    def _store_pre_collision_action(
        self,
        *,
        bot: BotInfo,
        assign: Assignment | None,
        cmd: BotActionCommand,
        movement_target: tuple[int, int] | None,
    ) -> None:
        if not self.capture_debug:
            return
        payload: dict[str, object] = {
            "bot_id": int(bot.id),
            "start": [int(bot.pos.x), int(bot.pos.y)],
            "action": str(cmd.action.value),
            "item_id": cmd.item_id,
            "target_type": str(assign.target_type) if assign is not None else "none",
            "movement_target": list(movement_target) if movement_target is not None else None,
        }
        move_debug = self.last_move_debug.get(bot.id)
        if move_debug is not None:
            payload["move_debug"] = dict(move_debug)
        self.last_pre_collision_actions[int(bot.id)] = payload

    def _mark_move_debug(self, bot_id: int, **updates: object) -> None:
        if not self.capture_debug:
            return
        payload = dict(self.last_move_debug.get(int(bot_id), {}))
        payload.update(updates)
        self.last_move_debug[int(bot_id)] = payload

    def _startup_release_plan(
        self,
        *,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
        assignments: dict[int, Assignment],
        drop_off: tuple[int, int],
    ) -> tuple[dict[int, BotActionCommand], dict[int, tuple[int, int]]]:
        if not self.config.startup_release_enabled:
            return {}, {}
        if int(state.round) >= max(0, int(self.config.startup_release_rounds)):
            return {}, {}

        clustered = sum(1 for bot in state.bots if bot.pos.x >= 24 and bot.pos.y >= 15)
        stacked_counts = Counter(bot.pos.as_tuple() for bot in state.bots)
        if clustered < 2 and max(stacked_counts.values(), default=0) < 2:
            return {}, {}
        spawn = max(
            stacked_counts.items(),
            key=lambda row: (row[1], row[0][0] + row[0][1], row[0][0]),
        )[0]
        spawn_count = int(stacked_counts.get(spawn, 0))
        left1 = (spawn[0] - 1, spawn[1])
        up1 = (spawn[0], spawn[1] - 1)
        commands: dict[int, BotActionCommand] = {}
        goals: dict[int, tuple[int, int]] = {}
        spawn_bots = [bot for bot in sorted(state.bots, key=lambda cur: cur.id) if bot.pos.as_tuple() == spawn]
        if spawn_count <= 0:
            return {}, {}

        occupied_now = {bot.pos.as_tuple() for bot in state.bots if bot.pos.as_tuple() != spawn}
        available_exits: list[tuple[int, int]] = []
        if grid.is_walkable(left1[0], left1[1]) and left1 not in occupied_now:
            available_exits.append(left1)
        if grid.is_walkable(up1[0], up1[1]) and up1 not in occupied_now:
            available_exits.append(up1)
        if not available_exits:
            return {}, {}

        reserved: set[tuple[int, int]] = set()
        for bot in spawn_bots:
            if len(reserved) >= len(available_exits):
                break
            assign = assignments.get(bot.id)
            preferred_exit = left1
            if assign is not None:
                goal = drop_off if assign.target_type == "deliver" else assign.pickup_pos
                if goal is not None:
                    dx = abs(goal[0] - spawn[0])
                    dy = abs(goal[1] - spawn[1])
                    if dy > dx:
                        preferred_exit = up1
            for goal in (preferred_exit, up1 if preferred_exit == left1 else left1):
                if goal not in available_exits or goal in reserved:
                    continue
                reserved.add(goal)
                commands[int(bot.id)] = BotActionCommand(bot=bot.id, action=action_for_move(spawn, goal))
                goals[int(bot.id)] = goal
                if self.capture_debug:
                    self.last_move_debug[int(bot.id)] = {
                        "start": list(spawn),
                        "goal": list(goal),
                        "path_with_bots": [list(spawn), list(goal)],
                        "path_relaxed": [list(spawn), list(goal)],
                        "used_relaxed_fallback": False,
                        "used_simple_fallback": False,
                        "backtrack_alt": None,
                        "startup_release": True,
                        "one_way_blocked": False,
                        "whca_requested": False,
                        "whca_applied": False,
                        "whca_fallback": False,
                    }
                break
        return commands, goals

    def _startup_release_v3_fastpath(
        self,
        *,
        state: GameState,
        grid: Grid,
        drop_off: tuple[int, int],
        item_blocked: frozenset[tuple[int, int]],
        t0: float,
    ) -> RoundActions | None:
        if not self.config.startup_release_v3_enabled:
            return None
        if int(state.round) >= max(0, int(self.config.startup_release_rounds)):
            return None

        stacked_counts = Counter(bot.pos.as_tuple() for bot in state.bots)
        if not stacked_counts:
            return None
        spawn, spawn_count = max(stacked_counts.items(), key=lambda row: (row[1], row[0][0] + row[0][1], row[0][0]))
        if spawn_count != len(state.bots):
            self._startup_max_exited = max(self._startup_max_exited, len(state.bots) - spawn_count)
            self._startup_stuck_rounds = 0
            return None

        self._startup_stack_origin = spawn
        self._startup_stuck_rounds += 1
        blocked_recent = self._blocked_moves_history[-1] if self._blocked_moves_history else 0
        if blocked_recent <= 0 and self._startup_stuck_rounds < max(1, int(self.config.startup_release_stuck_rounds)):
            return None

        exits: list[tuple[int, int]] = []
        left1 = (spawn[0] - 1, spawn[1])
        up1 = (spawn[0], spawn[1] - 1)
        for cell in (left1, up1):
            if grid.is_walkable(cell[0], cell[1]) and cell not in item_blocked:
                exits.append(cell)
        if not exits:
            return None

        max_bots = max(1, min(len(exits), int(self.config.startup_release_max_bots_per_round)))
        selected: dict[int, tuple[int, int]] = {}
        remaining_exits = list(exits)
        for bot in sorted(state.bots, key=lambda cur: cur.id):
            if len(selected) >= max_bots or not remaining_exits:
                break
            ranked = sorted(
                (
                    self._startup_release_v3_exit_score(
                        bot=bot,
                        exit_cell=exit_cell,
                        state=state,
                        grid=grid,
                        drop_off=drop_off,
                        item_blocked=item_blocked,
                    ),
                    exit_cell,
                )
                for exit_cell in remaining_exits
            )
            chosen_exit = ranked[0][1]
            selected[int(bot.id)] = chosen_exit
            remaining_exits.remove(chosen_exit)

        actions: list[BotActionCommand] = []
        for bot in sorted(state.bots, key=lambda cur: cur.id):
            exit_cell = selected.get(int(bot.id))
            if exit_cell is None:
                actions.append(BotActionCommand(bot=bot.id, action=BotAction.WAIT))
                self._record_wait(bot.id, assign=None, explicit_reason="wait_due_to_collision_block")
                continue
            cmd = BotActionCommand(bot=bot.id, action=action_for_move(spawn, exit_cell))
            actions.append(cmd)
            if self.capture_debug:
                self.last_assignment_snapshot[int(bot.id)] = {
                    "target_type": "startup_release_v3",
                    "target_id": None,
                    "pickup_pos": list(exit_cell),
                    "drop_off": None,
                    "source": "startup_release_v3",
                }
                self.last_pre_collision_actions[int(bot.id)] = {
                    "bot_id": int(bot.id),
                    "start": [int(spawn[0]), int(spawn[1])],
                    "action": str(cmd.action.value),
                    "item_id": None,
                    "target_type": "startup_release_v3",
                    "movement_target": [int(exit_cell[0]), int(exit_cell[1])],
                }
                self.last_move_debug[int(bot.id)] = {
                    "start": [int(spawn[0]), int(spawn[1])],
                    "goal": [int(exit_cell[0]), int(exit_cell[1])],
                    "path_with_bots": [[int(spawn[0]), int(spawn[1])], [int(exit_cell[0]), int(exit_cell[1])]],
                    "path_relaxed": [[int(spawn[0]), int(spawn[1])], [int(exit_cell[0]), int(exit_cell[1])]],
                    "used_relaxed_fallback": False,
                    "used_simple_fallback": False,
                    "backtrack_alt": None,
                    "one_way_blocked": False,
                    "whca_requested": False,
                    "whca_applied": False,
                    "whca_fallback": False,
                    "startup_release_v3": True,
                }

        self.last_decision_ms = self._elapsed_ms(t0)
        self._blocked_moves_history.append(0)
        self.last_round_telemetry = {
            "blocked_moves": 0.0,
            "swaps_prevented": 0.0,
            "collisions_avoided": 0.0,
            "dropoff_zone_density": float(self._dropoff_zone_density(state, drop_off)),
            "corridor_density": float(self._corridor_density(state, grid)),
            "stall_streak": float(self._stall_rounds),
            "assignment_churn": 0.0,
            "mean_dist_to_targets": 0.0,
            "escape_mode_active": float(1.0 if self.last_escape_mode_active else 0.0),
            "whca_used": 0.0,
            "whca_ms": 0.0,
            "fallback_used": 0.0,
            "transition_active": float(1.0 if self.last_transition_active else 0.0),
            "transition_hold_bots": float(len(self.last_transition_hold_bot_ids)),
            "secondary_assignment_used": 0.0,
            "secondary_move_count": 0.0,
            "bots_without_primary_assignment": float(len(state.bots)),
            "primary_assignment_miss_streak_max": float(
                max(
                    (self._primary_assignment_miss_streak_by_bot.get(int(bot.id), 0) for bot in state.bots),
                    default=0,
                )
            ),
            "primary_assignment_miss_streak_mean": float(
                sum(self._primary_assignment_miss_streak_by_bot.get(int(bot.id), 0) for bot in state.bots)
                / float(max(1, len(state.bots)))
            ),
            "wait_due_to_no_assignment_streak_max": float(
                max(
                    (self._wait_no_assignment_streak_by_bot.get(int(bot.id), 0) for bot in state.bots),
                    default=0,
                )
            ),
            "wait_due_to_no_assignment_streak_mean": float(
                sum(self._wait_no_assignment_streak_by_bot.get(int(bot.id), 0) for bot in state.bots)
                / float(max(1, len(state.bots)))
            ),
            "secondary_assigned_bots": 0.0,
            "secondary_moved_bots": 0.0,
            "no_assignment_wait_bots": 0.0,
            "secondary_assigned_mask": 0.0,
            "secondary_moved_mask": 0.0,
            "no_assignment_wait_mask": 0.0,
            "primary_assigned_mask": 0.0,
        }
        for key in WAIT_REASON_KEYS:
            self.last_round_telemetry[key] = float(self.last_wait_reason_counts.get(key, 0))
        return RoundActions(actions=actions)

    def _startup_release_v3_exit_score(
        self,
        *,
        bot: BotInfo,
        exit_cell: tuple[int, int],
        state: GameState,
        grid: Grid,
        drop_off: tuple[int, int],
        item_blocked: frozenset[tuple[int, int]],
    ) -> tuple[object, ...]:
        if bot.inventory and items_matching_active(bot, state):
            return (bfs_distance(grid, exit_cell, drop_off, blocked=set(item_blocked)), bot.id)

        target_types = compute_needed_items(state)
        if not target_types:
            target_types = self._future_prefetch_fallback_types(state)
        best_dist = 999999
        for item in state.items:
            if item.type not in target_types:
                continue
            for pickup_pos in find_all_pickup_positions(grid, item.pos.as_tuple()):
                best_dist = min(best_dist, bfs_distance(grid, exit_cell, pickup_pos, blocked=set(item_blocked)))
        return (best_dist, bot.id, exit_cell[0], exit_cell[1])

    def _resolve_with_collision_rules(
        self,
        *,
        move_plans: list[tuple[int, tuple[int, int], tuple[int, int]]],
        stationary: set[tuple[int, int]],
        grid: Grid,
        state: GameState,
        drop_off: tuple[int, int],
        item_positions: frozenset[tuple[int, int]],
        detour_preferred_bots: set[int],
        effective_collision_aggressiveness: str,
        assignments_by_bot: dict[int, Assignment],
        whca_fallback_bot_ids: set[int],
    ) -> list[BotActionCommand]:
        if not move_plans:
            return []

        resolved, collision_stats = resolve_collisions_with_stats(
            move_plans,
            stationary,
            reservation_horizon=self.config.reservation_horizon,
        )
        self.last_collisions_avoided += collision_stats.blocked_moves
        self.last_blocked_moves += collision_stats.blocked_moves
        self.last_swaps_prevented += collision_stats.swaps_prevented

        emitted: list[BotActionCommand] = []
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
                if effective_collision_aggressiveness == "detour" or bot_id in detour_preferred_bots:
                    detour = self._detour_step(cur, desired, grid, final_reserved | item_positions)
                    if detour != cur:
                        final_reserved.add(detour)
                        emitted.append(
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
                        emitted.append(
                            BotActionCommand(
                                bot=bot_id,
                                action=action_for_move(cur, unclog),
                            )
                        )
                        continue
                emitted.append(BotActionCommand(bot=bot_id, action=BotAction.WAIT))
                explicit_reason = "wait_due_to_collision_block"
                if bot_id in whca_fallback_bot_ids:
                    explicit_reason = "wait_due_to_whca_no_plan_or_budget"
                self._record_wait(bot_id, assign=assignments_by_bot.get(bot_id), explicit_reason=explicit_reason)
            else:
                final_reserved.add(actual)
                emitted.append(BotActionCommand(bot=bot_id, action=action_for_move(cur, actual)))
        return emitted

    def _apply_regime_overrides(
        self,
        *,
        policy: AssignmentPolicy,
        dropoff_zone_density: float,
        corridor_density: float,
    ) -> AssignmentPolicy:
        out = replace(
            policy,
            dropoff_stop_line_enabled=bool(self.config.dropoff_stop_line_enabled),
            dropoff_stop_line_k=max(1, int(self.config.dropoff_stop_line_k)),
            dropoff_stop_line_radius=max(1, int(self.config.dropoff_stop_line_radius)),
            dropoff_stop_line_trigger_density=max(0.0, float(self.config.dropoff_stop_line_trigger_density)),
        )

        if not self.config.congestion_auction_enabled:
            return out

        blocked_trigger = max(1, int(self.config.congestion_auction_blocked_trigger))
        blocked_recent = self._blocked_moves_history[-1] if self._blocked_moves_history else 0
        congested = (
            dropoff_zone_density >= float(self.config.congestion_auction_dropoff_trigger)
            or corridor_density >= float(self.config.congestion_auction_corridor_trigger)
            or blocked_recent >= blocked_trigger
        )
        if not congested:
            return replace(
                out,
                predicted_dropoff_density_weight=0.0,
                predicted_corridor_density_weight=0.0,
            )

        return replace(
            out,
            assignment_strategy="auction",
            auction_allow_skip=bool(self.config.auction_allow_skip),
            auction_option_depth=max(1, int(self.config.congestion_auction_option_depth)),
            predicted_dropoff_density_weight=max(0.0, float(self.config.congestion_auction_dropoff_penalty)),
            predicted_corridor_density_weight=max(0.0, float(self.config.congestion_auction_corridor_penalty)),
        )

    def _should_apply_one_way_aisle(self, *, corridor_density: float) -> bool:
        if not self.config.one_way_aisle_enabled:
            return False
        if corridor_density < float(self.config.one_way_aisle_trigger_density):
            return False
        blocked_trigger = max(1, int(self.config.one_way_aisle_blocked_trigger))
        if not self._blocked_moves_history:
            return False
        recent = self._blocked_moves_history[-1]
        avg_recent = sum(self._blocked_moves_history) / len(self._blocked_moves_history)
        return recent >= blocked_trigger or avg_recent >= float(blocked_trigger)

    @staticmethod
    def _violates_one_way_aisle(
        *,
        start: tuple[int, int],
        target: tuple[int, int],
        grid: Grid,
    ) -> bool:
        dx = target[0] - start[0]
        dy = target[1] - start[1]
        if abs(dx) + abs(dy) != 1:
            return False
        if len(grid.neighbors(start[0], start[1])) > 2 and len(grid.neighbors(target[0], target[1])) > 2:
            return False
        if dx != 0:
            allowed_dx = 1 if (start[1] % 2 == 0) else -1
            return dx != allowed_dx
        allowed_dy = 1 if (start[0] % 2 == 0) else -1
        return dy != allowed_dy

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
        distance_map_cache: dict[tuple[int, int], dict[tuple[int, int], int]] | None = None,
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
                    dist_map = self._distance_map_for_start(
                        start=bpos,
                        grid=grid,
                        item_blocked=item_blocked,
                        cache=distance_map_cache,
                    )
                    best_dist = 999999
                    best_pp = None
                    for item in sorted(state.items, key=lambda it: it.id):
                        if item.type in needed:
                            pps = find_all_pickup_positions(grid, item.pos.as_tuple())
                            for pp in pps:
                                d = dist_map.get(pp, 999999)
                                if d < best_dist:
                                    best_dist = d
                                    best_pp = pp
                    if best_pp:
                        return self._move_toward(bot.id, bpos, best_pp, grid, state, item_blocked=item_blocked)
                self._set_wait_reason(bot.id, "wait_due_to_vacate_dropoff_failed")
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

        if assign.target_type == "secondary_reposition" and assign.pickup_pos is not None:
            if assign.pickup_pos == bpos:
                return BotActionCommand(bot=bot.id, action=BotAction.WAIT)
            return self._move_toward(bot.id, bpos, assign.pickup_pos, grid, state, item_blocked=item_blocked)

        if bot.id in self.last_transition_hold_bot_ids:
            hold_pickup = self._transition_hold_pickup_pos(
                bot=bot,
                state=state,
                grid=grid,
                item_blocked=item_blocked,
            )
            if hold_pickup is not None:
                return self._move_toward(bot.id, bpos, hold_pickup, grid, state, item_blocked=item_blocked)

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
                dist_map = self._distance_map_for_start(
                    start=bpos,
                    grid=grid,
                    item_blocked=item_blocked,
                    cache=distance_map_cache,
                )
                best_dist = 999999
                best_pp = None
                for item in sorted(state.items, key=lambda it: it.id):
                    if item.type in target_types:
                        pps = find_all_pickup_positions(grid, item.pos.as_tuple())
                        for pp in pps:
                            d = dist_map.get(pp, 999999)
                            if d < best_dist:
                                best_dist = d
                                best_pp = pp
                if best_pp:
                    return self._move_toward(bot.id, bpos, best_pp, grid, state, item_blocked=item_blocked)
            if (
                self.config.idle_stage_when_no_visible_targets
                and not target_types
                and not bot.inventory
            ):
                stage_target = self._idle_stage_target(
                    bot=bot,
                    state=state,
                    grid=grid,
                    item_blocked=item_blocked,
                )
                if stage_target is not None and stage_target != bpos:
                    return self._move_toward(bot.id, bpos, stage_target, grid, state, item_blocked=item_blocked)
        if bpos == drop_off:
            vacate_cmd = self._vacate_dropoff(bot.id, bpos, grid, state, item_blocked=item_blocked)
            if vacate_cmd is not None:
                return vacate_cmd
            self._set_wait_reason(bot.id, "wait_due_to_vacate_dropoff_failed")
        return BotActionCommand(bot=bot.id, action=BotAction.WAIT)

    @staticmethod
    def _distance_map_for_start(
        *,
        start: tuple[int, int],
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
        cache: dict[tuple[int, int], dict[tuple[int, int], int]] | None,
    ) -> dict[tuple[int, int], int]:
        if cache is None:
            return bfs_distance_map(grid, start, blocked=set(item_blocked))
        dist_map = cache.get(start)
        if dist_map is None:
            dist_map = bfs_distance_map(grid, start, blocked=set(item_blocked))
            cache[start] = dist_map
        return dist_map

    @staticmethod
    def _nearest_stage_cell(
        *,
        desired: tuple[int, int],
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
        drop_off: tuple[int, int],
    ) -> tuple[int, int] | None:
        for radius in range(0, 4):
            candidates: list[tuple[int, int]] = []
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if abs(dx) + abs(dy) != radius:
                        continue
                    cell = (desired[0] + dx, desired[1] + dy)
                    if not grid.is_walkable(cell[0], cell[1]):
                        continue
                    if cell in item_blocked:
                        continue
                    if abs(cell[0] - drop_off[0]) + abs(cell[1] - drop_off[1]) <= 2:
                        continue
                    candidates.append(cell)
            if candidates:
                candidates.sort(key=lambda pos: (abs(pos[1] - desired[1]), abs(pos[0] - desired[0]), pos[0], pos[1]))
                return candidates[0]
        return None

    def _idle_stage_target(
        self,
        *,
        bot: BotInfo,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        pickup_cache = {
            item.id: find_all_pickup_positions(grid, item.pos.as_tuple())
            for item in state.items
        }
        pickup_columns = _pickup_zone_columns(pickup_cache)
        bot_ids = [cur.id for cur in sorted(state.bots, key=lambda row: row.id)]
        bot_index = bot_ids.index(bot.id) if bot.id in bot_ids else 0
        zone_x = _zone_center_x(
            bot_index=bot_index,
            bot_count=max(1, len(bot_ids)),
            pickup_columns=pickup_columns,
            grid_width=grid.width,
        )
        stage_row = max(1, int(state.grid.height) - 3 - (bot_index % 2))
        drop_off = (state.drop_off[0], state.drop_off[1])
        desired = (zone_x, stage_row)
        return self._nearest_stage_cell(
            desired=desired,
            grid=grid,
            item_blocked=item_blocked,
            drop_off=drop_off,
        )

    def _active_needs_covered_by_team_inventory(self, state: GameState) -> bool:
        needed = Counter(compute_needed_items(state))
        if not needed:
            return True
        carried = Counter()
        for cur_bot in state.bots:
            for item_type in items_matching_active(cur_bot, state):
                carried[item_type] += 1
        return all(carried.get(item_type, 0) >= count for item_type, count in needed.items())

    def _transition_hold_pickup_pos(
        self,
        *,
        bot: BotInfo,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        active = get_active_order(state)
        if active is None:
            return None
        needed = Counter(str(item_type) for item_type in active.items_required)
        for item_type in active.items_delivered:
            if needed.get(item_type, 0) > 0:
                needed[item_type] -= 1
        if not any(count > 0 for count in needed.values()):
            return None

        start = bot.pos.as_tuple()
        best_rank: tuple[object, ...] | None = None
        best_pickup: tuple[int, int] | None = None
        for item in sorted(state.items, key=lambda cur: cur.id):
            if needed.get(item.type, 0) <= 0:
                continue
            for pickup_pos in find_all_pickup_positions(grid, item.pos.as_tuple()):
                dist = bfs_distance(grid, start, pickup_pos, blocked=set(item_blocked))
                if dist >= 999999:
                    continue
                rank = (
                    dist,
                    0 if pickup_pos[0] == start[0] else 1,
                    abs(pickup_pos[1] - start[1]),
                    abs(pickup_pos[0] - start[0]),
                    pickup_pos[1],
                    pickup_pos[0],
                    str(item.id),
                )
                if best_rank is None or rank < best_rank:
                    best_rank = rank
                    best_pickup = pickup_pos
        return best_pickup

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
            if self.capture_debug:
                self.last_move_debug[int(bot_id)] = {
                    "start": list(start),
                    "goal": list(goal),
                    "path_with_bots": [list(start)],
                    "path_relaxed": [list(goal)],
                    "used_relaxed_fallback": False,
                    "used_simple_fallback": False,
                    "backtrack_alt": None,
                    "one_way_blocked": False,
                    "whca_requested": False,
                    "whca_applied": False,
                    "whca_fallback": False,
                }
            return BotActionCommand(bot=bot_id, action=BotAction.WAIT)

        # Build blocked set from other bot positions + item positions
        blocked: set[tuple[int, int]] = set(item_blocked)
        for b in state.bots:
            if b.id != bot_id:
                blocked.add(b.pos.as_tuple())
        # Don't block the goal itself
        blocked.discard(goal)

        pathfn = astar_path if self.use_astar else bfs_shortest_path
        path_with_bots = pathfn(grid, start, goal, blocked)
        path_relaxed = path_with_bots
        path = path_with_bots
        used_relaxed_fallback = False

        if path is None or len(path) < 2:
            # Try without other-bot blocking (they might move), but keep items blocked
            path_relaxed = pathfn(grid, start, goal, set(item_blocked) - {goal})
            path = path_relaxed
            if path is None or len(path) < 2:
                # Fallback: simple manhattan move
                if self.capture_debug:
                    self.last_move_debug[int(bot_id)] = {
                        "start": list(start),
                        "goal": list(goal),
                        "path_with_bots": [list(pos) for pos in path_with_bots] if path_with_bots else None,
                        "path_relaxed": [list(pos) for pos in path_relaxed] if path_relaxed else None,
                        "used_relaxed_fallback": False,
                        "used_simple_fallback": True,
                        "backtrack_alt": None,
                        "one_way_blocked": False,
                        "whca_requested": False,
                        "whca_applied": False,
                        "whca_fallback": False,
                    }
                return self._simple_move(bot_id, start, goal, grid, item_blocked=item_blocked)
            used_relaxed_fallback = True
        elif self.capture_debug:
            path_relaxed = pathfn(grid, start, goal, set(item_blocked) - {goal})

        next_cell = path[1]
        backtrack_alt: tuple[int, int] | None = None
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
                        backtrack_alt = alt
        if self.capture_debug:
            self.last_move_debug[int(bot_id)] = {
                "start": list(start),
                "goal": list(goal),
                "path_with_bots": [list(pos) for pos in path_with_bots] if path_with_bots else None,
                "path_relaxed": [list(pos) for pos in path_relaxed] if path_relaxed else None,
                "used_relaxed_fallback": bool(used_relaxed_fallback),
                "used_simple_fallback": False,
                "backtrack_alt": list(backtrack_alt) if backtrack_alt is not None else None,
                "one_way_blocked": False,
                "whca_requested": False,
                "whca_applied": False,
                "whca_fallback": False,
            }
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

    def _maybe_explore_command(
        self,
        *,
        bot: BotInfo,
        cmd: BotActionCommand,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
    ) -> BotActionCommand:
        eps = max(0.0, min(1.0, float(self.config.exploration_epsilon)))
        if eps <= 0.0:
            return cmd
        if cmd.action in (BotAction.PICK_UP, BotAction.DROP_OFF):
            return cmd
        roll = self._exploration_float(
            state=state,
            bot_id=bot.id,
            salt="roll",
        )
        if roll >= eps:
            return cmd

        candidates: list[BotActionCommand] = [BotActionCommand(bot=bot.id, action=BotAction.WAIT)]
        occupied = {other.pos.as_tuple() for other in state.bots if other.id != bot.id}
        x, y = bot.pos.as_tuple()
        for action, (dx, dy) in (
            (BotAction.MOVE_UP, (0, -1)),
            (BotAction.MOVE_RIGHT, (1, 0)),
            (BotAction.MOVE_DOWN, (0, 1)),
            (BotAction.MOVE_LEFT, (-1, 0)),
        ):
            target = (x + dx, y + dy)
            if not grid.is_walkable(target[0], target[1]):
                continue
            if target in item_blocked:
                continue
            if target in occupied:
                continue
            candidates.append(BotActionCommand(bot=bot.id, action=action))

        alternatives = [cand for cand in candidates if cand.action != cmd.action]
        if not alternatives:
            return cmd
        pick = self._exploration_index(
            state=state,
            bot_id=bot.id,
            salt="pick",
            size=len(alternatives),
        )
        return alternatives[pick]

    def _exploration_float(
        self,
        *,
        state: GameState,
        bot_id: int,
        salt: str,
    ) -> float:
        seed = int(self.config.tie_break_seed)
        key = (
            f"{seed}:{state.round}:{state.active_order_index}:{bot_id}:{salt}"
            .encode("ascii", errors="ignore")
        )
        digest = hashlib.sha256(key).digest()
        raw = int.from_bytes(digest[:8], "big")
        return raw / float(1 << 64)

    def _exploration_index(
        self,
        *,
        state: GameState,
        bot_id: int,
        salt: str,
        size: int,
    ) -> int:
        if size <= 1:
            return 0
        val = self._exploration_float(state=state, bot_id=bot_id, salt=salt)
        idx = int(val * size)
        return max(0, min(size - 1, idx))

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

    def _should_use_whca(
        self,
        *,
        state: GameState,
        drop_off: tuple[int, int],
        total_moves: int,
    ) -> bool:
        if not self.config.whca_enabled:
            return False
        if total_moves <= 1:
            return False
        if self.last_escape_mode_active:
            return True
        if self._blocked_moves_history:
            avg_blocked = sum(self._blocked_moves_history) / len(self._blocked_moves_history)
            if avg_blocked >= float(max(1, int(self.config.whca_blocked_moves_trigger))):
                return True
        recent_blocked = self._blocked_moves_history[-1] if self._blocked_moves_history else 0
        radius = max(1, int(self.config.whca_congestion_radius))
        trigger_bots = max(2, int(self.config.whca_congestion_bots_trigger))
        near_drop = sum(
            1
            for bot in state.bots
            if abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1]) <= radius
        )
        return near_drop >= trigger_bots and recent_blocked > 0

    @staticmethod
    def _dropoff_zone_density(state: GameState, drop_off: tuple[int, int]) -> float:
        if not state.bots:
            return 0.0
        in_zone = sum(
            1
            for bot in state.bots
            if abs(bot.pos.x - drop_off[0]) + abs(bot.pos.y - drop_off[1]) <= 2
        )
        return float(in_zone) / float(len(state.bots))

    @staticmethod
    def _corridor_density(state: GameState, grid: Grid) -> float:
        if not state.bots:
            return 0.0
        corridor_bots = 0
        for bot in state.bots:
            deg = len(grid.neighbors(bot.pos.x, bot.pos.y))
            if deg <= 2:
                corridor_bots += 1
        return float(corridor_bots) / float(len(state.bots))

    @staticmethod
    def _mean_dist_to_targets(
        assignments: dict[int, Assignment],
        state: GameState,
        drop_off: tuple[int, int],
    ) -> float:
        if not state.bots:
            return 0.0
        dists: list[float] = []
        for bot in state.bots:
            assign = assignments.get(bot.id)
            bpos = bot.pos.as_tuple()
            if assign is None:
                dists.append(0.0)
                continue
            if assign.target_type == "deliver":
                dists.append(float(abs(bpos[0] - drop_off[0]) + abs(bpos[1] - drop_off[1])))
            elif assign.target_type in {"pick_item", "pre_pick", "secondary_reposition"} and assign.pickup_pos is not None:
                pp = assign.pickup_pos
                dists.append(float(abs(bpos[0] - pp[0]) + abs(bpos[1] - pp[1])))
            else:
                dists.append(0.0)
        return float(sum(dists) / len(dists))

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

        in_recovery = state.round < self._stall_recovery_until_round
        stall_delivery_breaker_rounds = max(1, int(self.config.stall_delivery_breaker_rounds))
        delivery_breaker_active = bool(
            self.config.stall_delivery_breaker_enabled
            and (self._stall_rounds >= stall_delivery_breaker_rounds or in_recovery)
        )
        effective_always_deliver_matching = bool(
            self.config.always_deliver_matching or delivery_breaker_active
        )
        self.last_escape_mode_active = bool(in_recovery and self.config.escape_mode_enabled)
        if in_recovery:
            base_seed = int(self.config.tie_break_seed)
            offset = int(self.config.escape_tie_break_seed_offset) if self.config.escape_mode_enabled else 0
            escape_seed = base_seed + offset + int(state.active_order_index or 0)
            return replace(
                self.assignment_policy,
                preview_weight=max(0.0, float(self.config.stall_recovery_preview_weight)),
                force_dropoff_for_full_nonmatching=bool(self.config.stall_recovery_force_dropoff),
                strict_active_priority=bool(self.config.stall_recovery_strict_active),
                always_deliver_matching=effective_always_deliver_matching,
                clear_adjacent_dropoff_lane=True,
                clear_lane_distance=max(
                    2,
                    int(
                        self.config.escape_clear_lane_distance
                        if self.config.escape_mode_enabled
                        else self.config.clear_lane_distance
                    ),
                ),
                tie_break_dynamic=bool(self.config.escape_mode_enabled),
                tie_break_seed=escape_seed,
            )
        self.last_escape_mode_active = False
        if delivery_breaker_active and not bool(self.assignment_policy.always_deliver_matching):
            return replace(
                self.assignment_policy,
                always_deliver_matching=True,
            )
        return self.assignment_policy
