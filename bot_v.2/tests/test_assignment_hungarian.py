from __future__ import annotations

from bot.assignment import AssignmentPolicy, _hungarian_min_cost, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state_two_bot_two_pick() -> GameState:
    return GameState(
        round=40,
        max_rounds=300,
        grid=GridInfo(width=12, height=12, walls=[]),
        bots=[
            BotInfo(id=0, position=[1, 2], inventory=[]),
            BotInfo(id=1, position=[1, 8], inventory=[]),
        ],
        items=[
            ItemInfo(id="i0", type="apples", position=[4, 2]),
            ItemInfo(id="i1", type="apples", position=[4, 8]),
            ItemInfo(id="i2", type="bread", position=[8, 5]),
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
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 10],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_active_vs_preview_tradeoff() -> GameState:
    return GameState(
        round=30,
        max_rounds=300,
        grid=GridInfo(width=14, height=12, walls=[]),
        bots=[
            BotInfo(id=0, position=[1, 2], inventory=[]),
            BotInfo(id=1, position=[1, 8], inventory=[]),
        ],
        items=[
            ItemInfo(id="a0", type="apples", position=[10, 2]),
            ItemInfo(id="a1", type="apples", position=[10, 8]),
            ItemInfo(id="b0", type="bread", position=[2, 2]),
            ItemInfo(id="b1", type="bread", position=[2, 8]),
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
        drop_off=[1, 10],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_hungarian_solver_known_matrix() -> None:
    cost = [
        [4.0, 1.0, 3.0],
        [2.0, 0.0, 5.0],
        [3.0, 2.0, 2.0],
    ]
    assignment = _hungarian_min_cost(cost)
    assert assignment == [1, 0, 2]


def test_assignment_strategy_hungarian_produces_global_plan() -> None:
    state = _state_two_bot_two_pick()
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        assignment_strategy="hungarian",
        auction_option_depth=6,
        strict_active_priority=True,
    )
    assignments = assign_bots(state, grid, policy=policy)

    pick_like = [
        assign
        for assign in assignments.values()
        if assign.target_type in {"pick_item", "pre_pick"} and assign.target_id
    ]
    assert pick_like
    assert all("hungarian_plan" in str(assign.source) for assign in pick_like)
    assert len({assign.target_id for assign in pick_like}) == len(pick_like)


def test_hungarian_active_only_prefers_active_when_needed() -> None:
    state = _state_active_vs_preview_tradeoff()
    grid = Grid(state.grid)
    policy = AssignmentPolicy(
        assignment_strategy="hungarian",
        active_weight=1.0,
        preview_weight=12.0,
        strict_active_priority=False,
        hungarian_active_only_when_needed=True,
    )
    assignments = assign_bots(state, grid, policy=policy)

    picks = [assign for assign in assignments.values() if assign.target_type in {"pick_item", "pre_pick"}]
    assert picks
    assert all(assign.target_type == "pick_item" for assign in picks)
    assert all(assign.item is not None and assign.item.type == "apples" for assign in picks)
    assert all("active_only" in str(assign.source) for assign in picks)
