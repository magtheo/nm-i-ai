from __future__ import annotations

from bot.assignment import AssignmentPolicy, _zone_center_x, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state(
    *,
    bots: list[BotInfo],
    items: list[ItemInfo],
    active_items: list[str],
    preview_items: list[str],
    drop_off: tuple[int, int] = (0, 0),
    round_num: int = 0,
    max_rounds: int = 300,
) -> GameState:
    orders = [
        OrderInfo(
            id="order_0",
            items_required=active_items,
            items_delivered=[],
            complete=False,
            status=OrderStatus.ACTIVE,
        ),
        OrderInfo(
            id="order_1",
            items_required=preview_items,
            items_delivered=[],
            complete=False,
            status=OrderStatus.PREVIEW,
        ),
    ]
    return GameState(
        round=round_num,
        max_rounds=max_rounds,
        grid=GridInfo(width=9, height=7, walls=[]),
        bots=bots,
        items=items,
        orders=orders,
        drop_off=[drop_off[0], drop_off[1]],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_zone_centers_scale_with_bot_count_and_grid_width() -> None:
    pickup_columns = [1, 4, 8, 12, 16, 20, 24, 26]
    centers = [
        _zone_center_x(
            bot_index=bot_index,
            bot_count=10,
            pickup_columns=pickup_columns,
            grid_width=28,
        )
        for bot_index in range(10)
    ]

    assert centers == sorted(centers)
    assert centers[0] >= min(pickup_columns)
    assert centers[-1] <= max(pickup_columns)
    assert centers[-1] < 28


def test_prefetch_guard_avoids_filling_nonmatching_inventory() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=["banana", "donut"]),
            BotInfo(id=1, position=[7, 1], inventory=[]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_active", type="apple", position=[5, 1]),
            ItemInfo(id="item_preview", type="banana", position=[2, 1]),
        ],
        active_items=["apple", "carrot", "egg"],
        preview_items=["banana", "banana"],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        lookahead_orders=2,
        active_weight=1.0,
        preview_weight=20.0,
        prefetch_spare_slots=0,
        prefetch_nonmatching_cap=2,
        assignment_strategy="greedy",
    )

    assignments = assign_bots(state, grid, policy=policy, active_order_index=0, order_forecast={})
    assert assignments[0].target_type == "pick_item"
    assert assignments[0].item is not None
    assert assignments[0].item.type == "apple"


def test_overflow_prefetch_uses_idle_bots_after_active_is_fully_assigned() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[1, 5], inventory=[]),
            BotInfo(id=2, position=[7, 1], inventory=[]),
            BotInfo(id=3, position=[7, 5], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_active_a", type="apple", position=[3, 1]),
            ItemInfo(id="item_active_b", type="pear", position=[3, 5]),
            ItemInfo(id="item_preview_a", type="banana", position=[5, 1]),
            ItemInfo(id="item_preview_b", type="carrot", position=[5, 5]),
        ],
        active_items=["apple", "pear"],
        preview_items=["banana", "carrot"],
    )
    grid = Grid(state.grid)

    blocked_policy = AssignmentPolicy(
        lookahead_orders=2,
        active_weight=20.0,
        preview_weight=20.0,
        prefetch_min_completion=0.5,
        overflow_prefetch_when_active_assigned=False,
        assignment_strategy="greedy",
    )
    blocked = assign_bots(state, grid, policy=blocked_policy, active_order_index=0, order_forecast={})
    assert blocked[0].target_type == "pick_item"
    assert blocked[1].target_type == "pick_item"
    assert blocked[2].target_type == "idle"
    assert blocked[3].target_type == "idle"

    overflow_policy = AssignmentPolicy(
        lookahead_orders=2,
        active_weight=20.0,
        preview_weight=20.0,
        prefetch_min_completion=0.5,
        overflow_prefetch_when_active_assigned=True,
        assignment_strategy="greedy",
    )
    overflow = assign_bots(state, grid, policy=overflow_policy, active_order_index=0, order_forecast={})
    assert overflow[0].target_type == "pick_item"
    assert overflow[1].target_type == "pick_item"
    assert overflow[2].target_type == "pre_pick"
    assert overflow[3].target_type == "pre_pick"


