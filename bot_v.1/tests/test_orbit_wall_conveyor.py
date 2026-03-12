from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from bot.models import BotAction, BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "run_nmiai_grocery_bot.py"
ANALYZER_PATH = ROOT / "scripts" / "orbit_wall_log_analyzer.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load_module(RUNNER_PATH, "orbit_wall_runner_test")
ANALYZER = _load_module(ANALYZER_PATH, "orbit_wall_analyzer_test")
WallOrbitEngine = RUNNER.WallOrbitEngine


def _state(
    *,
    bots: list[BotInfo],
    active_items: list[str],
    preview_items: list[str] | None = None,
    round_num: int = 50,
) -> GameState:
    preview = list(preview_items or ["banana"])
    orders = [
        OrderInfo(
            id="order_active",
            items_required=list(active_items),
            items_delivered=[],
            complete=False,
            status=OrderStatus.ACTIVE,
        ),
        OrderInfo(
            id="order_preview",
            items_required=preview,
            items_delivered=[],
            complete=False,
            status=OrderStatus.PREVIEW,
        ),
    ]
    return GameState(
        round=round_num,
        max_rounds=300,
        grid=GridInfo(width=28, height=18, walls=[]),
        bots=bots,
        items=[ItemInfo(id="shelf_item", type="carrot", position=[6, 11])],
        orders=orders,
        drop_off=[1, 16],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def _dest(start: tuple[int, int], action: BotAction) -> tuple[int, int]:
    if action == BotAction.MOVE_LEFT:
        return (start[0] - 1, start[1])
    if action == BotAction.MOVE_RIGHT:
        return (start[0] + 1, start[1])
    if action == BotAction.MOVE_UP:
        return (start[0], start[1] - 1)
    if action == BotAction.MOVE_DOWN:
        return (start[0], start[1] + 1)
    return start


def _action_for(actions, bot_id: int):
    return next(action for action in actions.actions if int(action.bot) == int(bot_id))


def test_exit_branch_has_only_continue_or_delivery_decision() -> None:
    engine = WallOrbitEngine(
        delivery_quota_min=1,
        delivery_quota_max=1,
        branch_exit=(8, 15),
        branch_continue=(7, 15),
        delivery_entry=(8, 16),
        rejoin_branch=(3, 15),
    )
    state = _state(
        bots=[
            BotInfo(id=0, position=[8, 15], inventory=["apple"]),
            BotInfo(id=1, position=[6, 9], inventory=[]),
        ],
        active_items=["apple", "apple"],
    )
    actions = engine.decide(state)
    bot0 = _action_for(actions, 0)
    assert bot0.action in {BotAction.MOVE_LEFT, BotAction.MOVE_DOWN}
    assert _dest((8, 15), bot0.action) in {(7, 15), (8, 16)}


def test_exit_to_delivery_corridor_forbidden_without_delivery_token() -> None:
    engine = WallOrbitEngine(
        delivery_quota_min=0,
        delivery_quota_max=0,
        branch_exit=(8, 15),
        branch_continue=(7, 15),
        delivery_entry=(8, 16),
        rejoin_branch=(3, 15),
    )
    state = _state(
        bots=[
            BotInfo(id=0, position=[8, 15], inventory=["apple"]),
            BotInfo(id=1, position=[6, 9], inventory=[]),
        ],
        active_items=["apple", "apple"],
    )
    actions = engine.decide(state)
    bot0 = _action_for(actions, 0)
    assert _dest((8, 15), bot0.action) != (8, 16)


def test_rejoin_at_branch_requires_reserved_slot() -> None:
    state = _state(
        bots=[
            BotInfo(id=0, position=[3, 15], inventory=[]),
            BotInfo(id=1, position=[7, 15], inventory=[]),
        ],
        active_items=["apple"],
    )

    engine_no_slot = WallOrbitEngine(rejoin_branch=(3, 15))
    engine_no_slot._return_mode = {0}

    def _no_slots(self, **_kwargs):
        return {}

    engine_no_slot._assign_return_slots = types.MethodType(_no_slots, engine_no_slot)
    actions_no_slot = engine_no_slot.decide(state)
    bot0_no_slot = _action_for(actions_no_slot, 0)
    assert _dest((3, 15), bot0_no_slot.action) != (4, 15)

    engine_with_slot = WallOrbitEngine(rejoin_branch=(3, 15))
    engine_with_slot._return_mode = {0}

    def _with_slot(self, **_kwargs):
        return {0: 0}

    engine_with_slot._assign_return_slots = types.MethodType(_with_slot, engine_with_slot)
    actions_with_slot = engine_with_slot.decide(state)
    bot0_with_slot = _action_for(actions_with_slot, 0)
    assert _dest((3, 15), bot0_with_slot.action) == (4, 15)


def test_queue_semantics_limits_dropoff_and_stop_line_targets() -> None:
    engine = WallOrbitEngine(
        delivery_quota_min=2,
        delivery_quota_max=2,
        branch_exit=(8, 15),
        branch_continue=(7, 15),
        delivery_entry=(8, 16),
        rejoin_branch=(3, 15),
    )
    state = _state(
        bots=[
            BotInfo(id=0, position=[8, 15], inventory=["apple", "apple"]),
            BotInfo(id=1, position=[7, 15], inventory=["apple"]),
            BotInfo(id=2, position=[6, 15], inventory=[]),
            BotInfo(id=3, position=[5, 15], inventory=[]),
            BotInfo(id=4, position=[4, 15], inventory=[]),
            BotInfo(id=5, position=[4, 9], inventory=[]),
        ],
        active_items=["apple", "apple", "apple"],
    )
    engine.decide(state)
    drop_off = (1, 16)
    stop_line = tuple(engine._topology.stop_line) if engine._topology is not None else (2, 15)

    targets: list[tuple[int, int]] = []
    for snap in engine.last_assignment_snapshot.values():
        source = str(snap.get("source", ""))
        if source not in {"deliver", "queue"}:
            continue
        pickup_pos = snap.get("pickup_pos")
        if not isinstance(pickup_pos, list) or len(pickup_pos) != 2:
            continue
        targets.append((int(pickup_pos[0]), int(pickup_pos[1])))

    assert sum(1 for cell in targets if cell == drop_off) <= 1
    assert sum(1 for cell in targets if cell == stop_line) <= 1
    assert float(engine.last_round_telemetry.get("queue_semantics_violation", 0.0)) == 0.0


def test_delivery_corridor_uses_single_step_left_moves() -> None:
    engine = WallOrbitEngine(
        delivery_quota_min=1,
        delivery_quota_max=1,
        branch_exit=(8, 15),
        branch_continue=(7, 15),
        delivery_entry=(8, 16),
        rejoin_branch=(3, 15),
    )
    state = _state(
        bots=[
            BotInfo(id=0, position=[8, 16], inventory=["apple"]),
            BotInfo(id=1, position=[7, 9], inventory=[]),
        ],
        active_items=["apple", "apple"],
    )
    actions = engine.decide(state)
    bot0 = _action_for(actions, 0)
    assert bot0.action == BotAction.MOVE_LEFT
    assert _dest((8, 16), bot0.action) == (7, 16)


def test_log_analyzer_classifies_spacing_pressure(tmp_path: Path) -> None:
    decision_trace = tmp_path / "decision_trace.jsonl"
    rows = [
        {
            "round": 1,
            "telemetry": {
                "branch_exit_visits": 5,
                "branch_to_delivery": 0,
                "branch_waits": 2,
                "wait_due_to_spacing_guard": 6,
                "wait_due_to_collision_block": 1,
                "wait_due_to_no_assignment": 1,
                "deliver_bots": 2,
                "rejoin_backlog": 2,
                "rejoin_denials": 3,
            },
            "actions": [{"bot": 0, "action": "wait"}],
            "wait_reason_by_bot": {"0": "wait_due_to_spacing_guard"},
        },
        {
            "round": 2,
            "telemetry": {
                "branch_exit_visits": 5,
                "branch_to_delivery": 0,
                "branch_waits": 2,
                "wait_due_to_spacing_guard": 5,
                "wait_due_to_collision_block": 1,
                "wait_due_to_no_assignment": 1,
                "deliver_bots": 2,
                "rejoin_backlog": 2,
                "rejoin_denials": 3,
            },
            "actions": [{"bot": 0, "action": "wait"}],
            "wait_reason_by_bot": {"0": "wait_due_to_spacing_guard"},
        },
    ]
    decision_trace.write_text("\n".join(json.dumps(row, ensure_ascii=True) for row in rows), encoding="utf-8")
    result = ANALYZER.analyze(decision_trace_path=decision_trace)
    reasons = {row.get("reason") for row in result.get("failures", [])}
    assert "branch_underutilized" in reasons
    assert "spacing_guard_dominant_wait" in reasons
