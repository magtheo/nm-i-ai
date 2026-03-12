"""Protocol-faithful local simulator used by the Automated Forge evolution loop."""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any, Callable

from .core import ActionValidationError, build_actions_payload, load_strategy_callable
from .protocol import ALLOWED_ACTIONS, DIFFICULTIES, MOVE_DELTAS, BotState, DifficultySpec, ItemState, OrderState


DEFAULT_DIFFICULTY_SEEDS: dict[str, int] = {
    "easy": 7001,
    "medium": 7002,
    "hard": 7003,
    "expert": 7004,
}


class SimulationError(RuntimeError):
    """Base class for simulator failures."""


class InvalidActionError(SimulationError):
    """Raised when an action outside protocol constraints appears."""


@dataclass
class SimulationSummary:
    difficulty: str
    seed: int
    score: int
    items_delivered: int
    orders_completed: int
    rounds_played: int
    blocked_moves: int
    collision_blocks: int
    idle_steps_by_bot: dict[int, int]
    invalid_actions: int
    error: str | None
    round_logs: list[dict[str, Any]]

    def bot_idle_ratio(self) -> dict[int, float]:
        if self.rounds_played <= 0:
            return {bot_id: 0.0 for bot_id in self.idle_steps_by_bot}
        return {
            bot_id: idle / float(self.rounds_played)
            for bot_id, idle in sorted(self.idle_steps_by_bot.items())
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty": self.difficulty,
            "seed": self.seed,
            "score": self.score,
            "items_delivered": self.items_delivered,
            "orders_completed": self.orders_completed,
            "rounds_played": self.rounds_played,
            "blocked_moves": self.blocked_moves,
            "collision_blocks": self.collision_blocks,
            "idle_steps_by_bot": {str(k): v for k, v in self.idle_steps_by_bot.items()},
            "idle_ratio_by_bot": {str(k): round(v, 6) for k, v in self.bot_idle_ratio().items()},
            "invalid_actions": self.invalid_actions,
            "error": self.error,
            "round_logs": self.round_logs,
        }


@dataclass
class BatchEvaluation:
    runs: list[SimulationSummary]

    def average_score(self) -> float:
        if not self.runs:
            return 0.0
        return statistics.fmean(run.score for run in self.runs)

    def has_errors(self) -> bool:
        return any(run.error for run in self.runs)

    def worst_error(self) -> str | None:
        for run in self.runs:
            if run.error:
                return run.error
        return None

    def aggregate_idle_ratio(self) -> dict[int, float]:
        totals: dict[int, float] = {}
        counts: dict[int, int] = {}
        for run in self.runs:
            ratios = run.bot_idle_ratio()
            for bot_id, ratio in ratios.items():
                totals[bot_id] = totals.get(bot_id, 0.0) + ratio
                counts[bot_id] = counts.get(bot_id, 0) + 1
        return {
            bot_id: (totals[bot_id] / counts[bot_id]) if counts[bot_id] > 0 else 0.0
            for bot_id in sorted(totals)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "average_score": self.average_score(),
            "has_errors": self.has_errors(),
            "worst_error": self.worst_error(),
            "aggregate_idle_ratio": {
                str(k): round(v, 6)
                for k, v in self.aggregate_idle_ratio().items()
            },
            "runs": [run.to_dict() for run in self.runs],
        }