def test_overflow_prefetch_round_limit_disables_late_overflow() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[1, 5], inventory=[]),
            BotInfo(id=2, position=[7, 1], inventory=[]),
            BotInfo(id=3, position=[7, 5], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_active_a", type="apple", position=[3, 1]),
            ItemInfo(id="item_active_b", type="pear", position=[3, 5]),
            ItemInfo(id="item_preview_a", type="banana", position=[5, 1]),
            ItemInfo(id="item_preview_b", type="carrot", position=[5, 5]),
        ],
        active_items=["apple", "pear"],
        preview_items=["banana", "carrot"],
    ).model_copy(update={"round": 50})
    grid = Grid(state.grid)

    policy = AssignmentPolicy(
        lookahead_orders=2,
        active_weight=20.0,
        preview_weight=20.0,
        prefetch_min_completion=0.5,
        overflow_prefetch_when_active_assigned=True,
        overflow_prefetch_round_limit=10,
        assignment_strategy="greedy",
    )

    assignments = assign_bots(state, grid, policy=policy, active_order_index=0, order_forecast={})
    assert assignments[0].target_type == "pick_item"
    assert assignments[1].target_type == "pick_item"
    assert assignments[2].target_type == "idle"
    assert assignments[3].target_type == "idle"


def test_transition_stash_prefers_preview_for_non_finisher_when_active_is_covered() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=["apple"]),
            BotInfo(id=1, position=[7, 5], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_preview", type="banana", position=[7, 3]),
        ],
        active_items=["apple"],
        preview_items=["banana"],
        drop_off=(0, 0),
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        lookahead_orders=2,
        active_weight=6.0,
        preview_weight=1.0,
        transition_stash_enabled=True,
        transition_stash_remaining_items=1,
        transition_stash_finisher_count=1,
        transition_stash_preview_bonus=20.0,
        assignment_strategy="greedy",
    )

    assignments = assign_bots(state, grid, policy=policy)

    assert assignments[0].target_type == "deliver"
    assert assignments[1].target_type == "pre_pick"
    assert assignments[1].item is not None
    assert assignments[1].item.id == "item_preview"


def test_full_nonmatching_inventory_is_staged_at_dropoff() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[4, 4], inventory=["banana", "donut", "egg"]),
            BotInfo(id=1, position=[7, 1], inventory=[]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple", "apple"],
        preview_items=["banana"],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(force_dropoff_for_full_nonmatching=True)

    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].target_type == "deliver"
    assert assignments[0].drop_off == (0, 0)


def test_full_nonmatching_inventory_can_be_left_idle_when_forcing_disabled() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[4, 4], inventory=["banana", "donut", "egg"]),
            BotInfo(id=1, position=[7, 1], inventory=[]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple", "apple"],
        preview_items=["banana"],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(force_dropoff_for_full_nonmatching=False)

    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].target_type == "idle"


def test_inventory_lock_forces_unload_even_when_forcing_disabled() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[4, 4], inventory=["banana", "donut", "egg"]),
            BotInfo(id=1, position=[6, 4], inventory=["banana", "donut", "egg"]),
            BotInfo(id=2, position=[7, 4], inventory=["banana", "donut", "egg"]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(force_dropoff_for_full_nonmatching=False)

    assignments = assign_bots(state, grid, policy=policy)
    assert any(assign.target_type == "deliver" for assign in assignments.values())


def test_congestion_weight_can_shift_choice_to_less_crowded_pickup() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[2, 2], inventory=["x", "y", "z"]),
            BotInfo(id=2, position=[3, 2], inventory=["x", "y", "z"]),
        ],
        items=[
            ItemInfo(id="item_near", type="apple", position=[3, 1]),
            ItemInfo(id="item_far", type="apple", position=[6, 1]),
        ],
        active_items=["apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    near_policy = AssignmentPolicy(
        active_weight=20.0,
        urgency_weight=1.0,
        dist_weight=1.0,
        dropoff_dist_weight=0.0,
        congestion_weight=0.0,
        assignment_strategy="greedy",
    )
    near_assignments = assign_bots(state, grid, policy=near_policy)
    assert near_assignments[0].item is not None
    assert near_assignments[0].item.id == "item_near"

    crowded_policy = AssignmentPolicy(
        active_weight=20.0,
        urgency_weight=1.0,
        dist_weight=1.0,
        dropoff_dist_weight=0.0,
        congestion_weight=10.0,
        assignment_strategy="greedy",
    )
    crowded_assignments = assign_bots(state, grid, policy=crowded_policy)
    assert crowded_assignments[0].item is not None
    assert crowded_assignments[0].item.id == "item_far"


