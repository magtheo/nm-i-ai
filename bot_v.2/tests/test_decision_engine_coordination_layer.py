from __future__ import annotations

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.models import BotInfo, GameState, GridInfo, OrderInfo, OrderStatus


def _state(*, inventory: list[str], active_required: list[str], active_delivered: list[str]) -> GameState:
    return GameState(
        round=80,
        max_rounds=300,
        grid=GridInfo(width=6, height=6, walls=[]),
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=list(inventory)),
            BotInfo(id=1, position=[2, 1], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=list(active_required),
                items_delivered=list(active_delivered),
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
        drop_off=[4, 4],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _state_two_bots(
    *,
    inv0: list[str],
    pos0: tuple[int, int],
    inv1: list[str],
    pos1: tuple[int, int],
    active_required: list[str],
    active_delivered: list[str],
) -> GameState:
    return GameState(
        round=80,
        max_rounds=300,
        grid=GridInfo(width=10, height=10, walls=[]),
        bots=[
            BotInfo(id=0, position=[int(pos0[0]), int(pos0[1])], inventory=list(inv0)),
            BotInfo(id=1, position=[int(pos1[0]), int(pos1[1])], inventory=list(inv1)),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=list(active_required),
                items_delivered=list(active_delivered),
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
        drop_off=[1, 8],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_coordination_layer_enforces_conversion_floor_and_preview_suppression() -> None:
    cfg = DecisionConfig(
        coordination_layer_enabled=True,
        transition_stash_enabled=True,
        preview_weight=4.0,
        max_concurrent_deliverers=1,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(
        _state(
            inventory=["apples"],
            active_required=["apples", "milk"],
            active_delivered=[],
        )
    )

    assert engine.last_active_remaining_delivered_only == 2
    assert engine.last_active_committed_reliable >= 0
    assert engine.last_active_tail_open is True
    assert engine.last_active_secured is False
    assert engine.last_conversion_floor_target >= 1
    assert policy.strict_active_priority is True
    assert policy.always_deliver_matching is True
    assert policy.transition_stash_enabled is False
    assert policy.preview_weight == 0.0
    assert policy.max_concurrent_deliverers >= 1


def test_coordination_layer_keeps_preview_weight_when_active_secured() -> None:
    cfg = DecisionConfig(
        coordination_layer_enabled=True,
        transition_stash_enabled=True,
        preview_weight=3.5,
        coordination_secure_remaining_threshold=1,
    )
    engine = DecisionEngine(config=cfg)

    policy = engine._effective_policy(
        _state(
            inventory=[],
            active_required=["apples", "milk"],
            active_delivered=["apples"],
        )
    )

    assert engine.last_active_remaining_delivered_only == 1
    assert engine.last_active_secured is True
    assert policy.preview_weight == 3.5
    assert policy.strict_active_priority is True


def test_committed_reliable_requires_short_dropoff_path() -> None:
    cfg = DecisionConfig(
        coordination_layer_enabled=True,
        coordination_reliable_max_dropoff_dist=3,
        coordination_reliable_min_matching_ratio=0.5,
        coordination_secure_remaining_threshold=0,
    )
    engine = DecisionEngine(config=cfg)
    state = _state_two_bots(
        inv0=["milk"],
        pos0=(8, 8),  # too far from dropoff to count as reliable
        inv1=["apples"],
        pos1=(2, 8),  # near dropoff, should count
        active_required=["milk", "apples"],
        active_delivered=[],
    )
    policy = engine._effective_policy(state)

    assert engine.last_active_remaining_delivered_only == 2
    assert engine.last_active_committed_reliable == 1
    assert engine.last_active_committed_reliable_bot_count == 1
    assert engine.last_active_secured_candidate is False
    assert engine.last_active_secured is False
    assert policy.strict_active_priority is True


def test_secured_revoke_triggers_on_delivery_stall() -> None:
    cfg = DecisionConfig(
        coordination_layer_enabled=True,
        coordination_reliable_max_dropoff_dist=8,
        coordination_secure_remaining_threshold=0,
        coordination_secured_progress_stall_rounds=2,
        coordination_secured_revoke_no_assignment_streak=99,
    )
    engine = DecisionEngine(config=cfg)
    state = _state_two_bots(
        inv0=["milk"],
        pos0=(2, 8),
        inv1=["apples"],
        pos1=(2, 7),
        active_required=["milk", "apples"],
        active_delivered=[],
    )

    engine._effective_policy(state)
    policy = engine._effective_policy(state)

    assert engine.last_active_secured_candidate is True
    assert engine.last_active_secured_revoked is True
    assert engine.last_active_secured_revoke_reason_code == 1
    assert engine.last_active_secured is False
    assert policy.preview_weight == 0.0
