"""Round-level telemetry — JSONL game logger + summary."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RoundRecord:
    round: int
    score: int
    decision_ms: float
    actions: list[dict]
    game_state: dict | None = None  # optional full state


@dataclass
class GameSummary:
    difficulty: str
    total_score: int
    rounds_played: int
    items_delivered: int
    orders_completed: int
    avg_decision_ms: float
    max_decision_ms: float
    start_time: str
    end_time: str


class RoundLogger:
    """Logs each round to a JSONL file and produces a game summary."""

    def __init__(self, log_dir: str = "logs/bot", difficulty: str = "unknown", save_states: bool = False):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.difficulty = difficulty
        self.save_states = save_states

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.log_dir / f"game_{ts}_{difficulty}.jsonl"
        self._file = open(self.log_path, "w", encoding="utf-8")

        self.rounds: list[RoundRecord] = []
        self.start_time = datetime.now().isoformat()
        self._last_score = 0

    def log_round(
        self,
        round_num: int,
        score: int,
        decision_ms: float,
        actions: list[dict],
        raw_state: dict | None = None,
    ) -> None:
        record = RoundRecord(
            round=round_num,
            score=score,
            decision_ms=decision_ms,
            actions=actions,
            game_state=raw_state if self.save_states else None,
        )
        self.rounds.append(record)

        entry: dict[str, Any] = {
            "round": round_num,
            "score": score,
            "score_delta": score - self._last_score,
            "decision_ms": round(decision_ms, 2),
            "actions": actions,
        }
        if self.save_states and raw_state:
            entry["state"] = raw_state

        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()
        self._last_score = score

    def finalize(self, final_score: int, items: int = 0, orders: int = 0) -> GameSummary:
        decision_times = [r.decision_ms for r in self.rounds]
        summary = GameSummary(
            difficulty=self.difficulty,
            total_score=final_score,
            rounds_played=len(self.rounds),
            items_delivered=items,
            orders_completed=orders,
            avg_decision_ms=sum(decision_times) / len(decision_times) if decision_times else 0,
            max_decision_ms=max(decision_times) if decision_times else 0,
            start_time=self.start_time,
            end_time=datetime.now().isoformat(),
        )

        # Write summary as last line
        self._file.write(json.dumps({
            "type": "summary",
            **summary.__dict__,
        }) + "\n")
        self._file.close()
        return summary

    def __del__(self):
        if hasattr(self, "_file") and not self._file.closed:
            self._file.close()
