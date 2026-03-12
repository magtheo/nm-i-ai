from __future__ import annotations

from collections import Counter

from bot.expert_supply_strategy import (
    CLUSTER_UPPER,
    ExpertSupplyStrategyEngine,
    PHASE_ACTIVE,
    ROLE_COURIER,
    ROLE_HARVESTER,
    ShelfRef,
)
from bot.grid import Grid
from bot.models import BotAction, BotInfo, GameState, GridInfo, ItemInfo, OrderInfo, OrderStatus


def _state(
    *,
    round_idx: int,
    bots: list[BotInfo],
    items: list[ItemInfo],
    active_required: list[str],
    active_delivered: list[str] | None = None,
    preview_required: list[str] | None = None,
    preview_delivered: list[str] | None = None,
    drop_off: tuple[int, int] = (1, 6),
) -> GameState:
    orders = [
        OrderInfo(
            id="active-0",
            items_required=list(active_required),
            items_delivered=list(active_delivered or []),
            complete=False,
            status=OrderStatus.ACTIVE,
        )
    ]
    if preview_required is not None:
        orders.append(
            OrderInfo(
                id="preview-0",
                items_required=list(preview_required),
                items_delivered=list(preview_delivered or []),
                complete=False,
                status=OrderStatus.PREVIEW,
            )
        )
    return GameState(
        type="game_state",
        round=round_idx,
        max_rounds=300,
        grid=GridInfo(width=12, height=12, walls=[]),
        bots=bots,
        items=items,
        orders=orders,
        drop_off=[drop_off[0], drop_off[1]],
        score=0,
        active_order_index=0,
        total_orders=50,
    )


def test_role_split_defaults_to_4_4_2() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=i, position=[2 + i, 2], inventory=[])
        for i in range(10)
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[8, 8]),
    ]
    state = _state(
        round_idx=0,
        bots=bots,
        items=items,
        active_required=["A"],
    )
    engine.decide(state)
    role_counts = Counter(engine._roles.values())
    assert role_counts["courier"] == 4
    assert role_counts["harvester"] == 4
    assert role_counts["flex"] == 2


def test_preview_pick_blocked_when_active_not_secured() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[3, 3], inventory=[]),
        BotInfo(id=1, position=[1, 6], inventory=[]),
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[9, 9]),
        ItemInfo(id="p1", type="P", position=[3, 4]),
    ]
    state = _state(
        round_idx=20,
        bots=bots,
        items=items,
        active_required=["A"],
        preview_required=["P"],
    )
    actions = engine.decide(state)
    bot0 = next(action for action in actions.actions if action.bot == 0)
    assert not (bot0.action == BotAction.PICK_UP and bot0.item_id == "p1")


def test_preview_pick_allowed_when_active_secured_by_committed() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[3, 3], inventory=[]),
        BotInfo(id=1, position=[3, 6], inventory=["A"]),
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[9, 9]),
        ItemInfo(id="p1", type="P", position=[3, 4]),
    ]
    state = _state(
        round_idx=20,
        bots=bots,
        items=items,
        active_required=["A"],
        preview_required=["P"],
    )
    actions = engine.decide(state)
    bot0 = next(action for action in actions.actions if action.bot == 0)
    assert bot0.action == BotAction.PICK_UP
    assert bot0.item_id == "p1"


def test_drop_queue_has_single_drop_and_stopline_target() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[6, 6], inventory=["A"]),
        BotInfo(id=1, position=[7, 6], inventory=["A"]),
        BotInfo(id=2, position=[8, 6], inventory=["A"]),
        BotInfo(id=3, position=[9, 6], inventory=["A"]),
    ]
    queue_map = engine._assign_drop_queue(
        bots=bots,
        drop=(1, 16),
        stop=(2, 16),
        queue=[(3, 16), (4, 16), (5, 16)],
        active_remaining=Counter({"A": 4}),
        active_committed=Counter({"A": 4}),
    )
    targets = list(queue_map.values())
    assert sum(1 for target in targets if target == (1, 16)) <= 1
    assert sum(1 for target in targets if target == (2, 16)) <= 1


def test_idle_lane_clear_moves_empty_bot_off_drop_lane() -> None:
    engine = ExpertSupplyStrategyEngine()
    grid = GridInfo(width=28, height=18, walls=[])
    from bot.grid import Grid

    step = engine._idle_lane_clear_step(
        grid=Grid(grid),
        pos=(16, 16),
        drop=(1, 16),
        blocked=set(),
        forbidden=set(),
    )
    assert step != (16, 16)
    assert step[1] != 16


