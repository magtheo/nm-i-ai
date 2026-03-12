"""Champion registry — the single source of truth for the best known baseline.

The champion is the strategy that produced the highest average simulation score.
It is NEVER overwritten unless a challenger explicitly beats it by at least
``min_improvement`` points.  The full history of past champions is always
preserved in ``champions_history.jsonl`` so nothing is ever lost.

Thread / process safety: all writes go through ``_atomic_write_json`` which
writes to a temp file then renames, so concurrent readers always see a complete
valid JSON file.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
CHAMPION_FILE = REGISTRY_DIR / "champion.json"
HISTORY_FILE = REGISTRY_DIR / "champions_history.jsonl"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChampionRecord:
    """Snapshot of the best known strategy at some point in time."""

    strategy_file: str
    """Path (relative to bot_v.3/ root) to the strategy .py used in evaluation."""

    average_score: float
    """Average score across all evaluation difficulties."""

    per_difficulty: dict[str, float]
    """Score by difficulty key, e.g. {"easy": 12.0, "medium": 24.0, ...}."""

    promoted_at: str
    """ISO-8601 timestamp when this champion was established."""

    hypothesis_name: str = "baseline"
    """Human-readable name for the hypothesis that produced this champion."""

    notes: str = ""
    """Optional free-form description."""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChampionRecord":
        return cls(
            strategy_file=str(d["strategy_file"]),
            average_score=float(d["average_score"]),
            per_difficulty={str(k): float(v) for k, v in d.get("per_difficulty", {}).items()},
            promoted_at=str(d.get("promoted_at", "")),
            hypothesis_name=str(d.get("hypothesis_name", "baseline")),
            notes=str(d.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to *path* atomically (write temp, rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=True)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_history(record: ChampionRecord) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), ensure_ascii=True) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load_champion() -> Optional[ChampionRecord]:
    """Return the current champion, or None if no champion is established yet."""
    if not CHAMPION_FILE.exists():
        return None
    try:
        data = json.loads(CHAMPION_FILE.read_text(encoding="utf-8"))
        return ChampionRecord.from_dict(data)
    except Exception:
        return None


def set_champion(
    record: ChampionRecord,
    *,
    min_improvement: float = 0.0,
    force: bool = False,
) -> bool:
    """Promote *record* to champion if it's better than the current one.

    Parameters
    ----------
    record:
        The candidate champion record.
    min_improvement:
        The candidate must beat the current champion by at least this many points.
        Default 0.0 means any improvement qualifies.
    force:
        If True, set regardless of score comparison (used for initial seeding).

    Returns
    -------
    bool
        True if the champion was updated; False if the candidate was rejected.
    """
    current = load_champion()

    if not force and current is not None:
        if record.average_score < current.average_score + min_improvement:
            return False  # Challenger did not beat champion

    # Archive the previous champion before overwriting
    if current is not None:
        _append_history(current)

    _atomic_write_json(CHAMPION_FILE, record.to_dict())
    return True


def is_better_than_champion(score: float, *, min_improvement: float = 0.0) -> bool:
    """Return True if *score* would beat the current champion."""
    current = load_champion()
    if current is None:
        return True  # No champion → any score sets new one
    return score >= current.average_score + min_improvement


def champion_score() -> float:
    """Return current champion's average_score, or 0.0 if none."""
    current = load_champion()
    return current.average_score if current is not None else 0.0


def load_champion_history() -> list[ChampionRecord]:
    """Return all past champions in chronological order."""
    if not HISTORY_FILE.exists():
        return []
    records: list[ChampionRecord] = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(ChampionRecord.from_dict(json.loads(line)))
        except Exception:
            pass
    return records


def describe_champion() -> str:
    """Return a human-readable one-line description of the current champion."""
    champ = load_champion()
    if champ is None:
        return "No champion established yet."
    return (
        f"Champion: '{champ.hypothesis_name}'  "
        f"avg_score={champ.average_score:.2f}  "
        f"promoted={champ.promoted_at[:19]}  "
        f"file={champ.strategy_file}"
    )
