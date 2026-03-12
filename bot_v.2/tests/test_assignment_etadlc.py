from __future__ import annotations

from bot.assignment import AssignmentPolicy, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state_converter_floor() -> GameState:
    return GameState(
        round=60,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=[
            BotInfo(id=0, position=[2, 8], inventory=["apples"]),
            BotInfo(id=1, position=[3, 8], inventory=["milk"]),
            BotInfo(id=2, position=[8, 2], inventory=[]),
            BotInfo(id=3, position=[8, 3], inventory=[]),
        ],
        items=[
            ItemInfo(id="i1", type="bread", position=[7, 2]),
            ItemInfo(id="i2", type="bread", position=[7, 3]),
        ],
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
                items_required=["eggs", "eggs"],
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


def _state_preview_gate() -> GameState:
    return GameState(
        round=40,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=[BotInfo(id=i, position=[2 + i, 8], inventory=[]) for i in range(4)],
        items=[
            ItemInfo(id="a1", type="apples", position=[7, 2]),
            ItemInfo(id="a2", type="apples", position=[7, 3]),
            ItemInfo(id="p1", type="bread", position=[3, 2]),
            ItemInfo(id="p2", type="bread", position=[3, 3]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples", "apples"],
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


def test_etadlc_enforces_converter_floor() -> None:
    state = _state_converter_floor()
    grid = Grid(state.grid)
    known_supply = {
        "bread": {(7, 2), (7, 3)},
        "apples": {(7, 2)},
        "milk": {(7, 3)},
    }

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
    etadlc = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            max_concurrent_deliverers=1,
            demand_commitment_mode="committed",
            etadlc_enabled=True,
            etadlc_converter_floor_min=2,
            etadlc_converter_floor_tail=3,
        ),
        known_supply_by_type=known_supply,
    )

    base_deliver = sum(1 for assign in baseline.values() if assign.target_type == "deliver")
    etadlc_deliver = sum(1 for assign in etadlc.values() if assign.target_type == "deliver")

    assert base_deliver <= 1
    assert etadlc_deliver >= 2
    assert any("etadlc_floor" in str(assign.source) for assign in etadlc.values())


def test_etadlc_preview_gates_until_active_secured() -> None:
    state = _state_preview_gate()
    grid = Grid(state.grid)
    known_supply = {
        "apples": {(7, 2), (7, 3)},
        "bread": {(3, 2), (3, 3)},
    }

    baseline = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=False,
            preview_weight=15.0,
            lookahead_orders=2,
            demand_commitment_mode="committed",
        ),
        known_supply_by_type=known_supply,
    )
    etadlc = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=False,
            preview_weight=15.0,
            lookahead_orders=2,
            demand_commitment_mode="committed",
            etadlc_enabled=True,
        ),
        known_supply_by_type=known_supply,
    )

    baseline_pre = sum(1 for assign in baseline.values() if assign.target_type == "pre_pick")
    etadlc_pre = sum(1 for assign in etadlc.values() if assign.target_type == "pre_pick")

    assert baseline_pre >= 1
    assert etadlc_pre == 0


def test_etadlc_marks_eta_targeted_retrieval_sources() -> None:
    state = _state_preview_gate()
    grid = Grid(state.grid)
    known_supply = {
        "apples": {(7, 2), (7, 3)},
        "bread": {(3, 2), (3, 3)},
    }

    etadlc = assign_bots(
        state,
        grid,
        policy=AssignmentPolicy(
            strict_active_priority=True,
            demand_commitment_mode="committed",
            etadlc_enabled=True,
            etadlc_known_shelf_target_bonus=2.0,
        ),
        known_supply_by_type=known_supply,
    )

    assert any("etadlc_eta" in str(assign.source) for assign in etadlc.values())