def test_active_committed_is_conservative_for_far_single_item() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[25, 2], inventory=["A"]),
        BotInfo(id=1, position=[3, 6], inventory=["A"]),
    ]
    committed = engine._count_active_committed(
        bots,
        Counter({"A": 2}),
        drop=(1, 6),
        commit_radius=8,
    )
    # Near bot counts; far single-item bot does not over-satisfy deficit.
    assert committed["A"] == 1


def test_deadweight_on_drop_does_not_spam_dropoff() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[1, 6], inventory=["B"]),
        BotInfo(id=1, position=[3, 6], inventory=[]),
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[3, 5]),
        ItemInfo(id="b1", type="B", position=[4, 5]),
    ]
    state = _state(
        round_idx=40,
        bots=bots,
        items=items,
        active_required=["A"],
        preview_required=["B"],
        drop_off=(1, 6),
    )
    actions = engine.decide(state)
    bot0 = next(action for action in actions.actions if action.bot == 0)
    assert bot0.action != BotAction.DROP_OFF


def test_forced_critical_tail_escalates_with_no_near_committed() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[2, 6], inventory=["C"]),  # near committed cargo covers one type
        BotInfo(id=1, position=[4, 6], inventory=[]),
        BotInfo(id=2, position=[5, 6], inventory=[]),
        BotInfo(id=3, position=[6, 6], inventory=[]),
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[4, 5]),
        ItemInfo(id="b1", type="B", position=[5, 5]),
        ItemInfo(id="c1", type="C", position=[6, 5]),
    ]
    state = _state(
        round_idx=80,
        bots=bots,
        items=items,
        active_required=["A", "B", "C"],
        drop_off=(1, 6),
    )
    engine.decide(state)
    assert engine.last_round_debug.get("force_critical_tail") is True
    hunters = list(engine.last_round_debug.get("critical_hunter_ids", []))
    assert 1 <= len(hunters) <= 2


def test_drop_queue_relaxes_under_low_pressure() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[20, 2], inventory=["A"]),
        BotInfo(id=1, position=[21, 2], inventory=["A"]),
        BotInfo(id=2, position=[22, 2], inventory=["A"]),
        BotInfo(id=3, position=[23, 2], inventory=["A"]),
        BotInfo(id=4, position=[24, 2], inventory=["A"]),
        BotInfo(id=5, position=[25, 2], inventory=["A"]),
    ]
    queue_map = engine._assign_drop_queue(
        bots=bots,
        drop=(1, 16),
        stop=(2, 16),
        queue=[(3, 16), (4, 16), (5, 16)],
        active_remaining=Counter({"A": 6}),
        active_committed=Counter({"A": 6}),
        corridor_occupancy=1,
        queue_hold_burst_recent=False,
    )
    # Under low pressure we admit only a small queue head, not all deliverers.
    assert 1 <= len(queue_map) <= 3


def test_drop_queue_does_not_relax_on_queue_hold_burst() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[20, 2], inventory=["A"]),
        BotInfo(id=1, position=[21, 2], inventory=["A"]),
        BotInfo(id=2, position=[22, 2], inventory=["A"]),
        BotInfo(id=3, position=[23, 2], inventory=["A"]),
        BotInfo(id=4, position=[24, 2], inventory=["A"]),
        BotInfo(id=5, position=[25, 2], inventory=["A"]),
    ]
    queue_map = engine._assign_drop_queue(
        bots=bots,
        drop=(1, 16),
        stop=(2, 16),
        queue=[(3, 16), (4, 16), (5, 16)],
        active_remaining=Counter({"A": 6}),
        active_committed=Counter({"A": 6}),
        corridor_occupancy=1,
        queue_hold_burst_recent=True,
    )
    assert len(queue_map) >= 4


def test_drop_queue_does_not_relax_when_corridor_busy() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[20, 2], inventory=["A"]),
        BotInfo(id=1, position=[21, 2], inventory=["A"]),
        BotInfo(id=2, position=[22, 2], inventory=["A"]),
        BotInfo(id=3, position=[23, 2], inventory=["A"]),
        BotInfo(id=4, position=[24, 2], inventory=["A"]),
        BotInfo(id=5, position=[25, 2], inventory=["A"]),
    ]
    queue_map = engine._assign_drop_queue(
        bots=bots,
        drop=(1, 16),
        stop=(2, 16),
        queue=[(3, 16), (4, 16), (5, 16)],
        active_remaining=Counter({"A": 6}),
        active_committed=Counter({"A": 6}),
        corridor_occupancy=5,
        queue_hold_burst_recent=False,
    )
    assert len(queue_map) >= 4


