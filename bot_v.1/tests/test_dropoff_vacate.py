from __future__ import annotations

from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.models import BotAction, BotInfo, GameState, GridInfo, OrderInfo, OrderStatus


def test_idle_bot_vacates_dropoff_for_delivery_flow() -> None:
    state = GameState(
        round=50,
        max_rounds=300,
        grid=GridInfo(width=6, height=6, walls=[]),
        bots=[
            BotInfo(id=0, position=[1, 4], inventory=[]),
            BotInfo(id=1, position=[2, 4], inventory=["bread"]),
            BotInfo(id=2, position=[4, 1], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_7",
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 4],
        score=0,
        active_order_index=7,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            clear_adjacent_dropoff_lane=True,
            clear_lane_distance=6,
        )
    )
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[0].action in {
        BotAction.MOVE_UP,
        BotAction.MOVE_DOWN,
        BotAction.MOVE_LEFT,
        BotAction.MOVE_RIGHT,
    }


def test_deliver_bot_sidesteps_when_dropoff_is_hard_blocked() -> None:
    state = GameState(
        round=120,
        max_rounds=300,
        grid=GridInfo(
            width=6,
            height=6,
            walls=[
                [1, 0],
                [0, 1],
                [1, 2],
            ],
        ),
        bots=[
            BotInfo(id=0, position=[1, 1], inventory=["butter", "pasta", "pasta"]),
            BotInfo(id=1, position=[2, 1], inventory=["bread"]),
            BotInfo(id=2, position=[4, 4], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_9",
                items_required=["bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 1],
        score=0,
        active_order_index=9,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            clear_adjacent_dropoff_lane=True,
            clear_lane_distance=6,
        )
    )
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[1].action in {
        BotAction.MOVE_UP,
        BotAction.MOVE_RIGHT,
        BotAction.MOVE_DOWN,
    }


def test_deliver_bot_with_nonmatching_inventory_vacates_dropoff() -> None:
    state = GameState(
        round=180,
        max_rounds=300,
        grid=GridInfo(width=6, height=6, walls=[]),
        bots=[
            BotInfo(id=0, position=[1, 4], inventory=["butter", "pasta", "pasta"]),
            BotInfo(id=1, position=[4, 4], inventory=["bread"]),
            BotInfo(id=2, position=[5, 4], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_12",
                items_required=["bread", "bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 4],
        score=0,
        active_order_index=12,
        total_orders=50,
    )
    engine = DecisionEngine(config=DecisionConfig(force_dropoff_for_full_nonmatching=True))
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[0].action in {
        BotAction.MOVE_UP,
        BotAction.MOVE_RIGHT,
        BotAction.MOVE_LEFT,
        BotAction.MOVE_DOWN,
    }


def test_adjacent_nonmatching_bot_clears_dropoff_lane_for_carrier() -> None:
    state = GameState(
        round=80,
        max_rounds=300,
        grid=GridInfo(width=7, height=7, walls=[]),
        bots=[
            BotInfo(id=0, position=[3, 4], inventory=["bread"]),
            BotInfo(id=1, position=[1, 3], inventory=["pasta"]),
            BotInfo(id=2, position=[6, 6], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_8",
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 4],
        score=0,
        active_order_index=8,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            clear_adjacent_dropoff_lane=True,
            clear_lane_distance=6,
        )
    )
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[1].action in {
        BotAction.MOVE_UP,
        BotAction.MOVE_RIGHT,
        BotAction.MOVE_LEFT,
        BotAction.MOVE_DOWN,
    }


def test_blocked_deliverer_can_detour_even_when_collision_mode_wait() -> None:
    state = GameState(
        round=42,
        max_rounds=300,
        grid=GridInfo(width=7, height=7, walls=[]),
        bots=[
            BotInfo(id=0, position=[3, 4], inventory=["bread"]),
            BotInfo(id=1, position=[2, 4], inventory=["milk", "milk"]),
            BotInfo(id=2, position=[6, 6], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_4",
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 4],
        score=0,
        active_order_index=4,
        total_orders=50,
    )
    engine = DecisionEngine(config=DecisionConfig(collision_aggressiveness="wait"))
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[0].action in {
        BotAction.MOVE_UP,
        BotAction.MOVE_RIGHT,
        BotAction.MOVE_LEFT,
        BotAction.MOVE_DOWN,
    }


def test_relaxed_lane_clear_allows_step_into_vacating_cell() -> None:
    state = GameState(
        round=90,
        max_rounds=300,
        grid=GridInfo(
            width=6,
            height=7,
            walls=[
                [2, 3],
                [2, 5],
            ],
        ),
        bots=[
            BotInfo(id=0, position=[3, 4], inventory=["bread"]),
            BotInfo(id=1, position=[1, 3], inventory=["milk"]),
            BotInfo(id=2, position=[2, 4], inventory=["milk"]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_11",
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 4],
        score=0,
        active_order_index=11,
        total_orders=50,
    )
    engine = DecisionEngine(
        config=DecisionConfig(
            clear_adjacent_dropoff_lane=True,
            clear_lane_distance=6,
        )
    )
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[2].action in {
        BotAction.MOVE_RIGHT,
        BotAction.MOVE_UP,
        BotAction.MOVE_LEFT,
        BotAction.MOVE_DOWN,
    }


def test_idle_nonmatching_bot_stages_to_dropoff_when_active_already_covered() -> None:
    state = GameState(
        round=210,
        max_rounds=300,
        grid=GridInfo(width=8, height=8, walls=[]),
        bots=[
            BotInfo(id=0, position=[5, 5], inventory=["pasta"]),
            BotInfo(id=1, position=[2, 1], inventory=["bread"]),
            BotInfo(id=2, position=[6, 6], inventory=[]),
        ],
        items=[],
        orders=[
            OrderInfo(
                id="order_14",
                items_required=["bread"],
                items_delivered=[],
                complete=False,
                status=OrderStatus.ACTIVE,
            )
        ],
        drop_off=[1, 1],
        score=0,
        active_order_index=14,
        total_orders=50,
    )
    engine = DecisionEngine(config=DecisionConfig(stage_nonmatching_when_active_covered=True))
    actions = engine.decide(state)
    by_bot = {a.bot: a for a in actions.actions}

    assert by_bot[0].action in {
        BotAction.MOVE_UP,
        BotAction.MOVE_RIGHT,
        BotAction.MOVE_LEFT,
        BotAction.MOVE_DOWN,
    }
