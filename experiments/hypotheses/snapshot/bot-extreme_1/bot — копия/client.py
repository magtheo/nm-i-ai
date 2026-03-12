"""GameWSClient — outbound WebSocket client for the NMiAI game server."""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

import websockets

from .decision_engine import DecisionEngine
from .endpoint import redact_ws_url
from .models import GameOver, GameState, RoundActions
from .telemetry import RoundLogger


class GameWSClient:
    """Connects to the game server and runs the play loop."""

    def __init__(
        self,
        url: str,
        engine: DecisionEngine,
        *,
        timeout: float = 1.8,
        logger: Optional[RoundLogger] = None,
        debug: bool = False,
        on_state: Optional[Callable[[GameState, dict], None]] = None,
        on_actions: Optional[Callable[[GameState, RoundActions], None]] = None,
        on_game_over: Optional[Callable[[GameOver], None]] = None,
    ):
        self.url = url
        self.engine = engine
        self.timeout = timeout
        self.logger = logger
        self.debug = debug
        self.game_over: Optional[GameOver] = None
        self.on_state = on_state
        self.on_actions = on_actions
        self.on_game_over = on_game_over

    async def play(self) -> GameOver | None:
        """Connect and play until game_over or disconnect."""
        debug_file = None
        if self.debug:
            os.makedirs("logs/bot", exist_ok=True)
            debug_file = open("logs/bot/debug_states.jsonl", "w", encoding="utf-8")
        try:
            async with websockets.connect(
                self.url,
                max_size=2**20,  # 1MB — plenty for game state
                close_timeout=5,
            ) as ws:
                if self.debug:
                    print(f"[GameWSClient] Connected to {redact_ws_url(self.url)}")

                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)

                    if msg.get("type") == "game_over":
                        self.game_over = GameOver(**msg)
                        if self.on_game_over:
                            self.on_game_over(self.game_over)
                        if self.debug:
                            print(f"\n[GameWSClient] Game Over! "
                                  f"Score: {self.game_over.score}")
                        if self.logger:
                            items_delivered = self.game_over.items_delivered
                            if items_delivered is None:
                                items_delivered = self.game_over.items or 0
                            orders_completed = self.game_over.orders_completed
                            if orders_completed is None:
                                orders_completed = self.game_over.orders or 0
                            self.logger.finalize(
                                self.game_over.score,
                                items=items_delivered or 0,
                                orders=orders_completed or 0,
                            )
                        return self.game_over

                    # Parse game state
                    state = GameState(**msg)
                    if self.on_state:
                        self.on_state(state, msg)

                    # Decide synchronously to avoid background-thread race conditions.
                    # Executor+timeout can leave stale decide() calls mutating engine
                    # memory after fallback actions are already sent.
                    try:
                        actions = self.engine.decide(state)
                    except Exception:
                        from .models import BotAction, BotActionCommand
                        actions = RoundActions(actions=[
                            BotActionCommand(bot=b.id, action=BotAction.WAIT)
                            for b in state.bots
                        ])
                        if self.debug:
                            print(f"  [ERROR] round={state.round} - fallback to wait")

                    # Send
                    payload = actions.to_payload()
                    await ws.send(json.dumps(payload))
                    if self.on_actions:
                        self.on_actions(state, actions)

                    # Debug dump first 10 rounds to file
                    if debug_file and state.round < 10:
                        entry = {
                            "round": state.round,
                            "bot_pos": [b.position for b in state.bots],
                            "bot_inv": [b.inventory for b in state.bots],
                            "items": [(it.id, it.type, it.position) for it in state.items],
                            "orders": [(o.id, o.status.value, o.items_required, o.items_delivered) for o in state.orders],
                            "drop_off": state.drop_off,
                            "grid_size": [state.grid.width, state.grid.height],
                            "walls": state.grid.walls,
                            "score": state.score,
                            "actions": [a.to_dict() for a in actions.actions],
                        }
                        if state.round == 0:
                            entry["raw_keys"] = list(msg.keys())
                        debug_file.write(json.dumps(entry, ensure_ascii=True) + "\n")
                        debug_file.flush()

                    # Log
                    if self.logger:
                        self.logger.log_round(
                            round_num=state.round,
                            score=state.score,
                            decision_ms=self.engine.last_decision_ms,
                            actions=[a.to_dict() for a in actions.actions],
                            raw_state=msg if self.logger.save_states else None,
                        )

                    if self.debug and state.round % 10 == 0:
                        print(f"  Round {state.round:3d}/{state.max_rounds} "
                              f"score={state.score:4d} "
                              f"dt={self.engine.last_decision_ms:.1f}ms")

        except websockets.ConnectionClosedError as e:
            if self.debug:
                print(f"[GameWSClient] Connection closed: {e}")
            return self.game_over
        except Exception as e:
            if self.debug:
                print(f"[GameWSClient] Error: {e}")
            raise
        finally:
            if debug_file:
                debug_file.close()
