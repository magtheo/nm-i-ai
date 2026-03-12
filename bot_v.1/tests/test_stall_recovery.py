from __future__ import annotations

from collections import deque

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def test_stall_recovery_switches_policy_when_no_progress() -> None:
    state = GameState(
        round=0,
        max_rounds=300,
        grid=GridInfo(width=8, height=8, walls=[]),
        bots=[
            BotInfo(id=0, position=[6, 6], inventory=[]),
            BotInfo(id=1, position=[6, 6], inventory=[]),
            BotInfo(id=2, position=[6, 6], inventory=[]),
        ],
        items=[ItemInfo(id="item_0", type="cheese", position=[3, 3])],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["cheese", "eggs", "pasta"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["milk", "milk", "yogurt"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 6],
        score=0,
        active_order_index=0,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            preview_weight=5.0,
            force_dropoff_for_full_nonmatching=False,
            strict_active_priority=False,
            stall_round_threshold=2,
            stall_recovery_rounds=5,
            stall_recovery_preview_weight=0.0,
            stall_recovery_force_dropoff=True,
            stall_recovery_strict_active=True,
            tie_break_seed=11,
            escape_mode_enabled=True,
            escape_tie_break_seed_offset=1009,
            escape_clear_lane_distance=5,
        )
    )

    p0 = engine._effective_policy(state)
    assert p0.preview_weight == 5.0
    assert p0.force_dropoff_for_full_nonmatching is False
    assert p0.strict_active_priority is False

    state_same = state.model_copy(update={"round": 1})
    _ = engine._effective_policy(state_same)
    state_stalled = state.model_copy(update={"round": 2})
    p2 = engine._effective_policy(state_stalled)
    assert p2.preview_weight == 0.0
    assert p2.force_dropoff_for_full_nonmatching is True
    assert p2.strict_active_priority is True
    assert p2.clear_adjacent_dropoff_lane is True
    assert p2.tie_break_dynamic is True
    assert p2.tie_break_seed == 11 + 1009 + state.active_order_index
    assert p2.clear_lane_distance == 5


def test_stall_recovery_clears_short_term_memory() -> None:
    state = GameState(
        round=0,
        max_rounds=300,
        grid=GridInfo(width=8, height=8, walls=[]),
        bots=[
            BotInfo(id=0, position=[6, 6], inventory=[]),
            BotInfo(id=1, position=[6, 6], inventory=[]),
            BotInfo(id=2, position=[6, 6], inventory=[]),
        ],
        items=[ItemInfo(id="item_0", type="cheese", position=[3, 3])],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["cheese", "eggs", "pasta"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["milk", "milk", "yogurt"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 6],
        score=0,
        active_order_index=0,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            stall_round_threshold=1,
            stall_recovery_rounds=5,
        )
    )
    engine._sticky_targets = {0: "item_0"}
    engine._pickup_fail_counts = {"item_0": 2}
    engine._blocked_pick_items_until_round = {"item_0": 50}

    _ = engine._effective_policy(state)
    _ = engine._effective_policy(state.model_copy(update={"round": 1}))

    assert engine._sticky_targets == {}
    assert engine._pickup_fail_counts == {}
    assert engine._blocked_pick_items_until_round == {}


