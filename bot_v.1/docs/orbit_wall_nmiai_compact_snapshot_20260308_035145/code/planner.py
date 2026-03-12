"""Optimised single-bot planner with lookahead, trip planning, and auto-delivery.

Drop-in replacement for ``DecisionEngine`` — exposes the same
``decide(state) -> RoundActions`` interface.
"""
from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Optional

from .collision import action_for_move
from .grid import Grid
from .max_score import OrderTracker
from .models import (
    BotAction,
    BotActionCommand,
    BotInfo,
    GameState,
    ItemInfo,
    Pos,
    RoundActions,
)
from .orders import (
    compute_needed_items,
    compute_preview_items,
    get_active_order,
    get_preview_order,
    items_matching_active,
)
from .pathfinding import bfs_distance, bfs_shortest_path, find_all_pickup_positions


# ── Configuration ──────────────────────────────────────────────────────────

@dataclass
class PlannerConfig:
    """All tunable knobs for the optimised planner."""

    # How many future orders to consider when scoring items.
    lookahead_orders: int = 2

    # Utility weights for item selection.
    active_weight: float = 10.0
    preview_weight: float = 3.0
    auto_delivery_bonus: float = 5.0

    # Factor for how much return-to-dropoff distance affects item scoring.
    # 0 = old behaviour (only pickup distance), 1.0 = full round-trip cost.
    return_cost_factor: float = 0.7

    # Prefetch: pick preview-order items when no active items to pick.
    prefetch: bool = True

    # Delivery policy.
    deliver_on_full: bool = True       # deliver when inventory capacity reached
    deliver_to_complete: bool = True   # deliver when delivery completes active order

    # Time-pressure: skip preview-fill when this many rounds remain.
    time_pressure_threshold: int = 15

    # Controlled randomness for tie-breaking.
    tiebreak_seed: int = 0             # 0 = deterministic (no shuffle)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlannerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Planner ────────────────────────────────────────────────────────────────

