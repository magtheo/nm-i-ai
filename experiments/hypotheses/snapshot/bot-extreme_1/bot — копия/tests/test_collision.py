from __future__ import annotations

from bot.collision import resolve_collisions_with_stats


def test_collision_same_target_cell_lower_priority_waits() -> None:
    plans = [
        (0, (1, 1), (2, 1)),
        (1, (1, 2), (2, 1)),
    ]
    resolved, stats = resolve_collisions_with_stats(plans, occupied=set())

    assert resolved[0] == (2, 1)
    assert resolved[1] == (1, 2)
    assert stats.blocked_moves >= 1
    assert len(set(resolved.values())) == 2


def test_collision_swap_is_prevented() -> None:
    plans = [
        (0, (2, 2), (3, 2)),
        (1, (3, 2), (2, 2)),
    ]
    resolved, stats = resolve_collisions_with_stats(plans, occupied=set())

    # No direct swap and no overlap in final reserved positions.
    assert not (resolved[0] == (3, 2) and resolved[1] == (2, 2))
    assert len(set(resolved.values())) == 2
    assert stats.swaps_prevented >= 1
