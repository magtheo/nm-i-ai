"""Tests for the hypothesis lifecycle manager (bot_v.3/bot/hypothesis.py)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from bot.champion import ChampionRecord, set_champion
from bot.hypothesis import HypothesisManager, HypothesisRecord


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mgr(tmp_path: Path) -> HypothesisManager:
    return HypothesisManager(hypotheses_dir=tmp_path / "hypotheses")


def _seed_champion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, score: float = 20.0) -> None:
    """Set a champion in the tmp registry."""
    import bot.champion as champ_mod
    monkeypatch.setattr(champ_mod, "CHAMPION_FILE", tmp_path / "champion.json")
    monkeypatch.setattr(champ_mod, "HISTORY_FILE", tmp_path / "champions_history.jsonl")
    monkeypatch.setattr(champ_mod, "REGISTRY_DIR", tmp_path)
    set_champion(
        ChampionRecord(
            strategy_file="forge/strategy.py",
            average_score=score,
            per_difficulty={"easy": score},
            promoted_at=datetime.now().isoformat(timespec="seconds"),
        ),
        force=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Propose / load / save
# ─────────────────────────────────────────────────────────────────────────────

class TestPropose:
    def test_propose_creates_record(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        rec = mgr.propose("test_h", "forge/test.py", description="A test hypothesis")
        assert rec.name == "test_h"
        assert rec.status == "proposed"
        assert rec.strategy_file == "forge/test.py"
        assert rec.description == "A test hypothesis"
        assert rec.average_score is None

    def test_propose_persists_to_disk(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("persisted_h", "forge/test.py")
        loaded = mgr.load("persisted_h")
        assert loaded.name == "persisted_h"

    def test_propose_raises_on_duplicate(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("dup_h", "forge/test.py")
        with pytest.raises(ValueError, match="already exists"):
            mgr.propose("dup_h", "forge/test2.py")

    def test_propose_overwrite_allowed(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("dup_h", "forge/test.py")
        rec = mgr.propose("dup_h", "forge/test2.py", overwrite=True)
        assert rec.strategy_file == "forge/test2.py"

    def test_load_raises_on_missing(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        with pytest.raises(KeyError):
            mgr.load("nonexistent")


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_mark_evaluating(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("h1", "forge/h1.py")
        mgr.mark_evaluating("h1")
        assert mgr.load("h1").status == "evaluating"

    def test_record_evaluation(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("h2", "forge/h2.py")
        mgr.record_evaluation(
            "h2",
            average_score=25.0,
            per_difficulty={"easy": 10.0, "medium": 15.0},
            champion_score_at_eval=20.0,
        )
        rec = mgr.load("h2")
        assert rec.average_score == 25.0
        assert rec.per_difficulty == {"easy": 10.0, "medium": 15.0}
        assert rec.champion_score_at_eval == 20.0
        assert rec.status == "evaluating"
        assert rec.evaluated_at != ""

    def test_archive_explicitly(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("h3", "forge/h3.py")
        mgr.archive("h3", reason="negative result")
        assert mgr.load("h3").status == "archived"

    def test_error_in_record_causes_archive_on_promote(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champion(monkeypatch, tmp_path, score=10.0)
        mgr = _mgr(tmp_path)
        mgr.propose("h_err", "forge/err.py")
        mgr.record_evaluation(
            "h_err",
            average_score=50.0,  # would beat champion
            per_difficulty={},
            error="runtime error",
        )
        promoted = mgr.try_promote("h_err")
        assert promoted is False
        assert mgr.load("h_err").status == "archived"


# ─────────────────────────────────────────────────────────────────────────────
# Promotion logic
# ─────────────────────────────────────────────────────────────────────────────

class TestPromotion:
    def test_promote_when_better_than_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champion(monkeypatch, tmp_path, score=20.0)
        mgr = _mgr(tmp_path)
        mgr.propose("winner", "forge/winner.py")
        mgr.record_evaluation(
            "winner",
            average_score=25.0,
            per_difficulty={"easy": 25.0},
            champion_score_at_eval=20.0,
        )
        promoted = mgr.try_promote("winner")
        assert promoted is True
        assert mgr.load("winner").status == "promoted"

    def test_archive_when_not_better_than_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champion(monkeypatch, tmp_path, score=30.0)
        mgr = _mgr(tmp_path)
        mgr.propose("loser", "forge/loser.py")
        mgr.record_evaluation(
            "loser",
            average_score=28.0,
            per_difficulty={"easy": 28.0},
            champion_score_at_eval=30.0,
        )
        promoted = mgr.try_promote("loser")
        assert promoted is False
        assert mgr.load("loser").status == "archived"

    def test_min_improvement_respected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champion(monkeypatch, tmp_path, score=20.0)
        mgr = _mgr(tmp_path)
        mgr.propose("barely_better", "forge/bb.py")
        mgr.record_evaluation(
            "barely_better",
            average_score=21.0,
            per_difficulty={},
        )
        promoted = mgr.try_promote("barely_better", min_improvement=2.0)
        assert promoted is False
        assert mgr.load("barely_better").status == "archived"

    def test_try_promote_raises_without_scores(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("no_scores", "forge/no.py")
        with pytest.raises(RuntimeError, match="no evaluation scores"):
            mgr.try_promote("no_scores")


# ─────────────────────────────────────────────────────────────────────────────
# List / filter
# ─────────────────────────────────────────────────────────────────────────────

class TestListing:
    def test_list_all(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("a", "forge/a.py")
        mgr.propose("b", "forge/b.py")
        mgr.propose("c", "forge/c.py")
        recs = mgr.list_all()
        names = {r.name for r in recs}
        assert names == {"a", "b", "c"}

    def test_list_by_status(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr.propose("x", "forge/x.py")
        mgr.propose("y", "forge/y.py")
        mgr.archive("y")
        proposed = mgr.list_by_status("proposed")
        assert len(proposed) == 1
        assert proposed[0].name == "x"

    def test_summary_table_format(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champion(monkeypatch, tmp_path)
        mgr = _mgr(tmp_path)
        mgr.propose("hyp_a", "forge/a.py", description="test")
        table = mgr.summary_table()
        assert "hyp_a" in table
        assert "proposed" in table