class OptimizedEngine:
    """Stateful per-round planner optimised for single-bot Easy.

    Key improvements over ``DecisionEngine``:
    1. **Smarter delivery timing** — only delivers when it completes the
       active order or when inventory is full.  Avoids wasted partial trips.
    2. **Utility-based item scoring** — items scored by
       ``utility / travel_cost`` with deterministic tie-breaking.
    3. **Auto-delivery awareness** — when the current delivery will complete
       an order, remaining inventory slots are filled with preview-order
       items that get auto-delivered on order transition.
    4. **Prefetch** — picks preview items when nothing for active order
       remains on the map.
    """

    def __init__(
        self,
        config: Optional[PlannerConfig] = None,
        *,
        debug: bool = False,
        verbose: bool = False,
    ):
        self.config = config or PlannerConfig()
        self.debug = debug
        self.verbose = verbose
        self.last_decision_ms: float = 0.0

        # Internal state
        self._order_tracker = OrderTracker()
        self._rng: Optional[random.Random] = None
        if self.config.tiebreak_seed:
            self._rng = random.Random(self.config.tiebreak_seed)

    # ── Public API ─────────────────────────────────────────────────────

    def decide(self, state: GameState) -> RoundActions:
        t0 = time.perf_counter()

        # Track orders
        self._order_tracker.update(state)

        if len(state.bots) == 0:
            self.last_decision_ms = (time.perf_counter() - t0) * 1000
            return RoundActions(actions=[])

        bot = state.bots[0]
        bpos = bot.pos.as_tuple()
        drop_off = (state.drop_off[0], state.drop_off[1])
        grid = Grid(state.grid)
        item_blocked = frozenset(
            (it.position[0], it.position[1]) for it in state.items
        )

        action = self._plan_action(bot, state, grid, drop_off, item_blocked)

        self.last_decision_ms = (time.perf_counter() - t0) * 1000

        if self.debug:
            print(
                f"  R{state.round:3d} score={state.score:3d} "
                f"dt={self.last_decision_ms:.1f}ms "
                f"actions=[{action.action.value}]"
            )

        return RoundActions(actions=[action])

    # ── Core decision logic ────────────────────────────────────────────

    def _plan_action(
        self,
        bot: BotInfo,
        state: GameState,
        grid: Grid,
        drop_off: tuple[int, int],
        item_blocked: frozenset[tuple[int, int]],
    ) -> BotActionCommand:
        bpos = bot.pos.as_tuple()

        active_needs = compute_needed_items(state)       # types still needed, minus inventory
        preview_needs = compute_preview_items(state)     # upcoming order items

        # ── 1. OPPORTUNISTIC DROP-OFF ──────────────────────────────
        if bpos == drop_off and bot.inventory:
            matching = items_matching_active(bot, state)
            if matching:
                return BotActionCommand(bot=bot.id, action=BotAction.DROP_OFF)

        # ── 2. OPPORTUNISTIC PICKUP ────────────────────────────────
        if len(bot.inventory) < 3:
            adj = self._adjacent_useful_item(bot, state, active_needs, preview_needs)
            if adj is not None:
                return BotActionCommand(
                    bot=bot.id, action=BotAction.PICK_UP, item_id=adj.id,
                )

        # ── 3. SHOULD WE DELIVER? ─────────────────────────────────
        if self._should_deliver(bot, state, active_needs, item_blocked):
            return self._move_toward(bot.id, bpos, drop_off, grid, state, item_blocked)

        # ── 4. PICK NEXT ITEM ─────────────────────────────────────
        if len(bot.inventory) < 3:
            target_item, pickup_pos = self._best_item_to_pick(
                bot, state, grid, item_blocked, active_needs, preview_needs,
            )
            if target_item is not None and pickup_pos is not None:
                ipos = target_item.pos.as_tuple()
                # Already adjacent → pick up immediately
                if abs(bpos[0] - ipos[0]) + abs(bpos[1] - ipos[1]) == 1:
                    return BotActionCommand(
                        bot=bot.id, action=BotAction.PICK_UP, item_id=target_item.id,
                    )
                return self._move_toward(bot.id, bpos, pickup_pos, grid, state, item_blocked)

        # ── 5. FALLBACK — deliver or wait ─────────────────────────
        if bot.inventory:
            return self._move_toward(bot.id, bpos, drop_off, grid, state, item_blocked)
        return BotActionCommand(bot=bot.id, action=BotAction.WAIT)

    # ── Helpers ────────────────────────────────────────────────────────

    def _adjacent_useful_item(
        self,
        bot: BotInfo,
        state: GameState,
        active_needs: list[str],
        preview_needs: list[str],
    ) -> Optional[ItemInfo]:
        """Return an adjacent item worth picking, or None.

        Only considers preview items when *active_needs* is empty.
        """
        bpos = bot.pos.as_tuple()
        rounds_left = state.max_rounds - state.round
        time_pressure = rounds_left <= self.config.time_pressure_threshold
        active_adj: list[ItemInfo] = []
        preview_adj: list[ItemInfo] = []
        for item in state.items:
            ipos = item.pos.as_tuple()
            if abs(bpos[0] - ipos[0]) + abs(bpos[1] - ipos[1]) == 1:
                if item.type in active_needs:
                    active_adj.append(item)
                elif (
                    not active_needs
                    and not time_pressure
                    and self.config.prefetch
                    and item.type in preview_needs
                ):
                    preview_adj.append(item)
        # Prefer active; preview only when nothing active remains
        for lst in (active_adj, preview_adj):
            if lst:
                lst.sort(key=lambda it: it.id)
                return lst[0]
        return None

    def _should_deliver(
        self,
        bot: BotInfo,
        state: GameState,
        active_needs: list[str],
        item_blocked: frozenset[tuple[int, int]],
    ) -> bool:
        """Decide whether the bot should head to the drop-off now."""
        if not bot.inventory:
            return False

        matching = items_matching_active(bot, state)
        if not matching:
            return False

        # Inventory full with matching items → deliver
        if self.config.deliver_on_full and len(bot.inventory) >= 3:
            return True

        # Delivery would complete the active order
        if self.config.deliver_to_complete and len(active_needs) == 0:
            free_slots = 3 - len(bot.inventory)
            rounds_left = state.max_rounds - state.round
            time_pressure = rounds_left <= self.config.time_pressure_threshold
            # If free slots, preview items nearby, and no time pressure → delay
            if free_slots > 0 and self.config.prefetch and not time_pressure:
                preview_needs = compute_preview_items(state)
                if preview_needs:
                    bpos = bot.pos.as_tuple()
                    drop_off = (state.drop_off[0], state.drop_off[1])
                    drop_dist = abs(bpos[0] - drop_off[0]) + abs(bpos[1] - drop_off[1])
                    preview_set = set(preview_needs)
                    for item in state.items:
                        if item.type in preview_set:
                            ipos = item.pos.as_tuple()
                            # Compute detour cost: go to item then to drop-off
                            item_dist = abs(bpos[0] - ipos[0]) + abs(bpos[1] - ipos[1])
                            via_item = item_dist + abs(ipos[0] - drop_off[0]) + abs(ipos[1] - drop_off[1])
                            detour = via_item - drop_dist
                            if detour <= 6:  # max 6 extra steps
                                return False  # pick preview first
            return True

        # No more active-need items available on the map → deliver what we have
        active_set = set(active_needs)
        if active_set and not any(it.type in active_set for it in state.items):
            return True

        return False

    def _best_item_to_pick(
        self,
        bot: BotInfo,
        state: GameState,
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
        active_needs: list[str],
        preview_needs: list[str],
    ) -> tuple[Optional[ItemInfo], Optional[tuple[int, int]]]:
        """Score every reachable item and return the best (item, pickup_pos)."""
        bpos = bot.pos.as_tuple()
        cfg = self.config

        # Determine if we can complete the active order this trip
        free_slots = 3 - len(bot.inventory)
        can_complete_this_trip = len(active_needs) <= free_slots

        active_counter = Counter(active_needs)
        preview_counter = Counter(preview_needs)
        rounds_left = state.max_rounds - state.round
        time_pressure = rounds_left <= cfg.time_pressure_threshold

        blocked_set = set(item_blocked)
        scored: list[tuple[float, int, str, int, int, ItemInfo]] = []
        drop_off_tuple = (state.drop_off[0], state.drop_off[1])

        for item in state.items:
            utility = 0.0

            if item.type in active_counter and active_counter[item.type] > 0:
                utility = cfg.active_weight
            elif not active_needs and not time_pressure and item.type in preview_counter and preview_counter[item.type] > 0:
                # Only consider preview items when ALL active needs are met.
                # This ensures the bot finishes the active order first.
                if not cfg.prefetch:
                    continue
                utility = cfg.preview_weight
                # Auto-delivery bonus: active order will complete on delivery,
                # so preview items get auto-delivered for free.
                if can_complete_this_trip:
                    utility += cfg.auto_delivery_bonus
            else:
                continue  # not needed

            for pp in find_all_pickup_positions(grid, item.pos.as_tuple()):
                dist = bfs_distance(grid, bpos, pp, blocked=blocked_set)
                if dist >= 999999:
                    continue
                # Account for return trip: items far from drop-off cost more
                return_manhattan = abs(pp[0] - drop_off_tuple[0]) + abs(pp[1] - drop_off_tuple[1])
                total_cost = dist + cfg.return_cost_factor * return_manhattan
                score = utility / max(total_cost, 1)
                scored.append((score, dist, item.type, pp[0], pp[1], item))

        if not scored:
            return None, None

        # Deterministic sort: best score first → shortest dist → item type → position
        scored.sort(key=lambda s: (-s[0], s[1], s[2], s[3], s[4]))

        # Optional controlled tie-breaking among top candidates
        if self._rng and len(scored) > 1:
            top_score = scored[0][0]
            top_dist = scored[0][1]
            ties = [s for s in scored if s[0] == top_score and s[1] == top_dist]
            if len(ties) > 1:
                choice = self._rng.choice(ties)
                return choice[5], (choice[3], choice[4])

        best = scored[0]
        return best[5], (best[3], best[4])

    # ── Movement (reuses pathfinding module) ───────────────────────────

    def _move_toward(
        self,
        bot_id: int,
        start: tuple[int, int],
        goal: tuple[int, int],
        grid: Grid,
        state: GameState,
        item_blocked: frozenset[tuple[int, int]],
    ) -> BotActionCommand:
        if start == goal:
            return BotActionCommand(bot=bot_id, action=BotAction.WAIT)

        blocked: set[tuple[int, int]] = set(item_blocked)
        for b in state.bots:
            if b.id != bot_id:
                blocked.add(b.pos.as_tuple())
        blocked.discard(goal)

        path = bfs_shortest_path(grid, start, goal, blocked)
        if path is None or len(path) < 2:
            path = bfs_shortest_path(grid, start, goal, set(item_blocked) - {goal})
            if path is None or len(path) < 2:
                return self._simple_move(bot_id, start, goal, grid, item_blocked)

        return BotActionCommand(
            bot=bot_id,
            action=action_for_move(start, path[1]),
        )

    @staticmethod
    def _simple_move(
        bot_id: int,
        start: tuple[int, int],
        goal: tuple[int, int],
        grid: Grid,
        item_blocked: frozenset[tuple[int, int]],
    ) -> BotActionCommand:
        sx, sy = start
        gx, gy = goal
        dx, dy = gx - sx, gy - sy

        candidates = []
        if abs(dx) >= abs(dy):
            if dx > 0: candidates.append((sx + 1, sy, BotAction.MOVE_RIGHT))
            elif dx < 0: candidates.append((sx - 1, sy, BotAction.MOVE_LEFT))
            if dy > 0: candidates.append((sx, sy + 1, BotAction.MOVE_DOWN))
            elif dy < 0: candidates.append((sx, sy - 1, BotAction.MOVE_UP))
        else:
            if dy > 0: candidates.append((sx, sy + 1, BotAction.MOVE_DOWN))
            elif dy < 0: candidates.append((sx, sy - 1, BotAction.MOVE_UP))
            if dx > 0: candidates.append((sx + 1, sy, BotAction.MOVE_RIGHT))
            elif dx < 0: candidates.append((sx - 1, sy, BotAction.MOVE_LEFT))

        for nx, ny, action in candidates:
            if grid.is_walkable(nx, ny) and (nx, ny) not in item_blocked:
                return BotActionCommand(bot=bot_id, action=action)

        return BotActionCommand(bot=bot_id, action=BotAction.WAIT)
