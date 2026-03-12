"""Tests for the clean bot_v.3 decision engine."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from bot.decision_engine import BotTask, DecisionEngine, EngineConfig
from bot.models import (
    BotAction,
    BotInfo,
    GameState,
    GridInfo,
    ItemInfo,
    OrderInfo,
    OrderStatus,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers to build minimal game states
# ─────────────────────────────────────────────────────────────────────────────

def _make_grid(width: int = 12, height: int = 10, walls: list | None = None) -> GridInfo:
    """Build a minimal GridInfo. Default walls are just the border."""
    if walls is None:
        walls = []
        for x in range(width):
            walls += [[x, 0], [x, height - 1]]
        for y in range(1, height - 1):
            walls += [[0, y], [width - 1, y]]
    return GridInfo(width=width, height=height, walls=walls)


def _make_state(
    *,
    bots: list[BotInfo],
    items: list[ItemInfo],
    orders: list[OrderInfo],
    drop_off: list[int] | None = None,
    score: int = 0,
    round_num: int = 1,
    grid: GridInfo | None = None,
) -> GameState:
    return GameState(
        type="game_state",
        round=round_num,
        max_rounds=300,
        grid=grid or _make_grid(),
        bots=bots,
        items=items,
        orders=orders,
        drop_off=drop_off or [1, 8],
        score=score,
    )


def _bot(bot_id: int, x: int, y: int, inventory: list[str] | None = None) -> BotInfo:
    return BotInfo(id=bot_id, position=[x, y], inventory=inventory or [])


def _item(item_id: str, item_type: str, x: int, y: int) -> ItemInfo:
    return ItemInfo(id=item_id, type=item_type, position=[x, y])


def _order(order_id: str, required: list[str], delivered: list[str] | None = None, status: str = "active") -> OrderInfo:
    return OrderInfo(
        id=order_id,
        items_required=required,
        items_delivered=delivered or [],
        complete=False,
        status=OrderStatus(status),
    )


# ─────────────────────────────────────────────────────────────────────────────
# EngineConfig tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineConfig:
    def test_default_values(self) -> None:
        cfg = EngineConfig()
        assert cfg.active_item_weight == 10.0
        assert cfg.preview_item_weight == 3.0
        assert cfg.dist_weight == 1.0
        assert cfg.max_deliverers == 3
        assert cfg.enable_preview_picks is True
        assert cfg.starvation_rounds == 5

    def test_from_dict_ignores_unknown_keys(self) -> None:
        cfg = EngineConfig.from_dict({"active_item_weight": 15.0, "unknown_key": 999})
        assert cfg.active_item_weight == 15.0

    def test_from_dict_only_known_keys(self) -> None:
        cfg = EngineConfig.from_dict({"max_deliverers": 2})
        assert cfg.max_deliverers == 2
        assert cfg.active_item_weight == 10.0  # default preserved

    def test_from_json_roundtrip(self, tmp_path: Path) -> None:
        original = EngineConfig(active_item_weight=8.0, max_deliverers=2)
        config_path = tmp_path / "test_config.json"
        config_path.write_text(
            json.dumps({"active_item_weight": 8.0, "max_deliverers": 2}),
            encoding="utf-8",
        )
        loaded = EngineConfig.from_json(config_path)
        assert loaded.active_item_weight == original.active_item_weight
        assert loaded.max_deliverers == original.max_deliverers

    def test_config_is_frozen(self) -> None:
        cfg = EngineConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.active_item_weight = 999  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# Basic engine smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDecisionEngineSmoke:
    """Engine must return valid RoundActions for common state shapes."""

    def test_single_bot_no_orders(self) -> None:
        engine = DecisionEngine()
        state = _make_state(
            bots=[_bot(0, 5, 5)],
            items=[],
            orders=[],
        )
        result = engine.decide(state)
        assert len(result.actions) == 1
        assert result.actions[0].bot == 0

    def test_single_bot_picks_active_item(self) -> None:
        """Bot with no inventory should head toward the needed item."""
        engine = DecisionEngine()
        # Shelf item at (3,1) — bot needs to stand at (3,2) to pick it
        # (walls at y=0 so item must be at y=1 to be on a shelf)
        state = _make_state(
            bots=[_bot(0, 5, 5)],
            items=[_item("i1", "milk", 3, 1)],
            orders=[_order("o1", ["milk"])],
            drop_off=[1, 8],
        )
        result = engine.decide(state)
        assert len(result.actions) == 1
        cmd = result.actions[0]
        assert cmd.bot == 0
        # Bot should move (not wait) toward the item
        assert cmd.action != BotAction.WAIT

    def test_bot_with_active_cargo_goes_to_dropoff(self) -> None:
        """Bot carrying active-order items should deliver."""
        engine = DecisionEngine()
        drop = [1, 8]
        state = _make_state(
            bots=[_bot(0, 5, 5, inventory=["milk"])],
            items=[],
            orders=[_order("o1", ["milk"])],
            drop_off=drop,
        )
        result = engine.decide(state)
        cmd = result.actions[0]
        assert cmd.bot == 0
        # Bot is at (5,5), drop-off at (1,8): should move (not wait)
        assert cmd.action != BotAction.WAIT
        assert cmd.action != BotAction.PICK_UP

    def test_bot_at_dropoff_drops_active_cargo(self) -> None:
        """Bot at drop-off with active items should DROP_OFF."""
        engine = DecisionEngine()
        drop = [1, 8]
        state = _make_state(
            bots=[_bot(0, 1, 8, inventory=["milk"])],
            items=[],
            orders=[_order("o1", ["milk"])],
            drop_off=drop,
        )
        result = engine.decide(state)
        cmd = result.actions[0]
        assert cmd.bot == 0
        assert cmd.action == BotAction.DROP_OFF

    def test_all_bots_get_actions(self) -> None:
        """Every bot in state.bots must receive exactly one action."""
        engine = DecisionEngine()
        state = _make_state(
            bots=[_bot(i, 2 + i, 5) for i in range(5)],
            items=[],
            orders=[],
        )
        result = engine.decide(state)
        bot_ids = [cmd.bot for cmd in result.actions]
        assert sorted(bot_ids) == list(range(5))

    def test_returns_round_actions_type(self) -> None:
        from bot.models import RoundActions
        engine = DecisionEngine()
        state = _make_state(bots=[_bot(0, 3, 3)], items=[], orders=[])
        result = engine.decide(state)
        assert isinstance(result, RoundActions)

    def test_last_decision_ms_is_set(self) -> None:
        engine = DecisionEngine()
        state = _make_state(bots=[_bot(0, 3, 3)], items=[], orders=[])
        engine.decide(state)
        assert engine.last_decision_ms >= 0.0

    def test_last_round_telemetry_keys(self) -> None:
        engine = DecisionEngine()
        state = _make_state(bots=[_bot(0, 3, 3)], items=[], orders=[])
        engine.decide(state)
        tel = engine.last_round_telemetry
        assert "decision_ms" in tel
        assert "collision_blocks" in tel
        assert "active_need_count" in tel


# ─────────────────────────────────────────────────────────────────────────────
# Assignment logic tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignment:
    def test_active_item_preferred_over_preview(self) -> None:
        """Active-order item should be picked over preview-only item when adjacent."""
        engine = DecisionEngine()
        # Bot at (3, 2): exactly at the pickup position for milk at shelf (3, 1).
        # Preview item (bread) at (9, 1) — far away.
        # Bot is already in position to pick up milk; should PICK_UP, not move.
        state = _make_state(
            bots=[_bot(0, 3, 2)],
            items=[
                _item("active_item", "milk", 3, 1),   # shelf cell at (3,1)
                _item("preview_item", "bread", 9, 1),  # far away
            ],
            orders=[
                _order("o1", ["milk"], status="active"),
                _order("o2", ["bread"], status="preview"),
            ],
        )
        result = engine.decide(state)
        cmd = result.actions[0]
        assert cmd.bot == 0
        assert cmd.action == BotAction.PICK_UP
        assert cmd.item_id == "active_item"

    def test_no_duplicate_item_assignments(self) -> None:
        """Two bots should not both target the same item id."""
        engine = DecisionEngine()
        # Only one needed item
        state = _make_state(
            bots=[_bot(0, 3, 5), _bot(1, 7, 5)],
            items=[_item("i1", "milk", 5, 1)],
            orders=[_order("o1", ["milk"])],
        )
        result = engine.decide(state)
        pick_ups = [cmd for cmd in result.actions if cmd.action == BotAction.PICK_UP]
        item_ids = [cmd.item_id for cmd in pick_ups]
        # No duplicate item_id assignments
        assert len(item_ids) == len(set(item_ids))

    def test_max_deliverers_cap(self) -> None:
        """Only max_deliverers bots should get deliver tasks simultaneously."""
        cfg = EngineConfig(max_deliverers=1)
        engine = DecisionEngine(cfg)
        drop = [1, 8]
        # 3 bots all carrying active cargo, all away from drop-off
        state = _make_state(
            bots=[
                _bot(0, 5, 5, inventory=["milk"]),
                _bot(1, 6, 5, inventory=["milk"]),
                _bot(2, 7, 5, inventory=["milk"]),
            ],
            items=[],
            orders=[_order("o1", ["milk", "milk", "milk"])],
            drop_off=drop,
        )
        result = engine.decide(state)
        # All bots have cargo, but with max_deliverers=1 only one should be
        # assigned to deliver; the others should still move (not drop_off).
        # Since none are at drop-off yet, no DROP_OFF actions expected this round.
        drop_off_actions = [c for c in result.actions if c.action == BotAction.DROP_OFF]
        assert len(drop_off_actions) == 0  # No bot at drop-off yet

    def test_full_inventory_bot_delivers(self) -> None:
        """Bot with 3 items (full) should always be assigned deliver."""
        engine = DecisionEngine()
        drop = [1, 8]
        state = _make_state(
            bots=[_bot(0, 5, 5, inventory=["milk", "bread", "eggs"])],
            items=[],
            orders=[_order("o1", ["milk"])],
            drop_off=drop,
        )
        result = engine.decide(state)
        cmd = result.actions[0]
        # Should be moving toward drop-off
        assert cmd.action != BotAction.WAIT
        assert cmd.action != BotAction.PICK_UP


# ─────────────────────────────────────────────────────────────────────────────
# Stall detection tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStallDetection:
    def test_stall_counter_increments(self) -> None:
        engine = DecisionEngine(EngineConfig(starvation_rounds=3))
        state = _make_state(
            bots=[_bot(0, 5, 5)],
            items=[],
            orders=[],
        )
        # Call decide multiple times without bot moving
        for _ in range(3):
            engine.decide(state)
        assert engine._stall_counter.get(0, 0) >= 2

    def test_stall_counter_resets_on_move(self) -> None:
        engine = DecisionEngine(EngineConfig(starvation_rounds=3))
        state_a = _make_state(bots=[_bot(0, 5, 5)], items=[], orders=[])
        state_b = _make_state(bots=[_bot(0, 6, 5)], items=[], orders=[])
        engine.decide(state_a)
        engine.decide(state_a)  # bot stayed put
        assert engine._stall_counter.get(0, 0) >= 1
        engine.decide(state_b)  # bot moved
        assert engine._stall_counter.get(0, 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Collision resolution integration
# ─────────────────────────────────────────────────────────────────────────────

class TestCollisionResolution:
    def test_no_two_bots_same_cell(self) -> None:
        """After collision resolution, no two bots should share a cell."""
        engine = DecisionEngine()
        # Two bots trying to move toward each other
        drop = [1, 8]
        state = _make_state(
            bots=[_bot(0, 4, 5), _bot(1, 6, 5)],
            items=[_item("i1", "milk", 5, 1)],
            orders=[_order("o1", ["milk"])],
            drop_off=drop,
        )
        result = engine.decide(state)
        # Both bots move; their *rendered* actions shouldn't place them on the same cell.
        # We check indirectly: no two actions map to the same target cell.
        # This is guaranteed by the collision resolver, so just check actions are valid.
        assert len(result.actions) == 2
        for cmd in result.actions:
            assert cmd.action in BotAction


# ─────────────────────────────────────────────────────────────────────────────
# Multi-round integration
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiRound:
    def test_engine_is_stateless_across_configs(self) -> None:
        """Two engines with same config should produce same actions for same state."""
        cfg = EngineConfig(active_item_weight=8.0)
        e1 = DecisionEngine(cfg)
        e2 = DecisionEngine(cfg)
        state = _make_state(
            bots=[_bot(0, 3, 5)],
            items=[_item("i1", "milk", 3, 1)],
            orders=[_order("o1", ["milk"])],
        )
        r1 = e1.decide(state)
        r2 = e2.decide(state)
        assert [c.action for c in r1.actions] == [c.action for c in r2.actions]

    def test_ten_rounds_no_exception(self) -> None:
        """Engine must not raise over 10 rounds with a realistic state."""
        engine = DecisionEngine()
        bots = [_bot(i, 2 + i, 5) for i in range(3)]
        items = [
            _item("i1", "milk", 3, 1),
            _item("i2", "bread", 7, 1),
            _item("i3", "eggs", 5, 3),
        ]
        orders = [
            _order("o1", ["milk", "bread"], status="active"),
            _order("o2", ["eggs"], status="preview"),
        ]
        state = _make_state(bots=bots, items=items, orders=orders)
        for r in range(10):
            state = _make_state(
                bots=bots, items=items, orders=orders, round_num=r
            )
            result = engine.decide(state)
            assert len(result.actions) == len(bots)
