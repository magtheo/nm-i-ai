from __future__ import annotations

from bot.assignment import AssignmentPolicy, assign_bots
from bot.grid import Grid
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state(*, active_required: list[str], active_delivered: list[str]) -> GameState:
    bots = [
        BotInfo(id=i, position=[1, 6] if i == 0 else [6, 6], inventory=["apples"] if i == 0 else [])
        for i in range(10)
    ]
    return GameState(
        round=100,
        max_rounds=300,
        grid=GridInfo(width=8, height=8, walls=[]),
        bots=bots,
        items=[
            ItemInfo(id="i1", type="bread", position=[4, 4]),
            ItemInfo(id="i2", type="bread", position=[5, 4]),
            ItemInfo(id="i3", type="milk", position=[4, 3]),
            ItemInfo(id="i4", type="milk", position=[5, 3]),
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
                items_required=["bread", "bread", "milk", "milk"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 7],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _base_policy(**extra: object) -> AssignmentPolicy:
    payload = {
        "strict_active_priority": True,
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


def test_pipeline_budget_caps_secure_preview_preload_to_one() -> None:
    state = _state(active_required=["apples"], active_delivered=[])
    grid = Grid(state.grid)

    baseline = assign_bots(state, grid, policy=_base_policy())
    candidate = assign_bots(
        state,
        grid,
        policy=_base_policy(
            pipeline_budget_enabled=True,
            pipeline_secure_delivered_deficit_threshold=2,
        ),
    )

    baseline_pre = sum(1 for assign in baseline.values() if assign.target_type == "pre_pick")
    candidate_pre = sum(1 for assign in candidate.values() if assign.target_type == "pre_pick")

    assert baseline_pre >= 2
    assert candidate_pre == 1
    assert any("pipeline_secure" in str(assign.source) for assign in candidate.values())


def test_pipeline_budget_keeps_build_mode_preview_at_zero() -> None:
    state = _state(active_required=["apples", "milk", "bread", "eggs"], active_delivered=[])
    grid = Grid(state.grid)

    candidate = assign_bots(
        state,
        grid,
        policy=_base_policy(
            pipeline_budget_enabled=True,
            pipeline_secure_delivered_deficit_threshold=2,
        ),
    )

    candidate_pre = sum(1 for assign in candidate.values() if assign.target_type == "pre_pick")
    assert candidate_pre == 0
    assert any("pipeline_build" in str(assign.source) for assign in candidate.values())
