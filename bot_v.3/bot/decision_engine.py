"""Clean, layered decision engine — bot_v.3.

Architecture
============
Every call to ``decide()`` flows through four explicit layers:

  1. **Analyze**  — read GameState, compute active/preview demand, track stalls
  2. **Assign**   — greedy score-based task assignment (pick / deliver / idle)
  3. **Route**    — compute one-step desired moves via BFS
  4. **Render**   — collision resolution + protocol action commands

Design goals
============
- Small, focused config: ``EngineConfig`` has ~12 parameters (not 200+)
- One clear responsibility per layer; no tangled side-effects
- Only proven tactics included: active-first priority, delivery concurrency cap,
  optional preview pre-picks, simple stall detection
- Easily extensible: add a new layer or swap assignment strategy without touching others
"""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .collision import action_for_move, resolve_collisions_with_stats
from .grid import Grid
from .models import BotAction, BotActionCommand, BotInfo, GameState, ItemInfo, RoundActions
from .orders import (
    COMMIT_MODE_OPTIMISTIC,
    compute_needed_items,
    compute_preview_items,
    get_active_order,
    items_matching_active,
    should_prefetch_preview,
)
from .pathfinding import bfs_shortest_path, find_all_pickup_positions


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EngineConfig:
    """All tunable parameters in one place — intentionally small.

    Only parameters that have a *proven* impact on score are included here.
    Experimental mechanics belong in a dedicated branch, not this config.
    """

    # ── Demand scoring ─────────────────────────────────────────────────────
    active_item_weight: float = 10.0
    """Reward bonus for picking an item needed by the active order."""

    preview_item_weight: float = 3.0
    """Reward bonus for picking an item needed by the preview order."""

    dist_weight: float = 1.0
    """Penalty per BFS step of travel distance."""

    # ── Delivery ───────────────────────────────────────────────────────────
    max_deliverers: int = 3
    """Maximum simultaneous delivery bots. Prevents drop-off congestion."""

    # ── Preview pre-picking ────────────────────────────────────────────────
    enable_preview_picks: bool = True
    """Allow bots to pre-pick items for the upcoming preview order."""

    preview_safety_slots: int = 1
    """Extra free inventory slots required before preview pre-pick is allowed.
    Higher = more conservative; lower = more aggressive pre-picking."""

    # ── Stall / anti-starvation ────────────────────────────────────────────
    starvation_rounds: int = 5
    """Rounds a bot must stay in the same cell before forced reassignment."""

    # ── Timing ─────────────────────────────────────────────────────────────
    decision_timeout_ms: float = 50.0
    """Soft cap on decision time (ms). Logged for diagnostics; not enforced."""

    # ── Factory helpers ────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, d: dict) -> "EngineConfig":
        """Build from a plain dict, ignoring unknown keys."""
        valid = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_json(cls, path: str | Path) -> "EngineConfig":
        """Load config from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


# ─────────────────────────────────────────────────────────────────────────────
# Task representation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BotTask:
    """High-level task assigned to one bot for the current round."""

    type: str
    """One of: ``"pick_active"``, ``"pick_preview"``, ``"deliver"``, ``"idle"``."""

    item: Optional[ItemInfo] = None
    """Target item (pick tasks only)."""

    pickup_pos: Optional[tuple[int, int]] = None
    """Walkable cell adjacent to the item shelf (pick tasks only)."""

    target_pos: Optional[tuple[int, int]] = None
    """Destination cell (deliver tasks: drop-off position)."""


# ─────────────────────────────────────────────────────────────────────────────
# Decision Engine
# ─────────────────────────────────────────────────────────────────────────────

class DecisionEngine:
    """Clean, layered multi-bot decision engine.

    Usage::

        engine = DecisionEngine(EngineConfig.from_json("configs/default.json"))
        actions = engine.decide(state)  # called every round inside GameWSClient
    """

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config: EngineConfig = config or EngineConfig()

        # Stall tracking — bot_id → consecutive rounds without position change
        self._stall_counter: dict[int, int] = {}
        self._prev_pos: dict[int, tuple[int, int]] = {}

        # Diagnostics (written by decide(); read by GameWSClient / logger)
        self.last_decision_ms: float = 0.0
        self.last_round_telemetry: dict[str, float] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def decide(self, state: GameState) -> RoundActions:
        """Compute one round of actions for all bots.

        This is the only public method callers need.
        """
        t0 = time.perf_counter()
        grid = Grid(state.grid)

        # ── Layer 1: Analyze ─────────────────────────────────────────────
        active_need: list[str] = compute_needed_items(
            state, commitment_mode=COMMIT_MODE_OPTIMISTIC
        )
        can_preview: bool = (
            self.config.enable_preview_picks
            and should_prefetch_preview(
                state,
                commitment_mode=COMMIT_MODE_OPTIMISTIC,
                preview_safety_slots=self.config.preview_safety_slots,
            )
        )
        preview_need: list[str] = compute_preview_items(state) if can_preview else []
        self._update_stall_counters(state)

        # ── Layer 2: Assign ──────────────────────────────────────────────
        assignments: dict[int, BotTask] = self._assign_tasks(
            state, grid, list(active_need), list(preview_need)
        )

        # ── Layer 3: Route ───────────────────────────────────────────────
        plans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        for bot in state.bots:
            cur = bot.pos.as_tuple()
            desired = self._desired_pos(
                bot, grid, assignments.get(bot.id, BotTask("idle")), state
            )
            plans.append((bot.id, cur, desired))

        # ── Layer 4: Collision resolution + render ────────────────────────
        occupied: set[tuple[int, int]] = {b.pos.as_tuple() for b in state.bots}
        resolved, cstats = resolve_collisions_with_stats(plans, occupied)
        actions = self._render_actions(state, assignments, resolved)

        # Telemetry
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.last_decision_ms = elapsed_ms
        self.last_round_telemetry = {
            "decision_ms": round(elapsed_ms, 2),
            "collision_blocks": float(cstats.blocked_moves),
            "swaps_prevented": float(cstats.swaps_prevented),
            "active_need_count": float(len(active_need)),
            "preview_need_count": float(len(preview_need)),
        }

        return RoundActions(actions=actions)

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 1: Stall tracking
    # ─────────────────────────────────────────────────────────────────────────

    def _update_stall_counters(self, state: GameState) -> None:
        for bot in state.bots:
            cur = bot.pos.as_tuple()
            prev = self._prev_pos.get(bot.id)
            if prev == cur:
                self._stall_counter[bot.id] = self._stall_counter.get(bot.id, 0) + 1
            else:
                self._stall_counter[bot.id] = 0
            self._prev_pos[bot.id] = cur

    def _is_stalled(self, bot_id: int) -> bool:
        return self._stall_counter.get(bot_id, 0) >= self.config.starvation_rounds

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 2: Task assignment
    # ─────────────────────────────────────────────────────────────────────────

    def _assign_tasks(
        self,
        state: GameState,
        grid: Grid,
        active_need: list[str],
        preview_need: list[str],
    ) -> dict[int, BotTask]:
        """Greedy two-pass assignment.

        Pass 1 — assign deliver tasks to bots already carrying active-order items.
        Pass 2 — assign the best available pick task to remaining bots.

        The assignment is greedy (highest score first).  This is sufficient for
        strong performance; a Hungarian solver can be plugged in here if needed.
        """
        assignments: dict[int, BotTask] = {}
        drop_pos = state.drop_off_pos.as_tuple()
        reserved_item_ids: set[str] = set()

        # ── Pass 1: delivery ─────────────────────────────────────────────────
        # Count bots that have active-needed cargo so we can cap deliverers.
        delivery_candidates = [
            bot for bot in sorted(state.bots, key=lambda b: b.id)
            if items_matching_active(bot, state)
        ]
        # Sort so closest-to-drop-off bots deliver first (reduces congestion).
        delivery_candidates.sort(
            key=lambda b: abs(b.pos.x - drop_pos[0]) + abs(b.pos.y - drop_pos[1])
        )
        deliverers_assigned = 0
        for bot in delivery_candidates:
            if deliverers_assigned < self.config.max_deliverers or self._is_stalled(bot.id):
                assignments[bot.id] = BotTask(type="deliver", target_pos=drop_pos)
                deliverers_assigned += 1

        # ── Pass 2: pick tasks ───────────────────────────────────────────────
        for bot in sorted(state.bots, key=lambda b: b.id):
            if bot.id in assignments:
                continue
            if len(bot.inventory) >= 3:
                # Full inventory — go deliver regardless of what's in there.
                assignments[bot.id] = BotTask(type="deliver", target_pos=drop_pos)
                continue
            task = self._best_pick_task(
                bot, grid, state, active_need, preview_need, reserved_item_ids
            )
            assignments[bot.id] = task

        return assignments

    def _best_pick_task(
        self,
        bot: BotInfo,
        grid: Grid,
        state: GameState,
        active_need: list[str],
        preview_need: list[str],
        reserved: set[str],
    ) -> BotTask:
        """Return the highest-scoring available pick task for *bot*.

        Score = demand_weight - dist * dist_weight

        Active-order items always outscore preview-order items at the same
        distance because active_item_weight >> preview_item_weight.
        """
        cfg = self.config
        bot_pos = bot.pos.as_tuple()

        best_score: float = -1e18
        best_task: Optional[BotTask] = None

        for item in state.items:
            if item.id in reserved:
                continue

            if item.type in active_need:
                demand = cfg.active_item_weight
            elif item.type in preview_need:
                demand = cfg.preview_item_weight
            else:
                continue  # Not needed by any order we care about

            pickup_cells = find_all_pickup_positions(grid, item.pos.as_tuple())
            if not pickup_cells:
                continue

            # Use Manhattan distance to nearest pickup cell as a fast proxy.
            # BFS would be more accurate but is expensive at assignment time.
            nearest = min(
                pickup_cells,
                key=lambda p: abs(p[0] - bot_pos[0]) + abs(p[1] - bot_pos[1]),
            )
            dist = abs(nearest[0] - bot_pos[0]) + abs(nearest[1] - bot_pos[1])
            score = demand - dist * cfg.dist_weight

            if score > best_score:
                best_score = score
                best_task = BotTask(
                    type="pick_active" if item.type in active_need else "pick_preview",
                    item=item,
                    pickup_pos=nearest,
                )

        if best_task is not None and best_task.item is not None:
            reserved.add(best_task.item.id)
            # Deduct from demand so the next bot doesn't target the same slot.
            if best_task.type == "pick_active" and best_task.item.type in active_need:
                active_need.remove(best_task.item.type)
            elif best_task.type == "pick_preview" and best_task.item.type in preview_need:
                preview_need.remove(best_task.item.type)
            return best_task

        return BotTask(type="idle")

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 3: Path planning (desired next position)
    # ─────────────────────────────────────────────────────────────────────────

    def _desired_pos(
        self,
        bot: BotInfo,
        grid: Grid,
        task: BotTask,
        state: GameState,
    ) -> tuple[int, int]:
        """Return the desired grid cell for *bot* this round given its task.

        Returns the bot's current cell for idle/at-target cases so the
        collision resolver treats it as staying put.
        """
        cur = bot.pos.as_tuple()

        if task.type == "idle":
            return cur

        if task.type == "deliver":
            target = task.target_pos or state.drop_off_pos.as_tuple()
            if cur == target:
                return cur
            path = bfs_shortest_path(grid, cur, target)
            return path[1] if path and len(path) > 1 else cur

        # pick_active or pick_preview
        if task.item is None or task.pickup_pos is None:
            return cur

        pickup_pos = task.pickup_pos
        if cur == pickup_pos:
            return cur

        path = bfs_shortest_path(grid, cur, pickup_pos)
        return path[1] if path and len(path) > 1 else cur

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 4: Action rendering
    # ─────────────────────────────────────────────────────────────────────────

    def _render_actions(
        self,
        state: GameState,
        assignments: dict[int, BotTask],
        resolved: dict[int, tuple[int, int]],
    ) -> list[BotActionCommand]:
        """Convert resolved positions + tasks into protocol BotActionCommands.

        Priority order per bot:
          1. DROP_OFF  — bot is at drop-off and has active-matching cargo
          2. PICK_UP   — bot is adjacent to its target item
          3. MOVE / WAIT — follow the resolved movement position
        """
        commands: list[BotActionCommand] = []
        drop_pos = state.drop_off_pos.as_tuple()

        for bot in sorted(state.bots, key=lambda b: b.id):
            cur = bot.pos.as_tuple()
            task = assignments.get(bot.id, BotTask("idle"))
            resolved_pos = resolved.get(bot.id, cur)

            # ── 1. Drop off ────────────────────────────────────────────────
            if cur == drop_pos and task.type == "deliver":
                matching = items_matching_active(bot, state)
                if matching:
                    commands.append(
                        BotActionCommand(bot=bot.id, action=BotAction.DROP_OFF)
                    )
                    continue
                # Bot is at drop-off but nothing to drop — fall through to move.

            # ── 2. Pick up ─────────────────────────────────────────────────
            if task.type in ("pick_active", "pick_preview") and task.item is not None:
                item_pos = task.item.pos.as_tuple()
                adjacent = (
                    abs(cur[0] - item_pos[0]) + abs(cur[1] - item_pos[1]) == 1
                )
                if adjacent:
                    commands.append(
                        BotActionCommand(
                            bot=bot.id,
                            action=BotAction.PICK_UP,
                            item_id=task.item.id,
                        )
                    )
                    continue

            # ── 3. Move (or wait) ──────────────────────────────────────────
            commands.append(
                BotActionCommand(
                    bot=bot.id,
                    action=action_for_move(cur, resolved_pos),
                )
            )

        return commands
