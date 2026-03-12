from __future__ import annotations

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.models import BotAction, BotActionCommand, BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state(
    *,
    round_num: int,
    bot_position: tuple[int, int] = (1, 1),
    bot_inventory: list[str] | None = None,
    items: list[ItemInfo] | None = None,
    active_required: list[str] | None = None,
    active_delivered: list[str] | None = None,
) -> GameState:
    return GameState(
        round=round_num,
        max_rounds=300,
        grid=GridInfo(width=6, height=6, walls=[]),
        bots=[
            BotInfo(
                id=0,
                position=[int(bot_position[0]), int(bot_position[1])],
                inventory=list(bot_inventory or []),
            )
        ],
        items=list(items or []),
        orders=[
            OrderInfo(
                id="order_0",
                items_required=list(active_required or ["apples", "milk"]),
                items_delivered=list(active_delivered or []),
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


def test_conversion_guard_telemetry_fields_are_exposed() -> None:
    cfg = DecisionConfig(
        conversion_guard_enabled=True,
        conversion_guard_emergency_enabled=True,
        conversion_guard_emergency_min_round=50,
    )
    engine = DecisionEngine(config=cfg)
    engine.decide(_state(round_num=0))
    telemetry = engine.last_round_telemetry

    assert telemetry["conversion_guard_enabled"] == 1.0
    assert telemetry["conversion_guard_emergency_enabled"] == 1.0
    assert telemetry["conversion_guard_emergency_active"] == 0.0
    assert telemetry["conversion_guard_emergency_triggered"] == 0.0
    assert "conversion_guard_pickup_drop_coupling_break" in telemetry
    assert "conversion_guard_delivery_lane_breach" in telemetry
    assert "conversion_guard_combo_warn_active" in telemetry
    assert "conversion_guard_coupling_warn_active" in telemetry
    assert "conversion_guard_trigger_by_combo" in telemetry
    assert "conversion_guard_emergency_reason_code" in telemetry
    assert "wait_due_to_no_target" in telemetry


def test_conversion_guard_emergency_activates_after_coupling_break() -> None:
    cfg = DecisionConfig(
        conversion_guard_enabled=True,
        conversion_guard_emergency_enabled=True,
        conversion_guard_emergency_min_round=0,
        conversion_guard_pickup_drop_min_pickups=1,
        conversion_guard_commitment_stagnation_rounds=1,
        conversion_guard_delivery_lane_stagnation_rounds=1,
        conversion_guard_throughput_lane_rounds=1,
        conversion_guard_throughput_lane_floor=1.0,
        conversion_guard_combo_warn_rounds=1,
        conversion_guard_combo_emergency_rounds=99,
        conversion_guard_coupling_emergency_rounds=1,
        conversion_guard_weak_items_per_drop_threshold=1.0,
        conversion_guard_emergency_duration_rounds=4,
    )
    engine = DecisionEngine(config=cfg)

    state_r0 = _state(
        round_num=0,
        bot_position=(4, 4),
        bot_inventory=["apples"],
        active_required=["apples", "milk"],
        active_delivered=[],
    )
    engine._refresh_conversion_guard_state(0)
    telemetry_r0 = engine._update_conversion_guard_metrics(
        state=state_r0,
        actions=[BotActionCommand(bot=0, action=BotAction.PICK_UP, item_id="i0")],
        assignments={},
    )

    assert telemetry_r0["conversion_guard_pickup_drop_coupling_break"] == 1.0
    assert telemetry_r0["conversion_guard_coupling_warn_active"] == 1.0
    assert telemetry_r0["conversion_guard_emergency_triggered"] == 1.0
    assert telemetry_r0["conversion_guard_emergency_reason_code"] == 1.0
    assert telemetry_r0["conversion_guard_emergency_pending"] == 1.0

    policy_r1 = engine._effective_policy(
        _state(
            round_num=1,
            bot_inventory=["apples"],
            active_required=["apples", "milk"],
            active_delivered=[],
        )
    )

    assert engine._conversion_guard_emergency_active is True
    assert policy_r1.demand_commitment_mode == "delivered_only"
    assert policy_r1.always_deliver_matching is True
    assert policy_r1.transition_stash_enabled is False
    assert policy_r1.preview_weight == 0.0


def test_conversion_guard_emergency_activates_from_combo_streak() -> None:
    cfg = DecisionConfig(
        conversion_guard_enabled=True,
        conversion_guard_emergency_enabled=True,
        conversion_guard_emergency_min_round=0,
        conversion_guard_pickup_drop_min_pickups=3,
        conversion_guard_commitment_stagnation_rounds=1,
        conversion_guard_throughput_lane_rounds=1,
        conversion_guard_throughput_lane_floor=1.0,
        conversion_guard_combo_warn_rounds=1,
        conversion_guard_combo_emergency_rounds=2,
        conversion_guard_weak_items_per_drop_threshold=1.0,
        conversion_guard_emergency_duration_rounds=4,
    )
    engine = DecisionEngine(config=cfg)
    triggered_any = False
    trigger_reason = 0.0
    for round_num in range(4):
        current = _state(
            round_num=round_num,
            bot_position=(4, 4),
            bot_inventory=["apples"],
            active_required=["apples", "milk"],
            active_delivered=[],
        )
        engine._refresh_conversion_guard_state(round_num)
        telemetry = engine._update_conversion_guard_metrics(
            state=current,
            actions=[BotActionCommand(bot=0, action=BotAction.WAIT)],
            assignments={},
        )
        if telemetry["conversion_guard_emergency_triggered"] == 1.0:
            triggered_any = True
            trigger_reason = telemetry["conversion_guard_emergency_reason_code"]

    assert telemetry["conversion_guard_combo_warn_active"] == 1.0
    assert telemetry["conversion_guard_trigger_by_combo"] == 1.0
    assert triggered_any is True
    assert telemetry["conversion_guard_emergency_active"] == 1.0
    assert trigger_reason == 2.0
