from __future__ import annotations

from bot.assignment import AssignmentPolicy, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state(
    *,
    bots: list[BotInfo],
    items: list[ItemInfo],
    active_items: list[str],
    preview_items: list[str],
    drop_off: tuple[int, int] = (0, 0),
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
        round=0,
        max_rounds=300,
        grid=GridInfo(width=9, height=7, walls=[]),
        bots=bots,
        items=items,
        orders=orders,
        drop_off=[drop_off[0], drop_off[1]],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


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


def test_trip_chain_bonus_prefers_pickup_with_better_followup_chain() -> None:
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
            trip_chain_bonus_weight=0.0,
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
            trip_chain_bonus_weight=2.0,
            assignment_strategy="greedy",
        ),
    )
    assert chained[0].item is not None
    assert chained[0].item.id == "item_good"


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
