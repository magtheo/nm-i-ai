"""Immutable bot core: strategy loading, intent translation, and WebSocket runtime."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

import websockets

from .pathfinding import astar_next_step
from .protocol import ALLOWED_ACTIONS, MOVE_DELTAS


class ActionValidationError(RuntimeError):
    """Raised when strategy output cannot be mapped to legal protocol actions."""


StrategyFn = Callable[[dict[str, Any]], list[dict[str, Any]]]


def load_strategy_callable(path: str | Path) -> StrategyFn:
    strategy_path = Path(path).resolve()
    if not strategy_path.exists():
        raise FileNotFoundError(f"Strategy file not found: {strategy_path}")

    spec = importlib.util.spec_from_file_location("forge_strategy_runtime", strategy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import strategy from {strategy_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fn = getattr(module, "decide_intents", None)
    if fn is None or not callable(fn):
        raise RuntimeError(
            f"Strategy file must define callable decide_intents(game_state). Missing in {strategy_path}"
        )
    return fn


def _action_from_step(start: tuple[int, int], step: tuple[int, int]) -> str:
    dx = step[0] - start[0]
    dy = step[1] - start[1]
    for action, (mx, my) in MOVE_DELTAS.items():
        if dx == mx and dy == my:
            return action
    return "wait"


def _normalize_intents(intents: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_bot: dict[int, dict[str, Any]] = {}
    for raw in intents:
        if not isinstance(raw, dict):
            continue
        if "bot" not in raw:
            continue
        try:
            bot_id = int(raw["bot"])
        except (TypeError, ValueError):
            continue
        by_bot[bot_id] = raw
    return by_bot


def intents_to_actions(
    *,
    game_state: dict[str, Any],
    intents: list[dict[str, Any]],
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Translate high-level intents to legal protocol actions."""
    bots = list(game_state.get("bots", []))
    grid = dict(game_state.get("grid", {}))
    width = int(grid.get("width", 0))
    height = int(grid.get("height", 0))
    walls = {
        (int(w[0]), int(w[1]))
        for w in grid.get("walls", [])
        if isinstance(w, (list, tuple)) and len(w) == 2
    }

    bot_positions: dict[int, tuple[int, int]] = {}
    for bot in bots:
        bid = int(bot.get("id", -1))
        pos = bot.get("position", [0, 0])
        bot_positions[bid] = (int(pos[0]), int(pos[1]))

    intents_by_bot = _normalize_intents(intents)
    actions: list[dict[str, Any]] = []

    for bot in sorted(bots, key=lambda row: int(row.get("id", -1))):
        bot_id = int(bot.get("id", -1))
        bot_pos = bot_positions.get(bot_id, (0, 0))
        intent = intents_by_bot.get(bot_id, {})
        requested_action = str(intent.get("action", "")).strip().lower()

        if requested_action and requested_action in ALLOWED_ACTIONS:
            action_payload: dict[str, Any] = {"bot": bot_id, "action": requested_action}
            if requested_action == "pick_up" and "item_id" in intent:
                action_payload["item_id"] = str(intent.get("item_id"))
            actions.append(action_payload)
            continue

        if requested_action and strict:
            raise ActionValidationError(
                f"Invalid action generated. Allowed actions: {', '.join(ALLOWED_ACTIONS)}"
            )

        target_raw = intent.get("target")
        target: tuple[int, int] | None = None
        if isinstance(target_raw, (list, tuple)) and len(target_raw) == 2:
            target = (int(target_raw[0]), int(target_raw[1]))

        if target is None:
            actions.append({"bot": bot_id, "action": "wait"})
            continue

        if target == bot_pos:
            if requested_action in {"pick_up", "drop_off"}:
                payload: dict[str, Any] = {"bot": bot_id, "action": requested_action}
                if requested_action == "pick_up" and "item_id" in intent:
                    payload["item_id"] = str(intent.get("item_id"))
                actions.append(payload)
            else:
                actions.append({"bot": bot_id, "action": "wait"})
            continue

        blocked = set(bot_positions.values()) - {bot_pos}
        step = astar_next_step(
            start=bot_pos,
            goal=target,
            width=width,
            height=height,
            walls=walls,
            blocked=blocked,
        )
        if step is None:
            actions.append({"bot": bot_id, "action": "wait"})
            continue

        move_action = _action_from_step(bot_pos, step)
        actions.append({"bot": bot_id, "action": move_action})

    return actions


def build_actions_payload(
    *,
    game_state: dict[str, Any],
    strategy_fn: StrategyFn,
    strict: bool = True,
) -> dict[str, Any]:
    intents = strategy_fn(game_state)
    if not isinstance(intents, list):
        raise ActionValidationError("Strategy must return list[dict] intents")
    return {"actions": intents_to_actions(game_state=game_state, intents=intents, strict=strict)}


class LiveCoreRunner:
    """WebSocket game loop with immutable parsing + action rendering flow."""

    def __init__(self, *, strategy_file: str | Path, strict: bool = True):
        self.strategy_file = Path(strategy_file).resolve()
        self.strategy_fn = load_strategy_callable(self.strategy_file)
        self.strict = bool(strict)

    async def play(self, ws_url: str) -> dict[str, Any] | None:
        async with websockets.connect(ws_url, max_size=2**20, close_timeout=5) as ws:
            while True:
                raw = await ws.recv()
                message = json.loads(raw)
                msg_type = str(message.get("type", "")).strip().lower()

                if msg_type == "game_over":
                    return message
                if msg_type != "game_state":
                    continue

                payload = build_actions_payload(
                    game_state=message,
                    strategy_fn=self.strategy_fn,
                    strict=self.strict,
                )
                await ws.send(json.dumps(payload))
