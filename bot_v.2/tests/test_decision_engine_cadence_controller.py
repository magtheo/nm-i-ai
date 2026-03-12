from __future__ import annotations

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.models import BotInfo, GameState, GridInfo, OrderInfo, OrderStatus


def _state(
    *,
    round_num: int,
    active_order_index: int,
    active_required: list[str],
    active_delivered: list[str],
) -> GameState:
    return GameState(
        round=round_num,
        max_rounds=300,
        grid=GridInfo(width=4, height=4, walls=[]),
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[],
        orders=[
            OrderInfo(
                id=f"order_{active_order_index}",
                items_required=list(active_required),
                items_delivered=list(active_delivered),
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id=f"order_{active_order_index + 1}",
                items_required=["bread", "eggs"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 3],
        score=0,
        active_order_index=active_order_index,
        total_orders=50,
    )


def test_cadence_controller_activates_by_order_age() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        transition_stash_enabled=True,
        cadence_controller_enabled=True,
        cadence_close_min_order_index=4,
        cadence_target_order_age_rounds=20,
        cadence_close_deficit_threshold=1,
        cadence_close_disable_transition_stash=True,
    )
    engine = DecisionEngine(config=cfg)

    engine._effective_policy(
        _state(
            round_num=120,
            active_order_index=4,
            active_required=["apples", "milk", "bread"],
            active_delivered=[],
        )
    )
    policy = engine._effective_policy(
        _state(
            round_num=145,
            active_order_index=4,
            active_required=["apples", "milk", "bread"],
            active_delivered=[],
        )
    )

    assert engine.last_cadence_close_mode_active is True
    assert policy.demand_commitment_mode == "delivered_only"
    assert policy.transition_stash_enabled is False
    assert engine.last_active_order_age_rounds >= 20


def test_cadence_controller_activates_by_delivered_deficit() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        cadence_controller_enabled=True,
        cadence_close_min_order_index=4,
        cadence_target_order_age_rounds=40,
        cadence_close_deficit_threshold=2,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(
        _state(
            round_num=200,
            active_order_index=5,
            active_required=["apples", "milk", "bread", "eggs"],
            active_delivered=["apples", "milk"],
        )
    )

    assert engine.last_cadence_close_mode_active is True
    assert engine.last_active_remaining_delivered_only == 2
    assert policy.demand_commitment_mode == "delivered_only"
    assert policy.always_deliver_matching is True


def test_cadence_controller_respects_min_order_index_gate() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        cadence_controller_enabled=True,
        cadence_close_min_order_index=4,
        cadence_target_order_age_rounds=1,
        cadence_close_deficit_threshold=2,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(
        _state(
            round_num=250,
            active_order_index=2,
            active_required=["apples", "milk"],
            active_delivered=["apples"],
        )
    )

    assert engine.last_cadence_close_mode_active is False
    assert policy.demand_commitment_mode == "committed"


def test_cadence_controller_can_disable_secondary_assignment_in_close_mode() -> None:
    cfg = DecisionConfig(
        demand_commitment_mode="committed",
        secondary_assignment_enabled=True,
        cadence_controller_enabled=True,
        cadence_close_min_order_index=4,
        cadence_target_order_age_rounds=1,
        cadence_close_deficit_threshold=2,
        cadence_close_disable_secondary_assignment=True,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(
        _state(
            round_num=220,
            active_order_index=6,
            active_required=["apples", "milk"],
            active_delivered=["apples"],
        )
    )

    assert engine.last_cadence_close_mode_active is True
    assert policy.secondary_assignment_enabled is False