def test_deadlock_cycle_signature_triggers_recovery() -> None:
    state = GameState(
        round=20,
        max_rounds=300,
        grid=GridInfo(width=8, height=8, walls=[]),
        bots=[
            BotInfo(id=0, position=[6, 6], inventory=[]),
            BotInfo(id=1, position=[5, 6], inventory=[]),
            BotInfo(id=2, position=[4, 6], inventory=[]),
        ],
        items=[ItemInfo(id="item_0", type="cheese", position=[3, 3])],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["cheese", "eggs", "pasta"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["milk", "milk", "yogurt"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 6],
        score=0,
        active_order_index=0,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            stall_round_threshold=50,
            stall_recovery_rounds=5,
            stall_recovery_preview_weight=0.0,
            stall_recovery_force_dropoff=True,
            stall_recovery_strict_active=True,
            tie_break_seed=3,
            escape_mode_enabled=True,
            escape_tie_break_seed_offset=1009,
        )
    )
    sig_a = ((0, (6, 6)), (1, (5, 6)), (2, (4, 6)))
    sig_b = ((0, (6, 5)), (1, (5, 5)), (2, (4, 5)))
    engine._joint_signature_history = deque([sig_a, sig_b, sig_a, sig_b, sig_a, sig_b], maxlen=12)

    p = engine._effective_policy(state)
    assert p.preview_weight == 0.0
    assert p.force_dropoff_for_full_nonmatching is True
    assert p.strict_active_priority is True
    assert p.clear_adjacent_dropoff_lane is True
    assert p.tie_break_dynamic is True
    assert p.tie_break_seed == 3 + 1009 + state.active_order_index


def test_escape_mode_can_be_disabled_while_recovery_still_runs() -> None:
    state = GameState(
        round=0,
        max_rounds=300,
        grid=GridInfo(width=8, height=8, walls=[]),
        bots=[
            BotInfo(id=0, position=[6, 6], inventory=[]),
            BotInfo(id=1, position=[6, 6], inventory=[]),
            BotInfo(id=2, position=[6, 6], inventory=[]),
        ],
        items=[ItemInfo(id="item_0", type="cheese", position=[3, 3])],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["cheese", "eggs", "pasta"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["milk", "milk", "yogurt"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[1, 6],
        score=0,
        active_order_index=0,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            stall_round_threshold=1,
            stall_recovery_rounds=5,
            stall_recovery_preview_weight=0.0,
            stall_recovery_force_dropoff=True,
            stall_recovery_strict_active=True,
            tie_break_seed=7,
            escape_mode_enabled=False,
        )
    )
    _ = engine._effective_policy(state)
    p = engine._effective_policy(state.model_copy(update={"round": 1}))

    assert p.clear_adjacent_dropoff_lane is True
    assert p.tie_break_dynamic is False
    assert p.tie_break_seed == 7


def test_forecast_reconcile_drops_future_on_mismatch() -> None:
    state = GameState(
        round=0,
        max_rounds=300,
        grid=GridInfo(width=6, height=6, walls=[]),
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[ItemInfo(id="item_0", type="apple", position=[3, 1])],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["apple"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["banana"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.PREVIEW,
            ),
        ],
        drop_off=[0, 0],
        score=0,
        active_order_index=0,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(lookahead_orders=2),
        order_forecast={
            0: ["wrong_item"],
            1: ["banana"],
            2: ["carrot"],
        },
    )

    _ = engine.decide(state)

    assert engine._order_forecast == {}


def test_congestion_auction_only_activates_in_congested_regime() -> None:
    engine = DecisionEngine(
        config=DecisionConfig(
            assignment_strategy="greedy",
            auction_option_depth=12,
            auction_allow_skip=True,
            congestion_auction_enabled=True,
            congestion_auction_dropoff_trigger=0.67,
            congestion_auction_corridor_trigger=0.67,
            congestion_auction_blocked_trigger=2,
            congestion_auction_option_depth=9,
            congestion_auction_dropoff_penalty=1.0,
            congestion_auction_corridor_penalty=1.0,
        )
    )

    normal = engine._apply_regime_overrides(
        policy=engine.assignment_policy,
        dropoff_zone_density=0.1,
        corridor_density=0.2,
    )
    assert normal.assignment_strategy == "greedy"

    jammed = engine._apply_regime_overrides(
        policy=engine.assignment_policy,
        dropoff_zone_density=0.8,
        corridor_density=0.2,
    )
    assert jammed.assignment_strategy == "auction"
    assert jammed.auction_allow_skip is True
    assert jammed.auction_option_depth == 9