def test_carry_home_bias_shifts_pick_to_bot_without_matching_cargo() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[6, 1], inventory=["apple"]),
            BotInfo(id=1, position=[1, 1], inventory=[]),
        ],
        items=[ItemInfo(id="item_banana", type="banana", position=[7, 1])],
        active_items=["apple", "banana"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    no_bias = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=20.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            carry_home_bias_weight=0.0,
            assignment_strategy="greedy",
        ),
    )
    assert no_bias[0].item is not None
    assert no_bias[0].item.id == "item_banana"

    with_bias = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=20.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            carry_home_bias_weight=5.0,
            assignment_strategy="greedy",
        ),
    )
    assert with_bias[1].item is not None
    assert with_bias[1].item.id == "item_banana"


def test_duplicate_active_need_can_assign_same_item_id_when_no_alternative_shelf() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[1, 3], inventory=[]),
        ],
        items=[ItemInfo(id="item_only", type="apple", position=[2, 2])],
        active_items=["apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(assignment_strategy="greedy")

    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].item is not None
    assert assignments[1].item is not None
    assert assignments[0].item.id == "item_only"
    assert assignments[1].item.id == "item_only"


def test_allow_same_shelf_for_same_type_prefers_closer_duplicate_shelf() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[1, 3], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_near", type="apple", position=[2, 2]),
            ItemInfo(id="item_far", type="apple", position=[7, 2]),
        ],
        active_items=["apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    spread = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=20.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
            allow_same_shelf_for_same_type=False,
        ),
    )
    assert spread[0].item is not None
    assert spread[1].item is not None
    assert {spread[0].item.id, spread[1].item.id} == {"item_near", "item_far"}

    duplicate_ok = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=20.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
            allow_same_shelf_for_same_type=True,
        ),
    )
    assert duplicate_ok[0].item is not None
    assert duplicate_ok[1].item is not None
    assert duplicate_ok[0].item.id == "item_near"
    assert duplicate_ok[1].item.id == "item_near"


def test_conditional_same_shelf_for_active_duplicates_prefers_near_when_gap_is_large() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[1, 3], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_near", type="apple", position=[2, 2]),
            ItemInfo(id="item_far", type="apple", position=[8, 2]),
        ],
        active_items=["apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=20.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
            allow_same_shelf_for_same_type=False,
            allow_same_shelf_for_active_duplicates=True,
            active_duplicate_same_shelf_min_gap=2,
        ),
    )

    assert assignments[0].item is not None
    assert assignments[1].item is not None
    assert assignments[0].item.id == "item_near"
    assert assignments[1].item.id == "item_near"


def test_endgame_disables_prefetch_candidates() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[7, 1], inventory=[]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_active", type="apple", position=[5, 1]),
            ItemInfo(id="item_preview", type="banana", position=[2, 1]),
        ],
        active_items=["apple", "carrot", "egg"],
        preview_items=["banana", "banana"],
    ).model_copy(update={"round": 280, "max_rounds": 300})
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        lookahead_orders=3,
        active_weight=1.0,
        preview_weight=50.0,
        prefetch_min_completion=0.0,
        prefetch_spare_slots=0,
        endgame_disable_prefetch_rounds=30,
        assignment_strategy="greedy",
    )

    assignments = assign_bots(state, grid, policy=policy, active_order_index=0, order_forecast={})
    assert assignments[0].target_type == "pick_item"
    assert assignments[0].item is not None
    assert assignments[0].item.type == "apple"


