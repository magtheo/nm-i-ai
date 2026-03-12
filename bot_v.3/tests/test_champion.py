"""Tests for the champion registry (bot_v.3/bot/champion.py)."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from bot.champion import (
    ChampionRecord,
    _append_history,
    _atomic_write_json,
    champion_score,
    describe_champion,
    is_better_than_champion,
    load_champion,
    load_champion_history,
    set_champion,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _record(avg: float, name: str = "test", strategy: str = "forge/strategy.py") -> ChampionRecord:
    return ChampionRecord(
        strategy_file=strategy,
        average_score=avg,
        per_difficulty={"easy": avg, "medium": avg},
        promoted_at=datetime.now().isoformat(timespec="seconds"),
        hypothesis_name=name,
    )


def _use_tmp_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect champion.py's CHAMPION_FILE and HISTORY_FILE to tmp_path."""
    import bot.champion as champ_mod
    monkeypatch.setattr(champ_mod, "CHAMPION_FILE", tmp_path / "champion.json")
    monkeypatch.setattr(champ_mod, "HISTORY_FILE", tmp_path / "champions_history.jsonl")
    monkeypatch.setattr(champ_mod, "REGISTRY_DIR", tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# ChampionRecord tests
# ─────────────────────────────────────────────────────────────────────────────

class TestChampionRecord:
    def test_roundtrip(self) -> None:
        rec = _record(42.5)
        d = rec.to_dict()
        restored = ChampionRecord.from_dict(d)
        assert restored.average_score == 42.5
        assert restored.hypothesis_name == "test"

    def test_from_dict_defaults(self) -> None:
        rec = ChampionRecord.from_dict({
            "strategy_file": "foo.py",
            "average_score": "10.0",
            "per_difficulty": {},
            "promoted_at": "2026-01-01T00:00:00",
        })
        assert rec.hypothesis_name == "baseline"
        assert rec.notes == ""


# ─────────────────────────────────────────────────────────────────────────────
# load / set champion
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadSetChampion:
    def test_no_champion_returns_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        assert load_champion() is None

    def test_set_and_load(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        rec = _record(20.0)
        set_champion(rec, force=True)
        loaded = load_champion()
        assert loaded is not None
        assert loaded.average_score == 20.0

    def test_set_champion_rejects_lower_score(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(30.0), force=True)
        result = set_champion(_record(25.0))
        assert result is False
        assert load_champion().average_score == 30.0

    def test_set_champion_accepts_higher_score(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(30.0), force=True)
        result = set_champion(_record(35.0))
        assert result is True
        assert load_champion().average_score == 35.0

    def test_min_improvement_gate(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(30.0), force=True)
        # Score 31.0 is an improvement but not by 2.0
        result = set_champion(_record(31.0), min_improvement=2.0)
        assert result is False
        # Score 32.5 meets the threshold
        result = set_champion(_record(32.5), min_improvement=2.0)
        assert result is True

    def test_force_overwrites_regardless(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(50.0), force=True)
        set_champion(_record(10.0), force=True)
        assert load_champion().average_score == 10.0


# ─────────────────────────────────────────────────────────────────────────────
# History preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestHistory:
    def test_old_champion_archived_on_promotion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(20.0, name="v1"), force=True)
        set_champion(_record(25.0, name="v2"))

        history = load_champion_history()
        assert len(history) == 1
        assert history[0].hypothesis_name == "v1"
        assert history[0].average_score == 20.0

    def test_history_grows_with_each_promotion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(10.0, name="v1"), force=True)
        set_champion(_record(15.0, name="v2"))
        set_champion(_record(20.0, name="v3"))

        history = load_champion_history()
        assert len(history) == 2
        names = [r.hypothesis_name for r in history]
        assert "v1" in names
        assert "v2" in names

    def test_history_on_empty_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        assert load_champion_history() == []


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_champion_score_no_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        assert champion_score() == 0.0

    def test_champion_score_with_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(33.3), force=True)
        assert champion_score() == pytest.approx(33.3)

    def test_is_better_than_champion_no_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        assert is_better_than_champion(0.0) is True

    def test_is_better_than_champion_beats(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(20.0), force=True)
        assert is_better_than_champion(21.0) is True

    def test_is_better_than_champion_doesnt_beat(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        set_champion(_record(20.0), force=True)
        assert is_better_than_champion(19.0) is False

    def test_describe_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        assert "No champion" in describe_champion()
        set_champion(_record(15.5, name="my_test"), force=True)
        desc = describe_champion()
        assert "my_test" in desc
        assert "15.50" in desc


# ─────────────────────────────────────────────────────────────────────────────
# Atomic write / corrupt file robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestRobustness:
    def test_load_champion_returns_none_on_corrupt_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _use_tmp_registry(monkeypatch, tmp_path)
        champion_file = tmp_path / "champion.json"
        champion_file.write_text("not valid json", encoding="utf-8")
        assert load_champion() is None

    def test_atomic_write_produces_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "test.json"
        _atomic_write_json(target, {"key": "value", "num": 42})
        loaded = json.loads(target.read_text())
        assert loaded["key"] == "value"
        assert loaded["num"] == 42