def test_critical_hunter_admits_one_by_default_in_contested_corridor() -> None:
    engine = ExpertSupplyStrategyEngine()
    engine._roles = {0: "flex", 1: "flex", 2: "harvester"}
    engine._type_to_shelves = {
        "A": [ShelfRef(item_id="a1", pos=(9, 3))],
        "B": [ShelfRef(item_id="b1", pos=(9, 4))],
    }
    bots = [
        BotInfo(id=0, position=[6, 3], inventory=[]),
        BotInfo(id=1, position=[6, 4], inventory=[]),
        BotInfo(id=2, position=[7, 3], inventory=[]),
    ]
    hunters, _targets, overlap, corridor_occ = engine._select_critical_hunters(
        bots=bots,
        critical_types={"A", "B"},
        active_remaining=Counter({"A": 1, "B": 1}),
        active_committed=Counter(),
        drop=(1, 6),
    )
    assert len(hunters) == 1
    assert overlap == 0.0
    assert corridor_occ >= 2


def test_critical_hunter_allows_second_when_disjoint_corridor() -> None:
    engine = ExpertSupplyStrategyEngine()
    engine._roles = {0: "flex", 1: "flex"}
    engine._type_to_shelves = {
        "A": [ShelfRef(item_id="a1", pos=(9, 2))],
        "B": [ShelfRef(item_id="b1", pos=(9, 10))],
    }
    bots = [
        BotInfo(id=0, position=[7, 2], inventory=[]),
        BotInfo(id=1, position=[7, 10], inventory=[]),
    ]
    hunters, _targets, overlap, _corridor_occ = engine._select_critical_hunters(
        bots=bots,
        critical_types={"A", "B"},
        active_remaining=Counter({"A": 1, "B": 1}),
        active_committed=Counter(),
        drop=(1, 6),
    )
    assert len(hunters) == 2
    assert overlap == 0.0


def test_critical_hunter_blocks_second_when_same_lane_even_if_far() -> None:
    engine = ExpertSupplyStrategyEngine()
    engine._roles = {0: "flex", 1: "flex"}
    engine._type_to_shelves = {
        "A": [ShelfRef(item_id="a1", pos=(8, 2))],
        "B": [ShelfRef(item_id="b1", pos=(20, 2))],
    }
    bots = [
        BotInfo(id=0, position=[7, 2], inventory=[]),
        BotInfo(id=1, position=[19, 2], inventory=[]),
    ]
    hunters, _targets, _overlap, _corridor_occ = engine._select_critical_hunters(
        bots=bots,
        critical_types={"A", "B"},
        active_remaining=Counter({"A": 1, "B": 1}),
        active_committed=Counter(),
        drop=(1, 16),
    )
    assert len(hunters) == 1


def test_forced_critical_tail_suppresses_after_long_no_progress_on_single_type_deficit() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[2, 6], inventory=["B"]),  # near committed cargo
        BotInfo(id=1, position=[3, 6], inventory=["C"]),  # near committed cargo
        BotInfo(id=2, position=[5, 6], inventory=[]),
        BotInfo(id=3, position=[6, 6], inventory=[]),
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[4, 5]),
        ItemInfo(id="b1", type="B", position=[5, 5]),
        ItemInfo(id="c1", type="C", position=[6, 5]),
    ]
    state = _state(
        round_idx=90,
        bots=bots,
        items=items,
        active_required=["A", "B", "C"],
        drop_off=(1, 6),
    )
    forced_flags: list[bool] = []
    for _ in range(26):
        engine.decide(state)
        forced_flags.append(bool(engine.last_round_debug.get("force_critical_tail")))
    assert any(forced_flags[:8])
    assert forced_flags[-1] is False
    assert bool(engine.last_round_debug.get("force_tail_suppressed")) is True


