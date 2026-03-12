"""Hypothesis lifecycle manager.

A *hypothesis* is a named, versioned strategy variant that is tested against
the current champion.  The lifecycle is:

    proposed → evaluating → promoted | archived

All state is persisted to ``registry/hypotheses/<name>.json`` so the system
survives restarts.  This means you can:

  - propose a hypothesis (write the strategy code + metadata to disk)
  - evaluate it later (run simulation, update the record with scores)
  - promote it (if it beats the champion) or archive it (if it doesn't)

Nothing is ever deleted — archived hypotheses stay on disk as a searchable
experiment log.

Usage
-----
::

    from bot.hypothesis import HypothesisManager

    mgr = HypothesisManager()
    mgr.propose("preview_lookahead", "forge/hypotheses/preview_lookahead.py",
                 description="Add 2-step preview lookahead to strategy")
    record = mgr.evaluate("preview_lookahead", ...)
    mgr.try_promote("preview_lookahead")
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .champion import ChampionRecord, set_champion


REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
HYPOTHESES_DIR = REGISTRY_DIR / "hypotheses"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

HypothesisStatus = str  # "proposed" | "evaluating" | "promoted" | "archived"


@dataclass
class HypothesisRecord:
    """Full lifecycle record for one hypothesis."""

    name: str
    """Unique slug, e.g. 'preview_lookahead_v1'."""

    strategy_file: str
    """Path to the strategy .py for this hypothesis (relative to bot_v.3/)."""

    description: str = ""
    status: HypothesisStatus = "proposed"

    average_score: Optional[float] = None
    per_difficulty: dict[str, float] = field(default_factory=dict)

    champion_score_at_eval: Optional[float] = None
    """Champion average_score at the time this hypothesis was evaluated."""

    proposed_at: str = ""
    evaluated_at: str = ""
    decided_at: str = ""

    error: Optional[str] = None
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HypothesisRecord":
        return cls(
            name=str(d["name"]),
            strategy_file=str(d["strategy_file"]),
            description=str(d.get("description", "")),
            status=str(d.get("status", "proposed")),
            average_score=float(d["average_score"]) if d.get("average_score") is not None else None,
            per_difficulty={str(k): float(v) for k, v in d.get("per_difficulty", {}).items()},
            champion_score_at_eval=float(d["champion_score_at_eval"]) if d.get("champion_score_at_eval") is not None else None,
            proposed_at=str(d.get("proposed_at", "")),
            evaluated_at=str(d.get("evaluated_at", "")),
            decided_at=str(d.get("decided_at", "")),
            error=d.get("error"),
            notes=str(d.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_write(path: Path, data: dict) -> None:
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


def _record_path(name: str) -> Path:
    return HYPOTHESES_DIR / f"{name}.json"


# ─────────────────────────────────────────────────────────────────────────────
# Hypothesis Manager
# ─────────────────────────────────────────────────────────────────────────────

class HypothesisManager:
    """Manages the full hypothesis lifecycle on disk."""

    def __init__(self, hypotheses_dir: Path | None = None) -> None:
        self._dir = hypotheses_dir or HYPOTHESES_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self._dir / f"{name}.json"

    # ── CRUD ──────────────────────────────────────────────────────────────

    def load(self, name: str) -> HypothesisRecord:
        p = self._path(name)
        if not p.exists():
            raise KeyError(f"Hypothesis not found: {name!r}")
        return HypothesisRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, record: HypothesisRecord) -> None:
        _safe_write(self._path(record.name), record.to_dict())

    def list_all(self) -> list[HypothesisRecord]:
        records: list[HypothesisRecord] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                records.append(HypothesisRecord.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                pass
        return records

    def list_by_status(self, status: HypothesisStatus) -> list[HypothesisRecord]:
        return [r for r in self.list_all() if r.status == status]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def propose(
        self,
        name: str,
        strategy_file: str,
        *,
        description: str = "",
        notes: str = "",
        overwrite: bool = False,
    ) -> HypothesisRecord:
        """Register a new hypothesis.

        Raises ValueError if the name already exists and ``overwrite=False``.
        """
        if self._path(name).exists() and not overwrite:
            raise ValueError(
                f"Hypothesis {name!r} already exists. "
                "Use overwrite=True or choose a different name."
            )
        record = HypothesisRecord(
            name=name,
            strategy_file=strategy_file,
            description=description,
            status="proposed",
            proposed_at=datetime.now().isoformat(timespec="seconds"),
            notes=notes,
        )
        self.save(record)
        return record

    def mark_evaluating(self, name: str) -> HypothesisRecord:
        """Transition hypothesis to 'evaluating' state."""
        record = self.load(name)
        record.status = "evaluating"
        self.save(record)
        return record

    def record_evaluation(
        self,
        name: str,
        *,
        average_score: float,
        per_difficulty: dict[str, float],
        champion_score_at_eval: Optional[float] = None,
        error: Optional[str] = None,
    ) -> HypothesisRecord:
        """Store evaluation results for a hypothesis."""
        record = self.load(name)
        record.average_score = average_score
        record.per_difficulty = dict(per_difficulty)
        record.champion_score_at_eval = champion_score_at_eval
        record.error = error
        record.evaluated_at = datetime.now().isoformat(timespec="seconds")
        record.status = "evaluating"
        self.save(record)
        return record

    def try_promote(
        self,
        name: str,
        *,
        min_improvement: float = 0.0,
    ) -> bool:
        """Attempt to promote the hypothesis to champion.

        The hypothesis must already have evaluation scores.
        Returns True if promoted, False if archived (score didn't beat champion).
        """
        record = self.load(name)
        if record.average_score is None:
            raise RuntimeError(
                f"Cannot promote hypothesis {name!r}: no evaluation scores recorded. "
                "Run record_evaluation() first."
            )
        if record.error:
            record.status = "archived"
            record.decided_at = datetime.now().isoformat(timespec="seconds")
            record.notes += f" | archived (error: {record.error})"
            self.save(record)
            return False

        champ = ChampionRecord(
            strategy_file=record.strategy_file,
            average_score=record.average_score,
            per_difficulty=record.per_difficulty,
            promoted_at=datetime.now().isoformat(timespec="seconds"),
            hypothesis_name=record.name,
            notes=record.description,
        )
        promoted = set_champion(champ, min_improvement=min_improvement)
        record.decided_at = datetime.now().isoformat(timespec="seconds")
        record.status = "promoted" if promoted else "archived"
        self.save(record)
        return promoted

    def archive(self, name: str, *, reason: str = "") -> HypothesisRecord:
        """Forcibly archive a hypothesis (regardless of score)."""
        record = self.load(name)
        record.status = "archived"
        record.decided_at = datetime.now().isoformat(timespec="seconds")
        if reason:
            record.notes += f" | archived: {reason}"
        self.save(record)
        return record

    # ── Reporting ──────────────────────────────────────────────────────────

    def summary_table(self) -> str:
        """Return a formatted text table of all hypotheses."""
        records = self.list_all()
        if not records:
            return "(no hypotheses registered)"

        lines = [
            f"{'Name':<35} {'Status':<12} {'Score':>8} {'Champion':>10} {'Proposed'}",
            "-" * 90,
        ]
        for r in sorted(records, key=lambda x: x.proposed_at, reverse=True):
            score_str = f"{r.average_score:.2f}" if r.average_score is not None else "   —"
            champ_str = f"{r.champion_score_at_eval:.2f}" if r.champion_score_at_eval is not None else "   —"
            lines.append(
                f"{r.name:<35} {r.status:<12} {score_str:>8} {champ_str:>10}  {r.proposed_at[:19]}"
            )
        return "\n".join(lines)
