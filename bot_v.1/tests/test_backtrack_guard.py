from __future__ import annotations

from collections import deque

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.grid import Grid
from bot.models import BotAction, BotInfo, GameState, GridInfo, OrderInfo, OrderStatus


def _state() -> GameState:
    return GameState(
        round=5,
        max_rounds=300,
        grid=GridInfo(width=6, height=6, walls=[]),
        bots=[
            BotInfo(id=0, position=[2, 2], inventory=[]),
            BotInfo(id=1, position=[3, 2], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["apple"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
        ],
        drop_off=[0, 0],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_backtrack_guard_avoids_immediate_reverse_when_alt_exists() -> None:
    state = _state()
    engine = DecisionEngine(config=DecisionConfig(avoid_immediate_backtrack=True, backtrack_slack=1))
    engine._position_history[0] = deque([(2, 1), (2, 2)], maxlen=4)
    grid = Grid(state.grid)

    action = engine._move_toward(0, (2, 2), (4, 2), grid, state)
    assert action.action != BotAction.MOVE_UP


def test_without_backtrack_guard_engine_can_reverse() -> None:
    state = _state()
    engine = DecisionEngine(config=DecisionConfig(avoid_immediate_backtrack=False))
    engine._position_history[0] = deque([(2, 1), (2, 2)], maxlen=4)
    grid = Grid(state.grid)

    action = engine._move_toward(0, (2, 2), (4, 2), grid, state)
    assert action.action == BotAction.MOVE_UP