def test_endgame_force_deliver_overrides_completion_threshold() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[4, 4], inventory=["apple"]),
            BotInfo(id=1, position=[7, 1], inventory=[]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple", "apple"],
        preview_items=[],
    ).model_copy(update={"round": 285, "max_rounds": 300})
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        dropoff_completion_threshold=1.0,
        endgame_force_deliver_rounds=20,
    )

    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].target_type == "deliver"


def test_matching_items_deliver_immediately_when_always_deliver_enabled() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[4, 4], inventory=["apple"]),
            BotInfo(id=1, position=[7, 1], inventory=[]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        dropoff_completion_threshold=1.0,
        always_deliver_matching=True,
    )
    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].target_type == "deliver"


def test_nonmatching_full_bot_does_not_block_dropoff_when_others_have_matching() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[4, 4], inventory=["banana", "banana", "banana"]),
            BotInfo(id=1, position=[3, 4], inventory=["apple"]),
            BotInfo(id=2, position=[7, 5], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        force_dropoff_for_full_nonmatching=True,
        avoid_dropoff_block_when_matching=True,
        always_deliver_matching=True,
    )
    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[1].target_type == "deliver"
    assert assignments[0].target_type != "deliver"


def test_max_concurrent_deliverers_limits_delivery_queue() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 0], inventory=["apple"]),
            BotInfo(id=1, position=[8, 6], inventory=["apple"]),
            BotInfo(id=2, position=[6, 3], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[5, 1])],
        active_items=["apple", "apple", "apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        always_deliver_matching=True,
        max_concurrent_deliverers=1,
    )

    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].target_type == "deliver"
    assert assignments[1].target_type != "deliver"


def test_sticky_target_bonus_prefers_previous_item_target() -> None:
    state = _state(
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[
            ItemInfo(id="item_far", type="apple", position=[6, 1]),
            ItemInfo(id="item_near", type="apple", position=[3, 1]),
        ],
        active_items=["apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    plain = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(active_weight=20.0, dist_weight=1.0, sticky_target_bonus=0.0),
        sticky_targets={0: "item_far"},
    )
    assert plain[0].item is not None
    assert plain[0].item.id == "item_near"

    sticky = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(active_weight=20.0, dist_weight=1.0, sticky_target_bonus=20.0),
        sticky_targets={0: "item_far"},
    )
    assert sticky[0].item is not None
    assert sticky[0].item.id == "item_far"


def test_two_step_trip_bonus_prefers_pickup_with_better_followup_chain() -> None:
    state = _state(
        bots=[BotInfo(id=0, position=[1, 3], inventory=[])],
        items=[
            ItemInfo(id="item_bad", type="apple", position=[2, 1]),
            ItemInfo(id="item_good", type="apple", position=[4, 3]),
            ItemInfo(id="item_next", type="banana", position=[5, 3]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
        drop_off=(0, 3),
    )
    grid = Grid(state.grid)

    plain = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=10.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.35,
            two_step_trip_weight=0.0,
            assignment_strategy="greedy",
        ),
    )
    assert plain[0].item is not None
    assert plain[0].item.id == "item_bad"

    chained = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=10.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.35,
            two_step_trip_weight=1.0,
            two_step_trip_min_gain=1,
            assignment_strategy="greedy",
        ),
    )
    assert chained[0].item is not None
    assert chained[0].item.id == "item_good"


