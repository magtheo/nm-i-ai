from __future__ import annotations

from forge.protocol import OrderState
from forge.simulator import GrocerySimulator


def test_invalid_action_is_rejected_immediately() -> None:
    sim = GrocerySimulator(difficulty="easy", seed=7001, max_rounds=10, strict_actions=True)

    def bad_strategy(_state: dict):
        return [{"bot": 0, "action": "teleport"}]

    summary = sim.run(bad_strategy)
    assert summary.error is not None
    assert "Allowed actions" in summary.error


def test_collisions_resolve_in_bot_id_order() -> None:
    sim = GrocerySimulator(difficulty="medium", seed=7002, max_rounds=1, strict_actions=True)

    sim.bots[0].position = (4, 1)
    sim.bots[1].position = (6, 1)

    actions = [
        {"bot": 0, "action": "move_right"},
        {"bot": 1, "action": "move_left"},
        {"bot": 2, "action": "wait"},
    ]
    sim._apply_round_actions(actions, round_idx=0)

    assert sim.bots[0].position == (5, 1)
    assert sim.bots[1].position == (6, 1)
    assert sim.collision_blocks == 1


def test_dropoff_scoring_and_auto_delivery_chain() -> None:
    sim = GrocerySimulator(difficulty="easy", seed=7001, max_rounds=1, strict_actions=True)
    sim.orders = [
        OrderState(id="order_0", items_required=["milk"]),
        OrderState(id="order_1", items_required=["butter"]),
        OrderState(id="order_2", items_required=["cheese"]),
    ]
    sim.total_orders = len(sim.orders)
    sim.active_order_index = 0

    bot = sim.bots[0]
    bot.position = sim.drop_off
    bot.inventory = ["milk", "butter"]

    sim._apply_round_actions([{"bot": bot.id, "action": "drop_off"}], round_idx=0)

    assert sim.items_delivered == 2
    assert sim.orders_completed == 2
    assert sim.score == 12
    assert sim.active_order_index == 2
    assert bot.inventory == []
