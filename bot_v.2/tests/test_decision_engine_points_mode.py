from __future__ import annotations

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.models import BotInfo, GameState, GridInfo, OrderInfo, OrderStatus


def _state(*, round_num: int) -> GameState:
    return GameState(
        round=round_num,
        max_rounds=300,
        grid=GridInfo(width=4, height=4, walls=[]),
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["apples", "milk"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["bread", "eggs"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 3],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_late_game_points_mode_applies_delivered_only_override() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        late_game_points_mode_enabled=True,
        late_game_points_rounds_left=90,
        late_game_points_demand_commitment_mode="delivered_only",
        late_game_points_always_deliver_matching=True,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(_state(round_num=220))

    assert policy.demand_commitment_mode == "delivered_only"
    assert policy.strict_active_priority is True
    assert policy.always_deliver_matching is True
    assert engine.last_late_game_points_mode_active is True
    assert engine.last_effective_demand_commitment_mode == "delivered_only"


def test_late_game_points_mode_is_inactive_before_window() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        late_game_points_mode_enabled=True,
        late_game_points_rounds_left=70,
        late_game_points_demand_commitment_mode="delivered_only",
        late_game_points_always_deliver_matching=True,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(_state(round_num=120))

    assert policy.demand_commitment_mode == "committed"
    assert engine.last_late_game_points_mode_active is False
    assert engine.last_effective_demand_commitment_mode == "committed"


def test_late_game_points_mode_invalid_demand_falls_back_to_delivered_only() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        late_game_points_mode_enabled=True,
        late_game_points_rounds_left=120,
        late_game_points_demand_commitment_mode="invalid_mode",
        late_game_points_always_deliver_matching=False,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(_state(round_num=220))

    assert policy.demand_commitment_mode == "delivered_only"
    assert engine.last_late_game_points_mode_active is True