def test_legacy_trip_chain_weight_restores_short_chain_preference() -> None:
    state = _state(
        bots=[BotInfo(id=0, position=[1, 3], inventory=[])],
        items=[
            ItemInfo(id="item_bad", type="apple", position=[2, 1]),
            ItemInfo(id="item_good", type="apple", position=[4, 3]),
            ItemInfo(id="item_next", type="banana", position=[5, 3]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
        drop_off=(0, 3),
    )
    grid = Grid(state.grid)

    no_chain = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=10.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.35,
            trip_chain_bonus_weight=0.0,
            two_step_trip_weight=0.0,
            assignment_strategy="greedy",
        ),
    )
    with_legacy_chain = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=10.0,
            urgency_weight=1.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.35,
            trip_chain_bonus_weight=2.0,
            two_step_trip_weight=0.0,
            assignment_strategy="greedy",
        ),
    )
    assert no_chain[0].item is not None
    assert with_legacy_chain[0].item is not None
    assert no_chain[0].item.id == "item_bad"
    assert with_legacy_chain[0].item.id == "item_good"


def test_auction_option_depth_controls_candidate_search() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[1, 2], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_shared", type="apple", position=[3, 1]),
            ItemInfo(id="item_alt", type="banana", position=[7, 5]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    depth_one = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            assignment_strategy="auction",
            auction_option_depth=1,
            active_weight=20.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            two_step_trip_weight=0.0,
        ),
    )
    picked_depth_one = sum(1 for a in depth_one.values() if a.target_type in {"pick_item", "pre_pick"})
    assert picked_depth_one <= 1

    depth_wide = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            assignment_strategy="auction",
            auction_option_depth=8,
            active_weight=20.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            two_step_trip_weight=0.0,
        ),
    )
    picked_depth_wide = sum(1 for a in depth_wide.values() if a.target_type in {"pick_item", "pre_pick"})
    assert picked_depth_wide == 2


def test_future_count_weight_prefers_higher_future_demand_type() -> None:
    state = _state(
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[
            ItemInfo(id="item_carrot", type="carrot", position=[3, 1]),
            ItemInfo(id="item_banana", type="banana", position=[5, 1]),
        ],
        active_items=[],
        preview_items=[],
    )
    grid = Grid(state.grid)
    forecast = {
        1: ["banana", "carrot"],
        2: ["banana", "banana"],
    }

    plain = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            lookahead_orders=3,
            preview_weight=5.0,
            future_depth_decay=0.0,
            future_count_weight=0.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
        ),
        order_forecast=forecast,
        active_order_index=0,
    )
    assert plain[0].item is not None
    assert plain[0].item.id == "item_carrot"

    weighted = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            lookahead_orders=3,
            preview_weight=5.0,
            future_depth_decay=0.0,
            future_count_weight=2.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
        ),
        order_forecast=forecast,
        active_order_index=0,
    )
    assert weighted[0].item is not None
    assert weighted[0].item.id == "item_banana"


def test_strict_active_release_completion_allows_prefetch_after_threshold() -> None:
    state = _state(
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[
            ItemInfo(id="item_active", type="apple", position=[4, 1]),
            ItemInfo(id="item_preview", type="banana", position=[2, 1]),
        ],
        active_items=["apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    forecast = {1: ["banana"]}

    strict_locked = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            lookahead_orders=2,
            strict_active_priority=True,
            strict_active_release_completion=1.0,
            active_weight=4.0,
            preview_weight=20.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
        ),
        order_forecast=forecast,
        active_order_index=0,
    )
    assert strict_locked[0].item is not None
    assert strict_locked[0].item.id == "item_active"

    strict_released = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            lookahead_orders=2,
            strict_active_priority=True,
            strict_active_release_completion=0.0,
            active_weight=4.0,
            preview_weight=20.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
        ),
        order_forecast=forecast,
        active_order_index=0,
    )
    assert strict_released[0].item is not None
    assert strict_released[0].item.id == "item_preview"


def test_strict_active_allows_prefetch_when_active_is_already_covered_by_carry() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=["apple", "banana"]),
            BotInfo(id=1, position=[1, 3], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_active", type="apple", position=[4, 1]),
            ItemInfo(id="item_preview", type="carrot", position=[2, 3]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    forecast = {1: ["carrot"]}

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            lookahead_orders=2,
            strict_active_priority=True,
            strict_active_release_completion=1.0,
            active_weight=4.0,
            preview_weight=20.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
        ),
        order_forecast=forecast,
        active_order_index=0,
    )
    assert assignments[0].target_type == "deliver"
    assert assignments[1].item is not None
    assert assignments[1].item.id == "item_preview"


