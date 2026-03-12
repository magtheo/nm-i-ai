"""Autotune live strategy parameters for Expert difficulty."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
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
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))


RUN_SCRIPT = BOT_ROOT / "scripts" / "run_nmiai_grocery_bot.py"
RUN_ARTIFACT_ROOT = BOT_ROOT / ".seed_artifacts" / "nmiai" / "expert"
AUTOTUNE_ARTIFACT_ROOT = RUN_ARTIFACT_ROOT / "autotune"
BEST_CONFIG_PATH = BOT_ROOT / "app" / "integrations" / "nmiai_grocery_bot" / "best_configs" / "expert.json"
TOKEN_ENV_KEY = "AINM_ACCESS_TOKEN"


@dataclass(frozen=True)
class CandidateConfig:
    lookahead_k: int
    active_weight: float
    preview_weight: float
    dropoff_threshold: float
    collision_aggressiveness: str
    max_concurrent_deliverers: int | None
    seed: int

    def to_cli(self, supported_flags: set[str]) -> list[str]:
        cli: list[str] = []
        mapping: list[tuple[str, str | int | float | None]] = [
            ("--lookahead-k", self.lookahead_k),
            ("--active-weight", self.active_weight),
            ("--preview-weight", self.preview_weight),
            ("--dropoff-threshold", self.dropoff_threshold),
            ("--collision-aggressiveness", self.collision_aggressiveness),
            ("--max-concurrent-deliverers", self.max_concurrent_deliverers),
            ("--seed", self.seed),
        ]
        for flag, value in mapping:
            if value is None:
                continue
            if flag not in supported_flags:
                continue
            cli.extend([flag, str(value)])
        return cli

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


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
    parser = argparse.ArgumentParser(description="Autotune NMiAI Grocery Bot on Expert")
    parser.add_argument("--max-runs", type=int, default=40, help="Maximum live runs including baseline")
    parser.add_argument("--cooldown-sec", type=float, default=3.0, help="Cooldown between attempts")
    parser.add_argument("--plateau-attempts", type=int, default=12, help="Stop after this many non-improving attempts")
    parser.add_argument("--record", action="store_true", help="Keep per-run artifacts from the live runner")
    parser.add_argument("--target-score", type=int, default=180, help="Optional score target for early stop (<=0 disables)")
    return parser.parse_args()


def _strip_access_token_prefix(value: str) -> str:
    text = value.strip().strip('"').strip("'")
    if text.startswith("access_token="):
        return text.split("=", 1)[1]
    return text


def _load_token_from_dotenv(env_path: Path, *, env_key: str = TOKEN_ENV_KEY) -> str | None:
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export "):].strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() != env_key:
            continue
        token = _strip_access_token_prefix(value)
        return token or None
    return None


def _mask_suffix(token: str) -> str:
    if len(token) < 4:
        return "****"
    return f"****{token[-4:]}"


def _validate_token_consistency() -> bool:
    env_raw = os.getenv(TOKEN_ENV_KEY)
    env_token = _strip_access_token_prefix(env_raw) if env_raw else None
    dotenv_token = _load_token_from_dotenv(BOT_ROOT / ".env")

    env_present = bool(env_token)
    dotenv_present = bool(dotenv_token)
    print(f"[autotune] token present in env: {'yes' if env_present else 'no'}")
    print(f"[autotune] token present in .env: {'yes' if dotenv_present else 'no'}")

    if not dotenv_present:
        print("[autotune] warning: token missing in .env; refusing live runs.")
        return False

    if env_present:
        print(f"[autotune] token suffix env={_mask_suffix(env_token)} .env={_mask_suffix(dotenv_token)}")
        if env_token != dotenv_token:
            print("[autotune] warning: token mismatch between env and .env; refusing live runs.")
            return False
    else:
        # Keep live runner token source consistent with .env for this process.
        os.environ[TOKEN_ENV_KEY] = dotenv_token
        print(f"[autotune] token suffix .env={_mask_suffix(dotenv_token)}")
        print("[autotune] info: loaded token from .env into process env for live runs.")

    return True


def _runner_supported_flags() -> set[str]:
    completed = subprocess.run(
        [sys.executable, str(RUN_SCRIPT), "--help"],
        cwd=str(BOT_ROOT),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError("Failed to inspect runner help:\n" + "\n".join(stderr))

    text = f"{completed.stdout}\n{completed.stderr}".lower()
    return set(re.findall(r"--[a-z0-9][a-z0-9-]*", text))


def _existing_run_dirs() -> set[Path]:
    if not RUN_ARTIFACT_ROOT.exists():
        return set()
    return {p for p in RUN_ARTIFACT_ROOT.iterdir() if p.is_dir() and p.name.startswith("run_")}


def _latest_run_dir() -> Path:
    runs = [p for p in RUN_ARTIFACT_ROOT.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not runs:
        raise RuntimeError("No run artifacts found under .seed_artifacts/nmiai/expert")
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[0]


def _run_once(
    config: CandidateConfig,
    *,
    cooldown_sec: float,
    supported_flags: set[str],
    record: bool,
) -> AttemptResult:
    before = _existing_run_dirs()
    cmd = [
        sys.executable,
        str(RUN_SCRIPT),
        "--difficulty",
        "expert",
        "--runs",
        "1",
        "--cooldown-sec",
        str(cooldown_sec),
        "--show-max",
    ]
    if record:
        cmd.append("--record")
    cmd.extend(config.to_cli(supported_flags))

    completed = subprocess.run(cmd, cwd=str(BOT_ROOT), capture_output=True, text=True)
    if completed.returncode != 0:
        snippet = "\n".join(((completed.stderr or "") + "\n" + (completed.stdout or "")).strip().splitlines()[-10:])
        raise RuntimeError("Live run failed:\n" + snippet)

    if not record:
        raise RuntimeError("Autotune requires --record to read run artifacts")

    after = _existing_run_dirs()
    new_runs = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    run_dir = new_runs[0] if new_runs else _latest_run_dir()
    result_path = run_dir / "result.json"
    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    max_info = result_payload.get("max_score_info", {})

    return AttemptResult(
        config=config,
        run_dir=str(run_dir),
        score=int(result_payload["score"]),
        items_delivered=int(result_payload.get("items_delivered", 0)),
        orders_completed=int(result_payload.get("orders_completed", 0)),
        max_score_exact=result_payload.get("max_score_exact"),
        max_score_upper_bound=int(result_payload.get("max_score_upper_bound", 0)),
        total_orders=int(max_info.get("total_orders", 50)),
    )


def _candidate_space(supported_flags: set[str]) -> list[CandidateConfig]:
    lookaheads = [2, 3, 4, 5, 1, 6, 7, 8]
    active_weights = [10.0, 12.0, 8.0]
    preview_weights = [2.0, 4.0, 6.0]
    dropoff_thresholds = [0.67, 0.8, 0.9, 1.0]
    collision_modes = ["wait", "detour"] if "--collision-aggressiveness" in supported_flags else ["wait"]
    seeds = [0, 1]
    if "--max-concurrent-deliverers" in supported_flags:
        concurrent_deliverers: list[int | None] = [0, 2, 3, 1, 4]
    else:
        concurrent_deliverers = [None]

    configs: list[CandidateConfig] = []
    for la, aw, pw, dt, cm, deliverers, seed in itertools.product(
        lookaheads,
        active_weights,
        preview_weights,
        dropoff_thresholds,
        collision_modes,
        concurrent_deliverers,
        seeds,
    ):
        configs.append(
            CandidateConfig(
                lookahead_k=la,
                active_weight=aw,
                preview_weight=pw,
                dropoff_threshold=dt,
                collision_aggressiveness=cm,
                max_concurrent_deliverers=deliverers,
                seed=seed,
            )
        )

    def sort_key(cfg: CandidateConfig) -> tuple[float, ...]:
        deliverer_delta = 0.0 if cfg.max_concurrent_deliverers is None else abs(cfg.max_concurrent_deliverers - 0) * 0.2
        return (
            abs(cfg.lookahead_k - 4),
            abs(cfg.active_weight - 10.0),
            abs(cfg.preview_weight - 4.0),
            abs(cfg.dropoff_threshold - 0.8),
            0.0 if cfg.collision_aggressiveness == "wait" else 0.2,
            deliverer_delta,
            float(cfg.seed),
        )

    configs.sort(key=sort_key)
    return configs


def _save_best_config(result: AttemptResult) -> None:
    BEST_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BEST_CONFIG_PATH.write_text(
        json.dumps(
            {
                "difficulty": "expert",
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
    path = AUTOTUNE_ARTIFACT_ROOT / f"autotune_expert_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(
        json.dumps(
            {
                "difficulty": "expert",
                "attempts": [
                    {
                        "config": entry.config.to_dict(),
                        "score": entry.score,
                        "items_delivered": entry.items_delivered,
                        "orders_completed": entry.orders_completed,
                        "max_score_exact": entry.max_score_exact,
                        "max_score_upper_bound": entry.max_score_upper_bound,
                        "run_dir": entry.run_dir,
                    }
                    for entry in attempts
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
    if not _validate_token_consistency():
        raise SystemExit(2)
    if args.max_runs < 1:
        raise SystemExit("--max-runs must be >= 1")
    if args.cooldown_sec < 0:
        raise SystemExit("--cooldown-sec must be >= 0")
    if args.plateau_attempts < 1:
        raise SystemExit("--plateau-attempts must be >= 1")
    if not args.record:
        print("[autotune] warning: --record was not provided; enabling it for artifact-based evaluation.")
    record = True

    supported_flags = _runner_supported_flags()
    required = {"--difficulty", "--runs", "--show-max", "--record"}
    missing = sorted(required - supported_flags)
    if missing:
        raise RuntimeError(f"Runner is missing required flags: {missing}")

    candidates = _candidate_space(supported_flags)
    attempts: list[AttemptResult] = []

    baseline = candidates[0]
    print("[autotune] baseline run...")
    baseline_result = _run_once(
        baseline,
        cooldown_sec=args.cooldown_sec,
        supported_flags=supported_flags,
        record=record,
    )
    attempts.append(baseline_result)
    best = baseline_result
    _save_best_config(best)
    print(f"[autotune] baseline score={baseline_result.score} run={baseline_result.run_dir}")

    stale_attempts = 0
    for config in candidates[1:]:
        if len(attempts) >= args.max_runs:
            break
        time.sleep(max(0.0, args.cooldown_sec))
        result = _run_once(
            config,
            cooldown_sec=args.cooldown_sec,
            supported_flags=supported_flags,
            record=record,
        )
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

        if args.target_score > 0 and best.score >= args.target_score:
            print(f"[autotune] stop: reached target score {args.target_score}")
            break
        if best.max_score_exact is not None and best.score >= best.max_score_exact:
            print(f"[autotune] stop: reached exact max score {best.max_score_exact}")
            break
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
