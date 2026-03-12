"""Max-score bounds/exact computation for Grocery Bot."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import GameState


ORDER_BONUS = 5

_DIFFICULTY_ITEM_BOUNDS: dict[str, tuple[int, int]] = {
    "easy": (3, 4),
    "medium": (3, 5),
    "hard": (3, 5),
    "expert": (4, 6),
}


def _infer_difficulty(state: GameState, difficulty: str | None = None) -> str:
    if difficulty:
        return difficulty.lower()
    by_bots = {1: "easy", 3: "medium", 5: "hard", 10: "expert"}
    return by_bots.get(len(state.bots), "medium")


def _difficulty_item_bounds(difficulty: str) -> tuple[int, int]:
    return _DIFFICULTY_ITEM_BOUNDS.get(difficulty.lower(), (3, 5))


@dataclass(frozen=True)
class MaxScoreInfo:
    difficulty: str
    total_orders: int
    observed_orders: int
    unseen_orders: int
    observed_items: int
    min_items_per_unseen_order: int
    max_items_per_unseen_order: int
    min_total_items: int
    max_total_items: int
    lower_bound_score: int
    upper_bound_score: int
    exact: bool
    total_items_needed: int | None
    max_score: int | None


def max_score_for_game(
    state: GameState,
    *,
    difficulty: str | None = None,
) -> MaxScoreInfo:
    """Compute exact max score if all orders are visible, else a bound."""
    diff = _infer_difficulty(state, difficulty=difficulty)
    min_per_order, max_per_order = _difficulty_item_bounds(diff)

    total_orders = int(state.total_orders)
    observed_orders = len(state.orders)
    unseen_orders = max(0, total_orders - observed_orders)
    observed_items = sum(len(order.items_required) for order in state.orders)

    min_total_items = observed_items + unseen_orders * min_per_order
    max_total_items = observed_items + unseen_orders * max_per_order
    lower_score = min_total_items + total_orders * ORDER_BONUS
    upper_score = max_total_items + total_orders * ORDER_BONUS

    exact = observed_orders >= total_orders
    total_items_needed = max_total_items if exact else None
    max_score = upper_score if exact else None

    return MaxScoreInfo(
        difficulty=diff,
        total_orders=total_orders,
        observed_orders=observed_orders,
        unseen_orders=unseen_orders,
        observed_items=observed_items,
        min_items_per_unseen_order=min_per_order,
        max_items_per_unseen_order=max_per_order,
        min_total_items=min_total_items,
        max_total_items=max_total_items,
        lower_bound_score=lower_score,
        upper_bound_score=upper_score,
        exact=exact,
        total_items_needed=total_items_needed,
        max_score=max_score,
    )


@dataclass
class OrderTracker:
    """Track observed orders and recompute score bounds during gameplay."""

    difficulty: str | None = None
    total_orders: int = 50
    observed: dict[str, list[str]] = field(default_factory=dict)
    completed_ids: set[str] = field(default_factory=set)

    def update(self, state: GameState) -> None:
        self.total_orders = state.total_orders
        if self.difficulty is None:
            self.difficulty = _infer_difficulty(state)
        for order in state.orders:
            if order.id not in self.observed:
                self.observed[order.id] = list(order.items_required)
            if order.complete:
                self.completed_ids.add(order.id)

    def as_info(self) -> MaxScoreInfo:
        diff = (self.difficulty or "medium").lower()
        min_per_order, max_per_order = _difficulty_item_bounds(diff)

        observed_orders = len(self.observed)
        unseen_orders = max(0, self.total_orders - observed_orders)
        observed_items = sum(len(items) for items in self.observed.values())

        min_total_items = observed_items + unseen_orders * min_per_order
        max_total_items = observed_items + unseen_orders * max_per_order
        lower_score = min_total_items + self.total_orders * ORDER_BONUS
        upper_score = max_total_items + self.total_orders * ORDER_BONUS
        exact = observed_orders >= self.total_orders

        return MaxScoreInfo(
            difficulty=diff,
            total_orders=self.total_orders,
            observed_orders=observed_orders,
            unseen_orders=unseen_orders,
            observed_items=observed_items,
            min_items_per_unseen_order=min_per_order,
            max_items_per_unseen_order=max_per_order,
            min_total_items=min_total_items,
            max_total_items=max_total_items,
            lower_bound_score=lower_score,
            upper_bound_score=upper_score,
            exact=exact,
            total_items_needed=max_total_items if exact else None,
            max_score=upper_score if exact else None,
        )

    def summary(self) -> dict:
        info = self.as_info()
        return {
            "difficulty": info.difficulty,
            "total_orders": info.total_orders,
            "observed_orders": info.observed_orders,
            "completed_orders": len(self.completed_ids),
            "observed_items": info.observed_items,
            "lower_bound_score": info.lower_bound_score,
            "upper_bound_score": info.upper_bound_score,
            "exact": info.exact,
            "max_score": info.max_score,
            "unseen_orders": info.unseen_orders,
        }