def test_strict_active_delivered_gate_blocks_prefetch_when_only_carry_covered() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=["apple", "banana"]),
            BotInfo(id=1, position=[1, 3], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_active", type="apple", position=[4, 1]),
            ItemInfo(id="item_preview", type="carrot", position=[2, 3]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    forecast = {1: ["carrot"]}

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            lookahead_orders=2,
            strict_active_priority=True,
            strict_active_release_completion=1.0,
            prefetch_release_use_delivered_completion=True,
            active_weight=4.0,
            preview_weight=20.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.0,
            assignment_strategy="greedy",
        ),
        order_forecast=forecast,
        active_order_index=0,
    )

    assert assignments[0].target_type == "deliver"
    assert assignments[1].target_type != "pre_pick"

def test_adaptive_deliver_queue_limits_early_delivery_pressure() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 0], inventory=["apple"]),
            BotInfo(id=1, position=[8, 6], inventory=["apple"]),
            BotInfo(id=2, position=[6, 3], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_banana", type="banana", position=[5, 1]),
            ItemInfo(id="item_carrot", type="carrot", position=[5, 2]),
            ItemInfo(id="item_egg", type="egg", position=[5, 3]),
            ItemInfo(id="item_milk", type="milk", position=[5, 4]),
        ],
        active_items=["apple", "apple", "banana", "carrot", "egg", "milk"],
        preview_items=[],
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        always_deliver_matching=True,
        max_concurrent_deliverers=3,
        adaptive_deliver_queue=True,
        deliver_queue_min=1,
        deliver_queue_max=3,
    )

    assignments = assign_bots(state, grid, policy=policy)
    assert assignments[0].target_type == "deliver"
    assert assignments[1].target_type != "deliver"


def test_dropoff_stop_line_caps_nearby_deliverers_with_priority() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[0, 0], inventory=["apple"]),
            BotInfo(id=1, position=[1, 0], inventory=["apple"]),
            BotInfo(id=2, position=[0, 1], inventory=["apple"]),
        ],
        items=[],
        active_items=["apple", "apple", "apple"],
        preview_items=[],
        drop_off=(0, 0),
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        always_deliver_matching=True,
        max_concurrent_deliverers=3,
        dropoff_stop_line_enabled=True,
        dropoff_stop_line_k=1,
        dropoff_stop_line_radius=2,
        dropoff_stop_line_trigger_density=0.0,
    )

    assignments = assign_bots(state, grid, policy=policy)
    deliver_ids = [bot_id for bot_id, assign in assignments.items() if assign.target_type == "deliver"]
    assert deliver_ids == [0]


def test_two_step_completion_delay_threshold_blocks_overdelayed_batching() -> None:
    state = _state(
        bots=[BotInfo(id=0, position=[1, 3], inventory=[])],
        items=[
            ItemInfo(id="item_control", type="apple", position=[1, 1]),
            ItemInfo(id="item_chain", type="apple", position=[1, 2]),
            ItemInfo(id="item_chain_far", type="apple", position=[1, 5]),
        ],
        active_items=["apple"],
        preview_items=[],
        drop_off=(0, 3),
    )
    grid = Grid(state.grid)

    loose = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=10.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.35,
            two_step_trip_weight=1.0,
            two_step_trip_min_gain=1,
            two_step_completion_delay_threshold=3,
            assignment_strategy="greedy",
        ),
    )
    assert loose[0].item is not None
    assert loose[0].item.id == "item_chain"

    strict = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            active_weight=10.0,
            dist_weight=1.0,
            dropoff_dist_weight=0.35,
            two_step_trip_weight=1.0,
            two_step_trip_min_gain=1,
            two_step_completion_delay_threshold=0,
            assignment_strategy="greedy",
        ),
    )
    assert strict[0].item is not None
    assert strict[0].item.id == "item_control"


