from __future__ import annotations

import json
from pathlib import Path

from bot.max_score import OrderTracker, max_score_for_game
from bot.models import GameState


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_state_round0_medium_redacted.json"


def _load_state() -> GameState:
    return GameState(**json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_max_score_for_game_medium_bound() -> None:
    state = _load_state()
    info = max_score_for_game(state, difficulty="medium")

    assert info.exact is False
    assert info.total_orders == 50
    assert info.observed_orders == 2
    assert info.observed_items == 8
    assert info.lower_bound_score == 402
    assert info.upper_bound_score == 498
    assert info.max_score is None


def test_order_tracker_bounds_follow_observed_orders() -> None:
    state = _load_state()
    tracker = OrderTracker(difficulty="medium")
    tracker.update(state)
    summary = tracker.summary()

    assert summary["observed_orders"] == 2
    assert summary["lower_bound_score"] == 402
    assert summary["upper_bound_score"] == 498
