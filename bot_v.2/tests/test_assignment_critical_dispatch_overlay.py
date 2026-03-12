from __future__ import annotations

from bot.assignment import AssignmentPolicy, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state_dispatch() -> GameState:
    return GameState(
        round=70,
        max_rounds=300,
        grid=GridInfo(width=12, height=10, walls=[]),
        bots=[BotInfo(id=i, position=[1 + i, 8], inventory=[]) for i in range(6)],
        items=[
            ItemInfo(id="a1", type="apples", position=[9, 2]),
            ItemInfo(id="a2", type="apples", position=[9, 3]),
            ItemInfo(id="m1", type="milk", position=[7, 2]),
            ItemInfo(id="m2", type="milk", position=[7, 3]),
            ItemInfo(id="b1", type="bread", position=[5, 2]),
            ItemInfo(id="p1", type="pasta", position=[3, 2]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples", "milk", "bread", "pasta"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["eggs", "eggs", "cheese"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_tail_with_cargo() -> GameState:
    return GameState(
        round=120,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=[
            BotInfo(id=0, position=[2, 8], inventory=["apples"]),
            BotInfo(id=1, position=[3, 8], inventory=["apples"]),
            BotInfo(id=2, position=[8, 2], inventory=[]),
            BotInfo(id=3, position=[8, 3], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_preview_gate(*, active_required: list[str], active_delivered: list[str]) -> GameState:
    return GameState(
        round=50,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=[BotInfo(id=i, position=[2 + i, 8], inventory=[]) for i in range(5)],
        items=[
            ItemInfo(id="a1", type="apples", position=[8, 2]),
            ItemInfo(id="a2", type="apples", position=[8, 3]),
            ItemInfo(id="p1", type="bread", position=[3, 2]),
            ItemInfo(id="p2", type="bread", position=[3, 3]),
            ItemInfo(id="p3", type="bread", position=[3, 4]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=list(active_required),
                items_delivered=list(active_delivered),
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["bread", "bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_converter_payload_priority() -> GameState:
    return GameState(
        round=140,
        max_rounds=300,
        grid=GridInfo(width=12, height=10, walls=[]),
        bots=[
            BotInfo(id=0, position=[2, 8], inventory=["apples"]),
            BotInfo(id=1, position=[6, 8], inventory=["apples", "milk"]),
            BotInfo(id=2, position=[8, 2], inventory=[]),
        ],
        items=[ItemInfo(id="b1", type="bread", position=[9, 2])],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples", "milk", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["eggs"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_reliable_commit_preview_gate() -> GameState:
    return GameState(
        round=90,
        max_rounds=300,
        grid=GridInfo(width=12, height=10, walls=[]),
        bots=[
            BotInfo(id=0, position=[10, 1], inventory=["apples", "milk"]),
            BotInfo(id=1, position=[3, 8], inventory=[]),
            BotInfo(id=2, position=[4, 8], inventory=[]),
        ],
        items=[
            ItemInfo(id="p1", type="bread", position=[3, 2]),
            ItemInfo(id="p2", type="bread", position=[3, 3]),
            ItemInfo(id="p3", type="bread", position=[3, 4]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples", "milk"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["bread", "bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_close_dispatch_payload() -> GameState:
    return GameState(
        round=100,
        max_rounds=300,
        grid=GridInfo(width=12, height=10, walls=[]),
        bots=[BotInfo(id=i, position=[2 + i, 8], inventory=[]) for i in range(4)],
        items=[
            ItemInfo(id="a1", type="apples", position=[8, 2]),
            ItemInfo(id="a2", type="apples", position=[8, 3]),
            ItemInfo(id="b1", type="bread", position=[3, 2]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_overlay_limits_critical_dispatch_slots() -> None:
    state = _state_dispatch()
    grid = Grid(state.grid)
    known_supply = {
        "apples": {(9, 2), (9, 3)},
        "milk": {(7, 2), (7, 3)},
        "bread": {(5, 2)},
        "pasta": {(3, 2)},
    }
    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_max_slots=2,
        ),
        known_supply_by_type=known_supply,
    )

    dispatch_count = sum(1 for assign in assignments.values() if "critical_dispatch" in str(assign.source))
    assert dispatch_count == 1


def test_overlay_dynamic_converter_floor_reaches_two_in_tail() -> None:
    state = _state_tail_with_cargo()
    grid = Grid(state.grid)
    known_supply = {"bread": {(5, 2), (5, 3)}, "apples": {(8, 2)}, "milk": {(8, 3)}}

    baseline = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            max_concurrent_deliverers=1,
            demand_commitment_mode="committed",
        ),
        known_supply_by_type=known_supply,
    )
    overlay = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            max_concurrent_deliverers=1,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_tail_remaining_threshold=2,
        ),
        known_supply_by_type=known_supply,
    )

    baseline_deliver = sum(1 for assign in baseline.values() if assign.target_type == "deliver")
    overlay_deliver = sum(1 for assign in overlay.values() if assign.target_type == "deliver")

    assert baseline_deliver <= 1
    assert overlay_deliver >= 2


def test_overlay_preview_suppression_is_narrow_to_critical_window() -> None:
    grid_info_state = _state_preview_gate(
        active_required=["apples", "apples", "apples", "apples"],
        active_delivered=[],
    )
    tail_state = _state_preview_gate(
        active_required=["apples", "apples"],
        active_delivered=["apples"],
    )
    grid_open = Grid(grid_info_state.grid)
    grid_tail = Grid(tail_state.grid)
    known_supply = {"apples": {(8, 2), (8, 3)}, "bread": {(3, 2), (3, 3), (3, 4)}}

    open_assignments = assign_bots(
        grid_info_state,
        grid_open,
        policy=AssignmentPolicy(
            strict_active_priority=False,
            active_weight=1.0,
            preview_weight=15.0,
            lookahead_orders=2,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_preview_block_when_unsecured=True,
        ),
        known_supply_by_type=known_supply,
    )
    tail_assignments = assign_bots(
        tail_state,
        grid_tail,
        policy=AssignmentPolicy(
            strict_active_priority=False,
            active_weight=1.0,
            preview_weight=15.0,
            lookahead_orders=2,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_preview_block_when_unsecured=True,
        ),
        known_supply_by_type=known_supply,
    )

    open_pre = sum(1 for assign in open_assignments.values() if assign.target_type == "pre_pick")
    tail_pre = sum(1 for assign in tail_assignments.values() if assign.target_type == "pre_pick")

    assert open_pre >= 1
    assert tail_pre == 0


def test_overlay_converter_selection_prefers_higher_payload_when_capped() -> None:
    state = _state_converter_payload_priority()
    grid = Grid(state.grid)
    known_supply = {"bread": {(9, 2)}, "apples": {(6, 2)}, "milk": {(7, 2)}}

    baseline = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            always_deliver_matching=True,
            max_concurrent_deliverers=1,
            demand_commitment_mode="committed",
        ),
        known_supply_by_type=known_supply,
    )
    overlay = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            always_deliver_matching=True,
            max_concurrent_deliverers=1,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
        ),
        known_supply_by_type=known_supply,
    )

    baseline_deliver_ids = {bot_id for bot_id, assign in baseline.items() if assign.target_type == "deliver"}
    overlay_deliver_ids = {bot_id for bot_id, assign in overlay.items() if assign.target_type == "deliver"}

    assert baseline_deliver_ids == {0}
    assert overlay_deliver_ids == {1}


def test_overlay_reliable_commit_guard_blocks_preview_when_far_commit_is_unreliable() -> None:
    state = _state_reliable_commit_preview_gate()
    grid = Grid(state.grid)
    known_supply = {"bread": {(3, 2), (3, 3), (3, 4)}}

    loose = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=False,
            active_weight=1.0,
            preview_weight=15.0,
            lookahead_orders=2,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_preview_block_when_unsecured=True,
            critical_dispatch_reliable_max_dropoff_dist=20,
            critical_dispatch_reliable_min_matching_ratio=0.0,
        ),
        known_supply_by_type=known_supply,
    )
    strict = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=False,
            active_weight=1.0,
            preview_weight=15.0,
            lookahead_orders=2,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_preview_block_when_unsecured=True,
            critical_dispatch_reliable_max_dropoff_dist=3,
            critical_dispatch_reliable_min_matching_ratio=0.67,
        ),
        known_supply_by_type=known_supply,
    )

    loose_pre = sum(1 for assign in loose.values() if assign.target_type == "pre_pick")
    strict_pre = sum(1 for assign in strict.values() if assign.target_type == "pre_pick")

    assert loose_pre >= 1
    assert strict_pre == 0


def test_overlay_dispatch_marks_payload_priority_source() -> None:
    state = _state_close_dispatch_payload()
    grid = Grid(state.grid)
    known_supply = {"apples": {(8, 2), (8, 3)}, "bread": {(3, 2)}}

    assignments = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            demand_commitment_mode="committed",
            critical_dispatch_overlay_enabled=True,
            critical_dispatch_max_slots=1,
        ),
        known_supply_by_type=known_supply,
    )

    payload_dispatch = [
        assign
        for assign in assignments.values()
        if assign.target_type == "pick_item" and "critical_dispatch_payload" in str(assign.source)
    ]
    assert payload_dispatch