def test_secondary_active_support_replaces_idle_when_unassigned_bot_exists() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[7, 5], inventory=["milk", "bread", "eggs"]),
        ],
        items=[
            ItemInfo(id="item_active_apple", type="apple", position=[3, 1]),
            ItemInfo(id="item_active_banana", type="banana", position=[6, 5]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
        round_num=278,
    )
    grid = Grid(state.grid)

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            anti_no_assignment_enabled=True,
            secondary_assignment_enabled=True,
            secondary_max_distance=10,
            secondary_reposition_empty_only=False,
            assignment_strategy="greedy",
        ),
        primary_assignment_miss_streak_by_bot={1: 4},
    )

    assert assignments[0].target_type == "pick_item"
    assert assignments[1].target_type == "secondary_reposition"
    assert assignments[1].source == "secondary_active_support"


def test_secondary_duplicate_support_activates_for_repeated_active_type() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[7, 5], inventory=["milk", "bread", "eggs"]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[3, 1])],
        active_items=["apple", "apple"],
        preview_items=[],
        round_num=278,
    )
    grid = Grid(state.grid)

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            anti_no_assignment_enabled=True,
            secondary_assignment_enabled=True,
            secondary_duplicate_support=True,
            secondary_max_distance=10,
            secondary_reposition_empty_only=False,
            assignment_strategy="greedy",
        ),
        primary_assignment_miss_streak_by_bot={1: 4},
    )

    assert assignments[0].target_type == "pick_item"
    assert assignments[1].target_type == "secondary_reposition"
    assert assignments[1].source == "secondary_duplicate_support"


def test_disabling_anti_no_assignment_restores_idle_fallback() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[7, 5], inventory=[]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[3, 1])],
        active_items=["apple"],
        preview_items=[],
    )
    grid = Grid(state.grid)

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            anti_no_assignment_enabled=False,
            secondary_assignment_enabled=True,
            assignment_strategy="greedy",
        ),
    )

    assert assignments[0].target_type == "pick_item"
    assert assignments[1].target_type == "idle"
    assert assignments[1].source == "idle_fallback"


def test_secondary_recovery_stays_idle_before_late_window_even_when_starved() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[7, 5], inventory=["milk", "bread", "eggs"]),
        ],
        items=[
            ItemInfo(id="item_active_apple", type="apple", position=[3, 1]),
            ItemInfo(id="item_active_banana", type="banana", position=[6, 5]),
        ],
        active_items=["apple", "banana"],
        preview_items=[],
        round_num=250,
    )
    grid = Grid(state.grid)

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            anti_no_assignment_enabled=True,
            secondary_assignment_enabled=True,
            secondary_max_distance=10,
            secondary_reposition_empty_only=False,
            assignment_strategy="greedy",
        ),
        primary_assignment_miss_streak_by_bot={1: 10},
    )

    assert assignments[0].target_type == "pick_item"
    assert assignments[1].target_type == "idle"
    assert assignments[1].source == "idle_fallback"


def test_secondary_starvation_support_is_deterministic_for_same_state() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[7, 5], inventory=["milk", "bread", "eggs"]),
        ],
        items=[ItemInfo(id="item_active", type="apple", position=[3, 1])],
        active_items=["apple", "apple"],
        preview_items=[],
        round_num=278,
    )
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        anti_no_assignment_enabled=True,
        secondary_assignment_enabled=True,
        secondary_duplicate_support=False,
        anti_starvation_enabled=True,
        anti_starvation_rounds=2,
        anti_starvation_bonus=2.0,
        secondary_max_distance=10,
        secondary_reposition_empty_only=False,
        assignment_strategy="greedy",
    )
    streak = {1: 5}

    a1 = assign_bots(
        state,
        grid,
        policy=policy,
        primary_assignment_miss_streak_by_bot=streak,
    )
    a2 = assign_bots(
        state,
        grid,
        policy=policy,
        primary_assignment_miss_streak_by_bot=streak,
    )

    assert a1[1].target_type == "secondary_reposition"
    assert a1[1].source == "secondary_starvation_support"
    assert a1[1].pickup_pos == a2[1].pickup_pos
