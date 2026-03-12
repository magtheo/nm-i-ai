from __future__ import annotations

from bot.assignment import AssignmentPolicy, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state_active_vs_preview() -> GameState:
    bots = [BotInfo(id=i, position=[1, i + 1], inventory=[]) for i in range(6)]
    return GameState(
        round=80,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=bots,
        items=[
            ItemInfo(id="a1", type="apples", position=[8, 1]),
            ItemInfo(id="a2", type="apples", position=[8, 2]),
            ItemInfo(id="a3", type="apples", position=[8, 3]),
            ItemInfo(id="a4", type="apples", position=[8, 4]),
            ItemInfo(id="p1", type="bread", position=[2, 1]),
            ItemInfo(id="p2", type="bread", position=[2, 2]),
            ItemInfo(id="p3", type="bread", position=[2, 3]),
            ItemInfo(id="p4", type="bread", position=[2, 4]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples", "apples", "apples", "apples"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["bread", "bread", "bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 9],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_active_complete() -> GameState:
    bots = [BotInfo(id=i, position=[1, i + 1], inventory=[]) for i in range(4)]
    return GameState(
        round=120,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=bots,
        items=[
            ItemInfo(id="p1", type="bread", position=[2, 1]),
            ItemInfo(id="p2", type="bread", position=[2, 2]),
            ItemInfo(id="p3", type="bread", position=[2, 3]),
            ItemInfo(id="p4", type="bread", position=[2, 4]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["apples"],
                items_delivered=["apples"],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="o1",
                items_required=["bread", "bread", "bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 9],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _base_policy(**extra: object) -> AssignmentPolicy:
    payload = {
        "strict_active_priority": False,
        "active_weight": 10.0,
        "preview_weight": 25.0,
        "prefetch_release_use_delivered_completion": True,
        "transition_stash_enabled": True,
        "transition_stash_remaining_items": 2,
        "transition_stash_completion_ratio": 0.9,
        "transition_stash_finisher_count": 1,
        "anti_no_assignment_enabled": False,
        "secondary_assignment_enabled": False,
        "demand_commitment_mode": "committed",
    }
    payload.update(extra)
    return AssignmentPolicy(**payload)


def test_task_pool_admission_forces_completion_critical_assignments() -> None:
    state = _state_active_vs_preview()
    grid = Grid(state.grid)

    baseline = assign_bots(state, grid, policy=_base_policy())
    candidate = assign_bots(
        state,
        grid,
        policy=_base_policy(
            task_pool_admission_enabled=True,
            task_pool_critical_min_bots=3,
            task_pool_critical_max_bots=3,
            task_pool_tail_boost_bots=0,
            task_pool_preview_reserve_bots=1,
        ),
    )

    baseline_active = sum(1 for assign in baseline.values() if assign.target_type == "pick_item")
    candidate_active = sum(1 for assign in candidate.values() if assign.target_type == "pick_item")

    assert baseline_active < candidate_active
    assert candidate_active >= 3
    assert any("critical_pool" in str(assign.source) for assign in candidate.values())


def test_task_pool_admission_is_inactive_when_active_order_closed() -> None:
    state = _state_active_complete()
    grid = Grid(state.grid)

    candidate = assign_bots(
        state,
        grid,
        policy=_base_policy(
            task_pool_admission_enabled=True,
            task_pool_critical_min_bots=3,
            task_pool_critical_max_bots=3,
            task_pool_tail_boost_bots=0,
            task_pool_preview_reserve_bots=1,
        ),
    )

    assert any(assign.target_type == "pre_pick" for assign in candidate.values())
    assert not any("critical_pool" in str(assign.source) for assign in candidate.values())