def test_harvester_cluster_preferences_split_upper_lower() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [BotInfo(id=i, position=[2 + i, 2], inventory=[]) for i in range(10)]
    items = [ItemInfo(id="a1", type="A", position=[8, 8])]
    state = _state(
        round_idx=0,
        bots=bots,
        items=items,
        active_required=["A"],
    )
    engine.decide(state)
    harvester_ids = [bid for bid, role in engine._roles.items() if role == ROLE_HARVESTER]
    prefs = {engine._cluster_pref_by_bot.get(int(bid)) for bid in harvester_ids}
    assert prefs == {"upper", "lower"}


def test_choose_target_enforces_harvester_cluster_and_courier_radius() -> None:
    engine = ExpertSupplyStrategyEngine()
    bot = BotInfo(id=42, position=[4, 4], inventory=[])
    state = _state(
        round_idx=40,
        bots=[bot],
        items=[
            ItemInfo(id="upper", type="A", position=[6, 3]),
            ItemInfo(id="lower", type="A", position=[6, 10]),
            ItemInfo(id="far", type="B", position=[20, 2]),
        ],
        active_required=["A", "B"],
        drop_off=(1, 6),
    )
    engine._ensure_supply_index(state)
    grid = Grid(state.grid)
    active_remaining = Counter({"A": 1, "B": 1})
    visible_ids = {"upper", "lower", "far"}

    harvester_choice = engine._choose_target(
        bot,
        ROLE_HARVESTER,
        PHASE_ACTIVE,
        grid,
        (1, 6),
        active_remaining,
        Counter(),
        Counter(),
        False,
        False,
        set(),
        CLUSTER_UPPER,
        True,
        set(),
        visible_ids,
        set(),
    )
    assert harvester_choice is not None
    assert harvester_choice.item_id == "upper"

    courier_choice = engine._choose_target(
        bot,
        ROLE_COURIER,
        PHASE_ACTIVE,
        grid,
        (1, 6),
        Counter({"B": 1}),
        Counter(),
        Counter(),
        False,
        False,
        set(),
        "center",
        False,
        set(),
        {"far"},
        set(),
    )
    assert courier_choice is None


def test_active_committed_ignores_far_multi_item_inventory() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[25, 2], inventory=["A", "A"]),
        BotInfo(id=1, position=[2, 6], inventory=["A"]),
    ]
    committed = engine._count_active_committed(
        bots,
        Counter({"A": 3}),
        drop=(1, 6),
        commit_radius=8,
    )
    assert committed["A"] == 1


def test_should_deliver_keeps_courier_as_conversion_lane() -> None:
    engine = ExpertSupplyStrategyEngine()
    bot = BotInfo(id=0, position=[5, 6], inventory=["A"])
    should = engine._should_deliver(
        bot,
        Counter({"A": 3, "B": 2, "C": 1}),
        Counter({"A": 1}),
        drop=(1, 6),
        role=ROLE_COURIER,
        force_delivery=False,
    )
    assert should is True


def test_choose_target_supports_sustain_active_lane_when_deficit_is_covered() -> None:
    engine = ExpertSupplyStrategyEngine()
    bot = BotInfo(id=42, position=[4, 4], inventory=[])
    state = _state(
        round_idx=40,
        bots=[bot],
        items=[ItemInfo(id="a1", type="A", position=[6, 3])],
        active_required=["A"],
        drop_off=(1, 6),
    )
    engine._ensure_supply_index(state)
    choice = engine._choose_target(
        bot,
        ROLE_HARVESTER,
        PHASE_ACTIVE,
        Grid(state.grid),
        (1, 6),
        Counter({"A": 1}),
        Counter(),
        Counter({"A": 1}),
        False,
        True,
        set(),
        CLUSTER_UPPER,
        False,
        set(),
        {"a1"},
        set(),
    )
    assert choice is not None
    assert choice.item_id == "a1"
    assert choice.source in {"active", "active_sustain"}


def test_stage_fallback_avoids_no_target_wait_for_empty_bot() -> None:
    engine = ExpertSupplyStrategyEngine()
    bots = [
        BotInfo(id=0, position=[10, 10], inventory=[]),
        BotInfo(id=1, position=[2, 6], inventory=["A"]),
    ]
    items = [
        ItemInfo(id="a1", type="A", position=[8, 8]),
    ]
    state = _state(
        round_idx=60,
        bots=bots,
        items=items,
        active_required=["A"],
        drop_off=(1, 6),
    )
    actions = engine.decide(state)
    bot0 = next(action for action in actions.actions if action.bot == 0)
    # Empty bot should stage toward known active shelf instead of waiting with no target.
    assert bot0.action != BotAction.WAIT
