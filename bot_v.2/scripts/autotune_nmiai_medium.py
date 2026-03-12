"""Autotune live strategy parameters for Medium difficulty."""
from __future__ import annotations

import argparse
import itertools
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


THIS_FILE = Path(__file__).resolve()
BOT_ROOT = THIS_FILE.parents[1]
PROJECT_PARENT = BOT_ROOT.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))
if str(PROJECT_PARENT) not in sys.path:
    sys.path.append(str(PROJECT_PARENT))


RUN_SCRIPT = BOT_ROOT / "scripts" / "run_nmiai_grocery_bot.py"
RUN_ARTIFACT_ROOT = BOT_ROOT / ".seed_artifacts" / "nmiai" / "medium"
AUTOTUNE_ARTIFACT_ROOT = BOT_ROOT / ".seed_artifacts" / "nmiai" / "medium" / "autotune"
BEST_CONFIG_PATH = BOT_ROOT / "configs" / "best" / "medium.json"


@dataclass(frozen=True)
class CandidateConfig:
    lookahead_k: int
    active_weight: float
    preview_weight: float
    dropoff_threshold: float
    collision_aggressiveness: str
    seed: int

    def to_cli(self) -> list[str]:
        return [
            "--lookahead-k",
            str(self.lookahead_k),
            "--active-weight",
            str(self.active_weight),
            "--preview-weight",
            str(self.preview_weight),
            "--dropoff-threshold",
            str(self.dropoff_threshold),
            "--collision-aggressiveness",
            self.collision_aggressiveness,
            "--seed",
            str(self.seed),
        ]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttemptResult:
    config: CandidateConfig
    run_dir: str
    score: int
    items_delivered: int
    orders_completed: int
    max_score_exact: int | None
    max_score_upper_bound: int
    total_orders: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autotune NMiAI Grocery Bot on Medium")
    parser.add_argument("--max-runs", type=int, default=30, help="Maximum live runs including baseline")
    parser.add_argument("--cooldown-sec", type=float, default=3.0, help="Cooldown between attempts")
    parser.add_argument("--plateau-attempts", type=int, default=10, help="Stop after this many non-improving attempts")
    parser.add_argument("--record", action="store_true", help="Keep per-run artifacts from the live runner")
    return parser.parse_args()


def _existing_run_dirs() -> set[Path]:
    if not RUN_ARTIFACT_ROOT.exists():
        return set()
    return {p for p in RUN_ARTIFACT_ROOT.iterdir() if p.is_dir() and p.name.startswith("run_")}