class GrocerySimulator:
    """Local deterministic simulator aligned with protocol section 7 action semantics."""

    def __init__(
        self,
        *,
        difficulty: str,
        seed: int,
        max_rounds: int = 300,
        total_orders: int = 50,
        strict_actions: bool = True,
    ) -> None:
        key = str(difficulty).strip().lower()
        if key not in DIFFICULTIES:
            raise ValueError(f"Unknown difficulty: {difficulty}")

        self.spec = DIFFICULTIES[key]
        self.difficulty = key
        self.seed = int(seed)
        self.max_rounds = int(max_rounds)
        self.total_orders = int(total_orders)
        self.strict_actions = bool(strict_actions)
        self.rng = Random((hash(self.difficulty) & 0xFFFF) ^ self.seed)

        self.walls = self._build_walls()
        self.drop_off = (1, self.spec.height - 2)
        self.bots = self._build_bots()
        self.items = self._build_items()
        self.item_by_id = {item.id: item for item in self.items}
        self.orders = self._build_orders()

        self.active_order_index = 0
        self.score = 0
        self.items_delivered = 0
        self.orders_completed = 0
        self.blocked_moves = 0
        self.collision_blocks = 0
        self.invalid_actions = 0
        self.idle_steps_by_bot: dict[int, int] = {bot.id: 0 for bot in self.bots}

    def _build_walls(self) -> set[tuple[int, int]]:
        spec = self.spec
        walls: set[tuple[int, int]] = set()

        for x in range(spec.width):
            walls.add((x, 0))
            walls.add((x, spec.height - 1))
        for y in range(spec.height):
            walls.add((0, y))
            walls.add((spec.width - 1, y))

        shelf_cols = _evenly_spaced_columns(
            count=spec.aisle_count * 2,
            start=2,
            end=spec.width - 3,
        )

        top_start = 2
        top_end = max(top_start, spec.height // 2 - 1)
        bottom_start = min(spec.height - 4, spec.height // 2 + 1)
        bottom_end = spec.height - 4

        shelf_rows = set(range(top_start, top_end + 1))
        if bottom_start <= bottom_end:
            shelf_rows.update(range(bottom_start, bottom_end + 1))

        for x in shelf_cols:
            for y in shelf_rows:
                walls.add((x, y))

        return walls

    def _build_bots(self) -> list[BotState]:
        candidates: list[tuple[int, int]] = []
        for y in range(self.spec.height - 2, 0, -1):
            for x in range(self.spec.width - 2, 0, -1):
                pos = (x, y)
                if pos in self.walls or pos == self.drop_off:
                    continue
                candidates.append(pos)

        if len(candidates) < self.spec.bot_count:
            raise RuntimeError("Not enough walkable spawn cells for bot count")

        out: list[BotState] = []
        for bot_id in range(self.spec.bot_count):
            out.append(BotState(id=bot_id, position=candidates[bot_id], inventory=[]))
        return out

    def _build_items(self) -> list[ItemState]:
        shelf_cells = sorted(
            cell
            for cell in self.walls
            if 0 < cell[0] < self.spec.width - 1 and 0 < cell[1] < self.spec.height - 1
        )
        items: list[ItemState] = []
        for idx, (x, y) in enumerate(shelf_cells):
            item_type = self.rng.choice(self.spec.item_types)
            items.append(ItemState(id=f"item_{idx}", type=item_type, position=(x, y)))
        return items

    def _build_orders(self) -> list[OrderState]:
        orders: list[OrderState] = []
        for idx in range(self.total_orders):
            size = self.rng.randint(self.spec.order_size_min, self.spec.order_size_max)
            required = [self.rng.choice(self.spec.item_types) for _ in range(size)]
            orders.append(OrderState(id=f"order_{idx}", items_required=required))
        return orders

    def _is_walkable(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        if x < 0 or y < 0 or x >= self.spec.width or y >= self.spec.height:
            return False
        return cell not in self.walls

    def _active_order(self) -> OrderState | None:
        if 0 <= self.active_order_index < len(self.orders):
            return self.orders[self.active_order_index]
        return None

    def _preview_order(self) -> OrderState | None:
        preview_idx = self.active_order_index + 1
        if 0 <= preview_idx < len(self.orders):
            return self.orders[preview_idx]
        return None

    def _build_state_payload(self, round_idx: int) -> dict[str, Any]:
        orders_payload: list[dict[str, Any]] = []
        active = self._active_order()
        if active is not None:
            orders_payload.append(active.to_payload(status="active"))
        preview = self._preview_order()
        if preview is not None:
            orders_payload.append(preview.to_payload(status="preview"))

        return {
            "type": "game_state",
            "round": round_idx,
            "max_rounds": self.max_rounds,
            "grid": {
                "width": self.spec.width,
                "height": self.spec.height,
                "walls": [[x, y] for x, y in sorted(self.walls)],
            },
            "bots": [bot.to_payload() for bot in sorted(self.bots, key=lambda b: b.id)],
            "items": [item.to_payload() for item in sorted(self.items, key=lambda row: row.id)],
            "orders": orders_payload,
            "drop_off": [self.drop_off[0], self.drop_off[1]],
            "score": self.score,
            "active_order_index": self.active_order_index,
            "total_orders": self.total_orders,
        }

    def _validate_actions(self, actions: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        by_bot: dict[int, dict[str, Any]] = {}
        known_bots = {bot.id for bot in self.bots}

        for raw in actions:
            if not isinstance(raw, dict):
                self.invalid_actions += 1
                raise InvalidActionError("Action payload must be dict")
            if "bot" not in raw:
                self.invalid_actions += 1
                raise InvalidActionError("Action payload missing bot field")

            try:
                bot_id = int(raw["bot"])
            except (TypeError, ValueError):
                self.invalid_actions += 1
                raise InvalidActionError(f"Invalid bot id: {raw.get('bot')!r}")

            if bot_id not in known_bots:
                self.invalid_actions += 1
                raise InvalidActionError(f"Unknown bot id in action: {bot_id}")

            action = str(raw.get("action", "")).strip().lower()
            if action not in ALLOWED_ACTIONS:
                self.invalid_actions += 1
                raise InvalidActionError(
                    "Invalid action generated. Allowed actions: "
                    + ", ".join(ALLOWED_ACTIONS)
                )

            if action == "pick_up" and not raw.get("item_id"):
                self.invalid_actions += 1
                raise InvalidActionError("pick_up action requires item_id")

            by_bot[bot_id] = {"bot": bot_id, "action": action, "item_id": raw.get("item_id")}

        return by_bot

    def _deliver_bot_inventory_to_active_order(self, bot: BotState) -> bool:
        order = self._active_order()
        if order is None:
            return False

        need = order.outstanding_counts()
        if not need:
            order.complete = True
            return True

        delivered_now = 0
        kept: list[str] = []
        for item_type in bot.inventory:
            remaining = need.get(item_type, 0)
            if remaining > 0:
                need[item_type] = remaining - 1
                order.items_delivered.append(item_type)
                delivered_now += 1
            else:
                kept.append(item_type)

        if delivered_now > 0:
            bot.inventory = kept
            self.items_delivered += delivered_now
            self.score += delivered_now

        if len(order.items_delivered) >= len(order.items_required):
            order.complete = True
            self.orders_completed += 1
            self.score += 5
            return True
        return False

    def _advance_completed_orders_with_auto_delivery(self) -> None:
        while True:
            current = self._active_order()
            if current is None or not current.complete:
                break
            self.active_order_index += 1
            new_active = self._active_order()
            if new_active is None:
                break
            for bot in self.bots:
                self._deliver_bot_inventory_to_active_order(bot)

    def _apply_round_actions(self, actions: list[dict[str, Any]], round_idx: int) -> dict[str, Any]:
        by_bot = self._validate_actions(actions)

        occupancy: set[tuple[int, int]] = {bot.position for bot in self.bots}
        round_blocked = 0
        round_collisions = 0

        for bot in sorted(self.bots, key=lambda b: b.id):
            payload = by_bot.get(bot.id, {"bot": bot.id, "action": "wait"})
            action = str(payload["action"])

            if action == "wait":
                self.idle_steps_by_bot[bot.id] += 1
                continue

            if action in MOVE_DELTAS:
                dx, dy = MOVE_DELTAS[action]
                target = (bot.position[0] + dx, bot.position[1] + dy)
                if not self._is_walkable(target):
                    round_blocked += 1
                    self.idle_steps_by_bot[bot.id] += 1
                    continue
                if target in occupancy:
                    round_blocked += 1
                    round_collisions += 1
                    self.idle_steps_by_bot[bot.id] += 1
                    continue
                occupancy.remove(bot.position)
                bot.position = target
                occupancy.add(bot.position)
                continue

            if action == "pick_up":
                item_id = str(payload.get("item_id"))
                item = self.item_by_id.get(item_id)
                if item is None:
                    self.idle_steps_by_bot[bot.id] += 1
                    continue
                if len(bot.inventory) >= 3:
                    self.idle_steps_by_bot[bot.id] += 1
                    continue
                if abs(bot.position[0] - item.position[0]) + abs(bot.position[1] - item.position[1]) != 1:
                    self.idle_steps_by_bot[bot.id] += 1
                    continue
                bot.inventory.append(item.type)
                continue

            if action == "drop_off":
                if bot.position != self.drop_off or not bot.inventory:
                    self.idle_steps_by_bot[bot.id] += 1
                    continue
                completed = self._deliver_bot_inventory_to_active_order(bot)
                if completed:
                    self._advance_completed_orders_with_auto_delivery()
                continue

        self.blocked_moves += round_blocked
        self.collision_blocks += round_collisions

        return {
            "round": round_idx,
            "score": self.score,
            "active_order_index": self.active_order_index,
            "blocked_moves": round_blocked,
            "collision_blocks": round_collisions,
        }

    def run(self, strategy_fn: Callable[[dict[str, Any]], list[dict[str, Any]]]) -> SimulationSummary:
        round_logs: list[dict[str, Any]] = []

        for round_idx in range(self.max_rounds):
            game_state = self._build_state_payload(round_idx)

            try:
                payload = build_actions_payload(
                    game_state=game_state,
                    strategy_fn=strategy_fn,
                    strict=self.strict_actions,
                )
                actions = payload.get("actions", [])
            except (ActionValidationError, InvalidActionError, Exception) as exc:  # noqa: BLE001
                error_text = str(exc)
                return SimulationSummary(
                    difficulty=self.difficulty,
                    seed=self.seed,
                    score=self.score,
                    items_delivered=self.items_delivered,
                    orders_completed=self.orders_completed,
                    rounds_played=round_idx,
                    blocked_moves=self.blocked_moves,
                    collision_blocks=self.collision_blocks,
                    idle_steps_by_bot=dict(self.idle_steps_by_bot),
                    invalid_actions=self.invalid_actions,
                    error=error_text,
                    round_logs=round_logs,
                )

            try:
                round_log = self._apply_round_actions(actions, round_idx)
            except InvalidActionError as exc:
                return SimulationSummary(
                    difficulty=self.difficulty,
                    seed=self.seed,
                    score=self.score,
                    items_delivered=self.items_delivered,
                    orders_completed=self.orders_completed,
                    rounds_played=round_idx,
                    blocked_moves=self.blocked_moves,
                    collision_blocks=self.collision_blocks,
                    idle_steps_by_bot=dict(self.idle_steps_by_bot),
                    invalid_actions=self.invalid_actions,
                    error=str(exc),
                    round_logs=round_logs,
                )

            round_logs.append(round_log)

        return SimulationSummary(
            difficulty=self.difficulty,
            seed=self.seed,
            score=self.score,
            items_delivered=self.items_delivered,
            orders_completed=self.orders_completed,
            rounds_played=self.max_rounds,
            blocked_moves=self.blocked_moves,
            collision_blocks=self.collision_blocks,
            idle_steps_by_bot=dict(self.idle_steps_by_bot),
            invalid_actions=self.invalid_actions,
            error=None,
            round_logs=round_logs,
        )


def evaluate_strategy_file(
    *,
    strategy_file: str | Path,
    difficulties: list[str] | None = None,
    difficulty_seeds: dict[str, int] | None = None,
    max_rounds: int = 300,
    strict_actions: bool = True,
) -> BatchEvaluation:
    strategy_fn = load_strategy_callable(strategy_file)

    selected = difficulties or ["easy", "medium", "hard", "expert"]
    seeds = dict(DEFAULT_DIFFICULTY_SEEDS)
    if difficulty_seeds:
        for key, value in difficulty_seeds.items():
            seeds[str(key).strip().lower()] = int(value)

    runs: list[SimulationSummary] = []
    for difficulty in selected:
        key = str(difficulty).strip().lower()
        simulator = GrocerySimulator(
            difficulty=key,
            seed=seeds.get(key, 0),
            max_rounds=max_rounds,
            strict_actions=strict_actions,
        )
        runs.append(simulator.run(strategy_fn))
    return BatchEvaluation(runs=runs)


def _parse_seed_overrides(raw: str) -> dict[str, int]:
    if not raw.strip():
        return {}
    out: dict[str, int] = {}
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError("Seed override must use difficulty=seed format")
        key, value = token.split("=", 1)
        out[key.strip().lower()] = int(value.strip())
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local Forge simulator")
    parser.add_argument("--strategy-file", type=str, default="forge/strategy.py")
    parser.add_argument(
        "--difficulties",
        type=str,
        default="easy,medium,hard,expert",
        help="Comma-separated list",
    )
    parser.add_argument(
        "--seed-overrides",
        type=str,
        default="",
        help="Format: easy=7001,medium=7002",
    )
    parser.add_argument("--max-rounds", type=int, default=300)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--non-strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    difficulties = [part.strip().lower() for part in args.difficulties.split(",") if part.strip()]
    seed_overrides = _parse_seed_overrides(args.seed_overrides)

    result = evaluate_strategy_file(
        strategy_file=args.strategy_file,
        difficulties=difficulties,
        difficulty_seeds=seed_overrides,
        max_rounds=int(args.max_rounds),
        strict_actions=not bool(args.non_strict),
    )

    payload = result.to_dict()
    text = json.dumps(payload, indent=2, ensure_ascii=True)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"[forge-sim] wrote {out_path}")
    else:
        print(text)


def _evenly_spaced_columns(*, count: int, start: int, end: int) -> list[int]:
    if count <= 0:
        return []
    if start > end:
        return []
    if count == 1:
        return [int((start + end) / 2)]

    span = end - start
    if span <= 0:
        return [start]

    raw = [int(round(start + (span * idx) / float(count - 1))) for idx in range(count)]
    out: list[int] = []
    seen: set[int] = set()
    for col in raw:
        if col < start:
            col = start
        if col > end:
            col = end
        if col in seen:
            continue
        seen.add(col)
        out.append(col)

    probe = start
    while len(out) < count and probe <= end:
        if probe not in seen:
            seen.add(probe)
            out.append(probe)
        probe += 1

    out.sort()
    return out


if __name__ == "__main__":
    main()
