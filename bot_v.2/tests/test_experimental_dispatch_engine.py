from __future__ import annotations

from bot.experimental_dispatch_engine import ExperimentalDispatchConfig, ExperimentalDispatchEngine
from bot.models import BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state() -> GameState:
    return GameState(
        round=12,
        max_rounds=300,
        grid=GridInfo(width=12, height=10, walls=[]),
        bots=[
            BotInfo(id=0, position=[2, 8], inventory=[]),
            BotInfo(id=1, position=[4, 8], inventory=["milk"]),
            BotInfo(id=2, position=[6, 8], inventory=[]),
        ],
        items=[
            ItemInfo(id="i1", type="milk", position=[7, 2]),
            ItemInfo(id="i2", type="bread", position=[7, 3]),
            ItemInfo(id="i3", type="eggs", position=[9, 3]),
        ],
        orders=[
            OrderInfo(
                id="o0",
                items_required=["milk", "bread"],
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


def test_experimental_dispatch_engine_smoke_actions() -> None:
    engine = ExperimentalDispatchEngine(
        config=ExperimentalDispatchConfig(path_window=6),
        order_forecast={1: ["eggs"]},
    )
    state = _state()
    actions = engine.decide(state)

    assert len(actions.actions) == len(state.bots)
    assert {a.bot for a in actions.actions} == {0, 1, 2}
    assert engine.last_decision_ms >= 0.0


def test_experimental_dispatch_engine_populates_telemetry() -> None:
    engine = ExperimentalDispatchEngine(
        config=ExperimentalDispatchConfig(preview_weight_open=0.1),
        order_forecast={1: ["eggs", "eggs"]},
    )
    _ = engine.decide(_state())
    telem = engine.last_round_telemetry

    assert "active_remaining_delivered_only" in telem
    assert "active_committed_reliable" in telem
    assert "critical_dispatch_slots" in telem
    assert "known_supply_types" in telem
    assert telem.get("known_supply_types", 0.0) >= 1.0
