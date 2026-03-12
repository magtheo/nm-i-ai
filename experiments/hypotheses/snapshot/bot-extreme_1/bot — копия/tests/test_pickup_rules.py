from __future__ import annotations

from bot.models import GridInfo
from bot.pathfinding import find_all_pickup_positions
from bot.grid import Grid
from bot.decision_engine import DecisionEngine
from bot.models import BotAction, BotInfo, GameState, ItemInfo, OrderInfo, OrderStatus


def test_pickup_positions_prefer_left_and_south() -> None:
    grid = Grid(GridInfo(width=6, height=6, walls=[]))
    assert set(find_all_pickup_positions(grid, (3, 3))) == {(3, 2), (4, 3), (3, 4), (2, 3)}


def test_pick_rule_accepts_any_adjacent_cardinal_cell() -> None:
    item = (3, 3)
    assert DecisionEngine._can_pick_from((2, 3), item) is True
    assert DecisionEngine._can_pick_from((3, 4), item) is True
    assert DecisionEngine._can_pick_from((3, 2), item) is True
    assert DecisionEngine._can_pick_from((4, 3), item) is True
    assert DecisionEngine._can_pick_from((2, 2), item) is False


def test_engine_does_not_overpick_same_active_type_in_one_tick() -> None:
    state = GameState(
        round=0,
        max_rounds=300,
        grid=GridInfo(width=8, height=6, walls=[]),
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=[]),
            BotInfo(id=1, position=[3, 1], inventory=[]),
            BotInfo(id=2, position=[5, 1], inventory=[]),
        ],
        items=[
            ItemInfo(id="item_0", type="milk", position=[1, 2]),
            ItemInfo(id="item_1", type="milk", position=[3, 2]),
            ItemInfo(id="item_2", type="milk", position=[5, 2]),
        ],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["milk"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            ),
            OrderInfo(
                id="order_1",
                items_required=["eggs", "bread", "cream"],
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
    engine = DecisionEngine()
    actions = engine.decide(state)
    picks = [a for a in actions.actions if a.action == BotAction.PICK_UP]
    assert len(picks) <= 1


def test_pickup_feedback_treats_disappeared_item_as_success() -> None:
    engine = DecisionEngine()
    engine._last_pick_attempt_by_bot = {0: "item_0"}
    engine._prev_inventory_by_bot = {0: tuple()}
    engine._pickup_fail_counts = {"item_0": 1}
    engine._blocked_pick_items_until_round = {"item_0": 999}

    state = GameState(
        round=10,
        max_rounds=300,
        grid=GridInfo(width=8, height=6, walls=[]),
        bots=[BotInfo(id=0, position=[1, 1], inventory=[])],
        items=[],
        orders=[
            OrderInfo(
                id="order_0",
                items_required=["milk"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[0, 0],
        score=0,
        active_order_index=0,
        total_orders=50,
    )

    engine._update_pickup_feedback(state)
    assert "item_0" not in engine._pickup_fail_counts
    assert "item_0" not in engine._blocked_pick_items_until_round