def _latest_run_dir() -> Path:
    runs = [p for p in RUN_ARTIFACT_ROOT.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not runs:
        raise RuntimeError("No run artifacts found under .seed_artifacts/nmiai/medium")
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[0]


def _run_once(
    config: CandidateConfig,
    *,
    cooldown_sec: float,
    keep_artifacts: bool,
) -> AttemptResult:
    before = _existing_run_dirs()
    cmd = [
        sys.executable,
        str(RUN_SCRIPT),
        "--difficulty",
        "medium",
        "--runs",
        "1",
        "--cooldown-sec",
        str(cooldown_sec),
        "--show-max",
        "--record",
    ]
    cmd.extend(config.to_cli())

    completed = subprocess.run(cmd, cwd=str(BOT_ROOT), capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError("Live run failed:\n" + "\n".join(stderr))

    after = _existing_run_dirs()
    new_runs = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    run_dir = new_runs[0] if new_runs else _latest_run_dir()
    result_path = run_dir / "result.json"
    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    max_info = result_payload.get("max_score_info", {})

    result = AttemptResult(
        config=config,
        run_dir=str(run_dir),
        score=int(result_payload["score"]),
        items_delivered=int(result_payload.get("items_delivered", 0)),
        orders_completed=int(result_payload.get("orders_completed", 0)),
        max_score_exact=result_payload.get("max_score_exact"),
        max_score_upper_bound=int(result_payload.get("max_score_upper_bound", 0)),
        total_orders=int(max_info.get("total_orders", 50)),
    )

    if not keep_artifacts and run_dir in new_runs:
        shutil.rmtree(run_dir, ignore_errors=True)

    return result


def _candidate_space() -> list[CandidateConfig]:
    lookaheads = [2, 1, 3, 4, 5, 6]
    active_weights = [10.0, 12.0, 8.0]
    preview_weights = [4.0, 2.0, 6.0]
    dropoff_thresholds = [0.67, 0.8, 1.0]
    collision_modes = ["wait", "detour"]
    seeds = [0, 1, 2]

    configs: list[CandidateConfig] = []
    for la, aw, pw, dt, cm, seed in itertools.product(
        lookaheads,
        active_weights,
        preview_weights,
        dropoff_thresholds,
        collision_modes,
        seeds,
    ):
        configs.append(
            CandidateConfig(
                lookahead_k=la,
                active_weight=aw,
                preview_weight=pw,
                dropoff_threshold=dt,
                collision_aggressiveness=cm,
                seed=seed,
            )
        )
    return configs


def _save_best_config(result: AttemptResult) -> None:
    BEST_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BEST_CONFIG_PATH.write_text(
        json.dumps(
            {
                "difficulty": "medium",
                "config": result.config.to_dict(),
                "score": result.score,
                "items_delivered": result.items_delivered,
                "orders_completed": result.orders_completed,
                "updated_at": datetime.now().isoformat(),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )


def _save_autotune_report(attempts: list[AttemptResult], best: AttemptResult) -> Path:
    AUTOTUNE_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = AUTOTUNE_ARTIFACT_ROOT / f"autotune_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "config": r.config.to_dict(),
                        "score": r.score,
                        "items_delivered": r.items_delivered,
                        "orders_completed": r.orders_completed,
                        "max_score_exact": r.max_score_exact,
                        "max_score_upper_bound": r.max_score_upper_bound,
                        "run_dir": r.run_dir,
                    }
                    for r in attempts
                ],
                "best": {
                    "config": best.config.to_dict(),
                    "score": best.score,
                    "run_dir": best.run_dir,
                },
                "best_config_path": str(BEST_CONFIG_PATH),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    return path


def main() -> None:
    args = parse_args()
    if args.max_runs < 1:
        raise SystemExit("--max-runs must be >= 1")

    candidates = _candidate_space()
    attempts: list[AttemptResult] = []

    baseline = candidates[0]
    print("[autotune] baseline run...")
    baseline_result = _run_once(baseline, cooldown_sec=args.cooldown_sec, keep_artifacts=bool(args.record))
    attempts.append(baseline_result)
    best = baseline_result
    _save_best_config(best)
    print(f"[autotune] baseline score={baseline_result.score} run={baseline_result.run_dir}")

    stale_attempts = 0
    for idx, config in enumerate(candidates[1:], start=2):
        if len(attempts) >= args.max_runs:
            break
        time.sleep(max(0.0, args.cooldown_sec))
        result = _run_once(config, cooldown_sec=args.cooldown_sec, keep_artifacts=bool(args.record))
        attempts.append(result)

        improved = result.score > best.score
        if improved:
            best = result
            _save_best_config(best)
            stale_attempts = 0
            marker = "NEW_BEST"
        else:
            stale_attempts += 1
            marker = "-"

        print(
            f"[autotune] run={len(attempts):02d} score={result.score:3d} "
            f"orders={result.orders_completed:2d} items={result.items_delivered:2d} {marker}"
        )

        # Stop on exact max.
        if best.max_score_exact is not None and best.score >= best.max_score_exact:
            print(f"[autotune] stop: reached exact max score {best.max_score_exact}")
            break

        # Stop on current upper bound with all orders completed.
        if best.score >= best.max_score_upper_bound and best.orders_completed >= best.total_orders:
            print("[autotune] stop: reached score upper bound with all orders completed")
            break

        if stale_attempts >= args.plateau_attempts:
            print(f"[autotune] stop: no improvement for {args.plateau_attempts} attempts")
            break

    attempts_sorted = sorted(attempts, key=lambda r: (-r.score, -r.orders_completed, -r.items_delivered))
    top5 = attempts_sorted[:5]
    report_path = _save_autotune_report(attempts, best)

    print("\n[autotune] final summary")
    print(f"best_score={best.score}")
    if best.max_score_exact is not None:
        print(f"max_score={best.max_score_exact} (exact)")
    else:
        print(f"max_score_upper_bound={best.max_score_upper_bound}")
    print(f"best_config_path={BEST_CONFIG_PATH}")
    print("top5:")
    for rank, entry in enumerate(top5, start=1):
        print(f"  {rank}. score={entry.score} config={entry.config.to_dict()}")
    print(f"autotune_artifact={report_path}")


if __name__ == "__main__":
    main()




