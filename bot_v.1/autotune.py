"""Autotune harness — search for the best PlannerConfig on a given difficulty.

Runs multiple game sessions with different parameter sets and records
results to ``.seed_artifacts/nmiai_grocery_bot/autotune/``.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .planner import PlannerConfig

# ── Paths ──────────────────────────────────────────────────────────────────

ARTIFACTS_DIR = Path(".seed_artifacts/nmiai_grocery_bot/autotune")
BEST_CONFIGS_DIR = Path("app/integrations/nmiai_grocery_bot/best_configs")


# ── Result dataclass ───────────────────────────────────────────────────────

@dataclass
class AutotuneResult:
    config: PlannerConfig
    score: int
    items_delivered: int
    orders_completed: int
    rounds_played: int
    avg_decision_ms: float
    run_index: int


# ── Config grid generation ─────────────────────────────────────────────────

def generate_config_grid(
    *,
    lookaheads: list[int] | None = None,
    preview_weights: list[float] | None = None,
    auto_delivery_bonuses: list[float] | None = None,
    tiebreak_seeds: list[int] | None = None,
) -> list[PlannerConfig]:
    """Build a grid of ``PlannerConfig`` s for autotune exploration.

    Default ranges are compact enough for ~30 runs.
    """
    lookaheads = lookaheads or [1, 2, 3]
    preview_weights = preview_weights or [2.0, 5.0]
    auto_delivery_bonuses = auto_delivery_bonuses or [3.0, 6.0, 10.0]
    tiebreak_seeds = tiebreak_seeds or [0, 1, 2]

    configs: list[PlannerConfig] = []
    for la, pw, adb, ts in itertools.product(
        lookaheads, preview_weights, auto_delivery_bonuses, tiebreak_seeds,
    ):
        configs.append(PlannerConfig(
            lookahead_orders=la,
            preview_weight=pw,
            auto_delivery_bonus=adb,
            tiebreak_seed=ts,
        ))
    return configs


# ── Persistence ────────────────────────────────────────────────────────────

def save_best_config(
    difficulty: str,
    config: PlannerConfig,
    result: AutotuneResult,
) -> Path:
    """Persist the best config to ``best_configs/<difficulty>.json``."""
    BEST_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    path = BEST_CONFIGS_DIR / f"{difficulty}.json"
    payload = {
        "config": config.to_dict(),
        "score": result.score,
        "items_delivered": result.items_delivered,
        "orders_completed": result.orders_completed,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_best_config(difficulty: str) -> PlannerConfig:
    """Load a previously saved best config, or return defaults."""
    path = BEST_CONFIGS_DIR / f"{difficulty}.json"
    if not path.exists():
        return PlannerConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return PlannerConfig.from_dict(data["config"])


# ── Autotune runner ────────────────────────────────────────────────────────

async def autotune(
    difficulty: str = "easy",
    *,
    max_runs: int = 30,
    target_score: int | None = None,
    access_token: str | None = None,
    debug: bool = False,
) -> AutotuneResult:
    """Run multiple games with different configs, return the best result.

    Parameters
    ----------
    difficulty : str
        ``easy`` | ``medium`` | ``hard`` | ``expert``.
    max_runs : int
        Maximum number of game sessions to play.
    target_score : int, optional
        Stop early when this score is reached.
    access_token : str, optional
        Bare JWT.  Loaded from ``.env`` when omitted.
    debug : bool
        Print per-run summaries.

    Returns
    -------
    AutotuneResult
        The best result found.
    """
    # Lazy imports to avoid circular deps at module level
    from app.integrations.nmiai_grocery_bot.endpoint import request_game_session
    from app.integrations.nmiai_grocery_bot.ws_client import _play
    from .planner import OptimizedEngine
    from .telemetry import RoundLogger

    configs = generate_config_grid()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    best: AutotuneResult | None = None
    results: list[AutotuneResult] = []

    run_count = min(max_runs, len(configs))
    if debug:
        print(f"\n=== AUTOTUNE ({difficulty}) — {run_count} runs ===\n")

    for i, cfg in enumerate(configs[:run_count]):
        # 10-second cooldown between games
        if i > 0:
            if debug:
                print("  (10s cooldown…)")
            await asyncio.sleep(10)

        session = request_game_session(difficulty, access_token=access_token)
        engine = OptimizedEngine(config=cfg, debug=False, verbose=False)
        logger = RoundLogger(
            log_dir=str(ARTIFACTS_DIR / "logs"),
            difficulty=difficulty,
            save_states=False,
        )

        game_result = await _play(session.ws_url, engine, logger=logger, debug=False)

        ar = AutotuneResult(
            config=cfg,
            score=game_result.score,
            items_delivered=game_result.items_delivered,
            orders_completed=game_result.orders_completed,
            rounds_played=game_result.rounds_played,
            avg_decision_ms=game_result.avg_decision_ms,
            run_index=i,
        )
        results.append(ar)

        # Save per-run result
        run_path = ARTIFACTS_DIR / f"run_{i:03d}.json"
        run_path.write_text(json.dumps({
            "run_index": i,
            "config": cfg.to_dict(),
            "score": ar.score,
            "items_delivered": ar.items_delivered,
            "orders_completed": ar.orders_completed,
            "rounds_played": ar.rounds_played,
            "avg_decision_ms": round(ar.avg_decision_ms, 2),
        }, indent=2), encoding="utf-8")

        if debug:
            marker = ""
            if best is None or ar.score > best.score:
                marker = " ★ NEW BEST"
            print(
                f"  Run {i+1:3d}/{run_count}  "
                f"score={ar.score:4d}  "
                f"la={cfg.lookahead_orders} pw={cfg.preview_weight} "
                f"adb={cfg.auto_delivery_bonus} ts={cfg.tiebreak_seed}"
                f"{marker}"
            )

        if best is None or ar.score > best.score:
            best = ar
            save_best_config(difficulty, cfg, ar)

        # Early stop
        if target_score is not None and ar.score >= target_score:
            if debug:
                print(f"\n  ✓ Target score {target_score} reached at run {i+1}!")
            break

    # Save summary
    summary_path = ARTIFACTS_DIR / "summary.json"
    summary_path.write_text(json.dumps({
        "difficulty": difficulty,
        "total_runs": len(results),
        "best_score": best.score if best else 0,
        "best_run_index": best.run_index if best else -1,
        "best_config": best.config.to_dict() if best else {},
        "all_scores": [r.score for r in results],
    }, indent=2), encoding="utf-8")

    if debug and best:
        print(f"\n=== AUTOTUNE COMPLETE ===")
        print(f"  Best score : {best.score}")
        print(f"  Best config: {best.config.to_dict()}")
        print(f"  Saved to   : {BEST_CONFIGS_DIR / f'{difficulty}.json'}")

    return best  # type: ignore[return-value]
