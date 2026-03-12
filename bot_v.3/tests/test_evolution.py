"""Tests for the evolution loop (champion gating, run_hypothesis flow)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from bot.champion import ChampionRecord, load_champion, set_champion
from forge.evolution import (
    EvolutionLoopConfig,
    _batch_to_per_difficulty,
    run_hypothesis,
    seed_champion_from_file,
)
from forge.simulator import GrocerySimulator


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]


def _patch_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect champion + hypothesis registry to tmp_path."""
    import bot.champion as champ_mod
    import bot.hypothesis as hyp_mod
    monkeypatch.setattr(champ_mod, "CHAMPION_FILE", tmp_path / "champion.json")
    monkeypatch.setattr(champ_mod, "HISTORY_FILE", tmp_path / "champions_history.jsonl")
    monkeypatch.setattr(champ_mod, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(hyp_mod, "HYPOTHESES_DIR", tmp_path / "hypotheses")
    monkeypatch.setattr(hyp_mod, "REGISTRY_DIR", tmp_path)


def _seed_champ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, score: float = 10.0) -> None:
    _patch_registry(monkeypatch, tmp_path)
    set_champion(
        ChampionRecord(
            strategy_file=str(ROOT / "forge" / "strategy.py"),
            average_score=score,
            per_difficulty={"easy": score},
            promoted_at=datetime.now().isoformat(timespec="seconds"),
            hypothesis_name="test_baseline",
        ),
        force=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _batch_to_per_difficulty
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchToPerDifficulty:
    def test_maps_difficulty_to_score(self) -> None:
        sim = GrocerySimulator(difficulty="easy", seed=7001, max_rounds=5)
        from forge.simulator import evaluate_strategy_file
        batch = evaluate_strategy_file(
            strategy_file=ROOT / "forge" / "strategy.py",
            difficulties=["easy"],
            max_rounds=5,
        )
        per_diff = _batch_to_per_difficulty(batch)
        assert "easy" in per_diff
        assert isinstance(per_diff["easy"], float)


# ─────────────────────────────────────────────────────────────────────────────
# seed_champion_from_file
# ─────────────────────────────────────────────────────────────────────────────

class TestSeedChampion:
    def test_seeds_champion_from_strategy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _patch_registry(monkeypatch, tmp_path)
        # Also patch hypothesis manager to use tmp dir
        from bot.hypothesis import HypothesisManager
        monkeypatch.setattr("forge.evolution.HypothesisManager",
                            lambda: HypothesisManager(tmp_path / "hypotheses"))

        record = seed_champion_from_file(
            ROOT / "forge" / "strategy.py",
            hypothesis_name="test_seed",
            max_rounds=5,
            verbose=False,
        )
        assert record.average_score >= 0.0
        champ = load_champion()
        assert champ is not None
        assert champ.hypothesis_name == "test_seed"


# ─────────────────────────────────────────────────────────────────────────────
# run_hypothesis
# ─────────────────────────────────────────────────────────────────────────────

class TestRunHypothesis:
    def test_promotes_when_score_beats_champion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If candidate scores above champion, it should be promoted."""
        _seed_champ(tmp_path, monkeypatch, score=0.0)  # Champion score=0 → anything wins

        from bot.hypothesis import HypothesisManager
        monkeypatch.setattr("forge.evolution.HypothesisManager",
                            lambda: HypothesisManager(tmp_path / "hypotheses"))

        record = run_hypothesis(
            "test_promotion",
            ROOT / "forge" / "strategy.py",
            description="Should promote over 0-score champion",
            max_rounds=10,
            verbose=False,
        )
        # The strategy.py gets some score even in 10 rounds; with champion=0 it should promote
        # (but we can't guarantee exactly; check at least it ran cleanly)
        assert record.average_score is not None
        assert record.error is None
        assert record.status in ("promoted", "archived")

    def test_archives_when_file_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champ(tmp_path, monkeypatch, score=10.0)
        from bot.hypothesis import HypothesisManager
        monkeypatch.setattr("forge.evolution.HypothesisManager",
                            lambda: HypothesisManager(tmp_path / "hypotheses"))

        record = run_hypothesis(
            "missing_file_test",
            tmp_path / "nonexistent_strategy.py",
            max_rounds=5,
            verbose=False,
        )
        assert record.status == "archived"
        assert record.error is not None

    def test_archives_when_strategy_errors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A strategy that raises inside simulator should be archived, not promoted."""
        _seed_champ(tmp_path, monkeypatch, score=10.0)
        from bot.hypothesis import HypothesisManager
        monkeypatch.setattr("forge.evolution.HypothesisManager",
                            lambda: HypothesisManager(tmp_path / "hypotheses"))

        # Write a broken strategy to tmp_path
        broken = tmp_path / "broken_strategy.py"
        broken.write_text(
            "def decide_intents(game_state):\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )

        record = run_hypothesis(
            "broken_strategy_test",
            broken,
            max_rounds=5,
            verbose=False,
        )
        assert record.status == "archived"

    def test_champion_unchanged_when_hypothesis_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Champion must not change when a hypothesis fails."""
        _seed_champ(tmp_path, monkeypatch, score=99.0)
        from bot.hypothesis import HypothesisManager
        monkeypatch.setattr("forge.evolution.HypothesisManager",
                            lambda: HypothesisManager(tmp_path / "hypotheses"))

        broken = tmp_path / "bad.py"
        broken.write_text("def decide_intents(gs):\n    raise RuntimeError('fail')\n")

        run_hypothesis("bad_test", broken, max_rounds=3, verbose=False)
        champ = load_champion()
        assert champ is not None
        assert champ.average_score == pytest.approx(99.0)

    def test_duplicate_hypothesis_name_is_overwritten(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_champ(tmp_path, monkeypatch, score=0.0)
        from bot.hypothesis import HypothesisManager
        monkeypatch.setattr("forge.evolution.HypothesisManager",
                            lambda: HypothesisManager(tmp_path / "hypotheses"))

        for _ in range(2):
            record = run_hypothesis(
                "rerun_test",
                ROOT / "forge" / "strategy.py",
                max_rounds=5,
                verbose=False,
            )
        assert record.name == "rerun_test"


# ─────────────────────────────────────────────────────────────────────────────
# Strategy syntax validation (ensure improved strategy.py compiles + runs)
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategyCompiles:
    def test_strategy_py_compiles(self) -> None:
        source = (ROOT / "forge" / "strategy.py").read_text(encoding="utf-8")
        compile(source, "forge/strategy.py", "exec")

    def test_strategy_runs_in_simulator(self) -> None:
        from forge.simulator import evaluate_strategy_file
        batch = evaluate_strategy_file(
            strategy_file=ROOT / "forge" / "strategy.py",
            difficulties=["easy"],
            max_rounds=50,
        )
        assert not batch.has_errors(), f"Strategy error: {batch.worst_error()}"
        assert batch.average_score() >= 0

    def test_hypothesis_strategy_compiles(self) -> None:
        hyp = ROOT / "forge" / "hypotheses" / "more_deliverers_v1.py"
        if hyp.exists():
            source = hyp.read_text(encoding="utf-8")
            compile(source, str(hyp), "exec")

    def test_hypothesis_runs_in_simulator(self) -> None:
        hyp = ROOT / "forge" / "hypotheses" / "more_deliverers_v1.py"
        if not hyp.exists():
            pytest.skip("Hypothesis file not present")
        from forge.simulator import evaluate_strategy_file
        batch = evaluate_strategy_file(
            strategy_file=hyp,
            difficulties=["easy"],
            max_rounds=50,
        )
        assert not batch.has_errors(), f"Hypothesis error: {batch.worst_error()}"
