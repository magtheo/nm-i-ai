"""Offline tuner for Orbit-Wall conveyor on Expert map using live-mined data.

Two-gate validation:
1) Gate A: weighted known-order synthesis + mined forecast.
2) Gate B: latest known-order synthesis + live forecast.

The tuner mines live artifacts, applies per-seed item-type shuffling, and
searches Orbit-Wall params for robust throughput/stability.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
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

from bot._simulator import (
    LocalMediumGame,
    default_generator_from_dataset,
    load_dataset_snapshot,
    mine_dataset,
    save_dataset_snapshot,
    synthesize_items,
    synthesize_orders,
)


RUNNER_PATH = BOT_ROOT / "scripts" / "run_nmiai_grocery_bot.py"
SPEC = importlib.util.spec_from_file_location("orbit_wall_runner_for_tune", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load runner module from {RUNNER_PATH}")
RUNNER_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules["orbit_wall_runner_for_tune"] = RUNNER_MODULE
SPEC.loader.exec_module(RUNNER_MODULE)

WallOrbitEngine = RUNNER_MODULE.WallOrbitEngine
ORBIT_DEFAULT_SHELF_IDS = tuple(RUNNER_MODULE.ORBIT_DEFAULT_SHELF_IDS)
ORBIT_FIXED_BRANCH_EXIT = tuple(RUNNER_MODULE.ORBIT_FIXED_BRANCH_EXIT)
ORBIT_FIXED_BRANCH_CONTINUE = tuple(RUNNER_MODULE.ORBIT_FIXED_BRANCH_CONTINUE)
ORBIT_FIXED_DELIVERY_ENTRY = tuple(RUNNER_MODULE.ORBIT_FIXED_DELIVERY_ENTRY)
ORBIT_FIXED_REJOIN_BRANCH = tuple(RUNNER_MODULE.ORBIT_FIXED_REJOIN_BRANCH)
ORBIT_FIXED_DROPOFF = tuple(RUNNER_MODULE.ORBIT_FIXED_DROPOFF)
ORBIT_FIXED_RETURN_BUFFER = tuple(tuple(cell) for cell in RUNNER_MODULE.ORBIT_FIXED_RETURN_BUFFER)


def _seed_list(raw: str, *, default_start: int, count: int) -> list[int]:
    text = str(raw or "").strip()
    if text:
        out: list[int] = []
        for tok in text.split(","):
            tok = tok.strip()
            if not tok:
                continue
            out.append(int(tok))
        if out:
            return out
    return [default_start + i for i in range(max(1, int(count)))]


@dataclass(frozen=True)
class OrbitTuneParams:
    reservation_horizon: int
    migration_stage: int
    delivery_quota_min: int
    delivery_quota_max: int
    forecast_buffer_cap: int
    rejoin_slot_headroom: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunMetrics:
    score: int
    items_delivered: int
    orders_completed: int
    rounds_used: int
    idle_steps: int
    spacing_wait: float
    collision_wait: float
    no_assign_wait: float
    queue_violation: float
    rejoin_denials: float
    rejoin_backlog: float
    branch_exit_visits: float
    branch_to_delivery: float
    deliver_bots_avg: float
    drop_off_actions: int


def _forecast_for_mode(
    *,
    forecast_mode: str,
    dataset: Any,
    orders: list[dict[str, Any]],
) -> dict[int, list[str]]:
    mode = str(forecast_mode or "mined").strip().lower()
    if mode == "live":
        return {}
    if mode == "oracle":
        return {idx: list(order["items_required"]) for idx, order in enumerate(orders)}
    return {idx: list(req) for idx, req in enumerate(dataset.observed_orders_exact)}


def simulate_orbit_wall_once(
    *,
    dataset: Any,
    params: OrbitTuneParams,
    seed: int,
    known_orders_mode: str,
    item_type_shuffle_prob: float,
    forecast_mode: str,
) -> RunMetrics:
    generator = default_generator_from_dataset(
        dataset,
        seed=int(seed),
        known_orders_mode=str(known_orders_mode),
        item_type_shuffle_prob=float(item_type_shuffle_prob),
    )
    orders = synthesize_orders(dataset, generator)
    items = synthesize_items(dataset, generator)
    forecast = _forecast_for_mode(forecast_mode=forecast_mode, dataset=dataset, orders=orders)

    game = LocalMediumGame(dataset, orders, items=items)
    engine = WallOrbitEngine(
        debug=False,
        reservation_horizon=max(1, int(params.reservation_horizon)),
        shelf_ids=ORBIT_DEFAULT_SHELF_IDS,
        migration_stage=max(0, int(params.migration_stage)),
        branch_exit=ORBIT_FIXED_BRANCH_EXIT,
        branch_continue=ORBIT_FIXED_BRANCH_CONTINUE,
        delivery_entry=ORBIT_FIXED_DELIVERY_ENTRY,
        rejoin_branch=ORBIT_FIXED_REJOIN_BRANCH,
        dropoff_override=ORBIT_FIXED_DROPOFF,
        return_buffer_cells=ORBIT_FIXED_RETURN_BUFFER,
        delivery_quota_min=max(0, int(params.delivery_quota_min)),
        delivery_quota_max=max(0, int(params.delivery_quota_max)),
        forecast_buffer_cap=max(0, int(params.forecast_buffer_cap)),
        rejoin_slot_headroom=max(0, int(params.rejoin_slot_headroom)),
        order_forecast=forecast,
    )

    idle_steps_total = 0
    spacing_wait = 0.0
    collision_wait = 0.0
    no_assign_wait = 0.0
    queue_violation = 0.0
    rejoin_denials = 0.0
    rejoin_backlog_total = 0.0
    branch_exit_visits = 0.0
    branch_to_delivery = 0.0
    deliver_bots_total = 0.0
    drop_off_actions = 0

    while not game.game_over:
        state = game.get_state()
        actions = engine.decide(state)
        step = game.step(actions)
        idle_steps_total += int(sum(step["idle_steps_by_bot"].values()))
        drop_off_actions += sum(1 for action in actions.actions if str(action.action.value) == "drop_off")

        telem = dict(getattr(engine, "last_round_telemetry", {}) or {})
        spacing_wait += float(telem.get("wait_due_to_spacing_guard", 0.0))
        collision_wait += float(telem.get("wait_due_to_collision_block", 0.0))
        no_assign_wait += float(telem.get("wait_due_to_no_assignment", 0.0))
        queue_violation += float(telem.get("queue_semantics_violation", 0.0))
        rejoin_denials += float(telem.get("rejoin_denials", 0.0))
        rejoin_backlog_total += float(telem.get("rejoin_backlog", 0.0))
        branch_exit_visits += float(telem.get("branch_exit_visits", 0.0))
        branch_to_delivery += float(telem.get("branch_to_delivery", 0.0))
        deliver_bots_total += float(telem.get("deliver_bots", 0.0))

    rounds_used = int(game.round)
    rounds_norm = float(max(1, rounds_used))
    return RunMetrics(
        score=int(game.score),
        items_delivered=int(game.items_delivered),
        orders_completed=int(game.orders_completed),
        rounds_used=rounds_used,
        idle_steps=int(idle_steps_total),
        spacing_wait=float(spacing_wait),
        collision_wait=float(collision_wait),
        no_assign_wait=float(no_assign_wait),
        queue_violation=float(queue_violation),
        rejoin_denials=float(rejoin_denials),
        rejoin_backlog=float(rejoin_backlog_total / rounds_norm),
        branch_exit_visits=float(branch_exit_visits),
        branch_to_delivery=float(branch_to_delivery),
        deliver_bots_avg=float(deliver_bots_total / rounds_norm),
        drop_off_actions=int(drop_off_actions),
    )


def aggregate_runs(rows: list[RunMetrics]) -> dict[str, float]:
    if not rows:
        return {
            "runs": 0.0,
            "mean_score": 0.0,
            "min_score": 0.0,
            "mean_items": 0.0,
            "mean_orders": 0.0,
            "mean_idle": 0.0,
            "mean_spacing_wait": 0.0,
            "mean_collision_wait": 0.0,
            "mean_no_assign_wait": 0.0,
            "mean_queue_violation": 0.0,
            "mean_rejoin_denials": 0.0,
            "mean_rejoin_backlog": 0.0,
            "mean_branch_rate": 0.0,
            "mean_deliver_bots": 0.0,
            "mean_drop_off_actions": 0.0,
        }
    mean_score = statistics.mean(r.score for r in rows)
    mean_items = statistics.mean(r.items_delivered for r in rows)
    mean_orders = statistics.mean(r.orders_completed for r in rows)
    mean_idle = statistics.mean(r.idle_steps for r in rows)
    mean_spacing = statistics.mean(r.spacing_wait for r in rows)
    mean_collision = statistics.mean(r.collision_wait for r in rows)
    mean_no_assign = statistics.mean(r.no_assign_wait for r in rows)
    mean_queue_violation = statistics.mean(r.queue_violation for r in rows)
    mean_rejoin_denials = statistics.mean(r.rejoin_denials for r in rows)
    mean_rejoin_backlog = statistics.mean(r.rejoin_backlog for r in rows)
    mean_branch_rate = statistics.mean(
        (r.branch_to_delivery / max(1.0, r.branch_exit_visits)) for r in rows
    )
    mean_deliver_bots = statistics.mean(r.deliver_bots_avg for r in rows)
    mean_drop_actions = statistics.mean(r.drop_off_actions for r in rows)
    return {
        "runs": float(len(rows)),
        "mean_score": float(mean_score),
        "min_score": float(min(r.score for r in rows)),
        "mean_items": float(mean_items),
        "mean_orders": float(mean_orders),
        "mean_idle": float(mean_idle),
        "mean_spacing_wait": float(mean_spacing),
        "mean_collision_wait": float(mean_collision),
        "mean_no_assign_wait": float(mean_no_assign),
        "mean_queue_violation": float(mean_queue_violation),
        "mean_rejoin_denials": float(mean_rejoin_denials),
        "mean_rejoin_backlog": float(mean_rejoin_backlog),
        "mean_branch_rate": float(mean_branch_rate),
        "mean_deliver_bots": float(mean_deliver_bots),
        "mean_drop_off_actions": float(mean_drop_actions),
    }


def objective(primary: dict[str, float], secondary: dict[str, float]) -> float:
    robust_score = min(float(primary["mean_score"]), float(secondary["mean_score"]))
    robust_orders = min(float(primary["mean_orders"]), float(secondary["mean_orders"]))
    robust_items = min(float(primary["mean_items"]), float(secondary["mean_items"]))
    robust_drop_actions = min(float(primary["mean_drop_off_actions"]), float(secondary["mean_drop_off_actions"]))

    spacing_penalty = max(float(primary["mean_spacing_wait"]), float(secondary["mean_spacing_wait"]))
    rejoin_penalty = max(float(primary["mean_rejoin_denials"]), float(secondary["mean_rejoin_denials"]))
    queue_penalty = max(float(primary["mean_queue_violation"]), float(secondary["mean_queue_violation"]))
    branch_rate = min(float(primary["mean_branch_rate"]), float(secondary["mean_branch_rate"]))

    return (
        100.0 * robust_score
        + 35.0 * robust_orders
        + 10.0 * robust_items
        + 0.4 * robust_drop_actions
        + 8.0 * branch_rate
        - 0.04 * spacing_penalty
        - 0.08 * rejoin_penalty
        - 15.0 * queue_penalty
    )


def random_candidate(rng: random.Random) -> OrbitTuneParams:
    delivery_min = rng.choice([0, 1, 2])
    delivery_max = rng.choice([1, 2, 3, 4])
    if delivery_max < delivery_min:
        delivery_max = delivery_min
    return OrbitTuneParams(
        reservation_horizon=rng.choice([1, 2, 3]),
        migration_stage=rng.choice([5, 6]),
        delivery_quota_min=delivery_min,
        delivery_quota_max=delivery_max,
        forecast_buffer_cap=rng.choice([0, 1, 2, 3, 4]),
        rejoin_slot_headroom=rng.choice([1, 2, 3, 4]),
    )


def evaluate_candidate(
    *,
    dataset: Any,
    params: OrbitTuneParams,
    seeds: list[int],
    gate_a_shuffle: float,
    gate_b_shuffle: float,
    gate_a_known_orders_mode: str,
    gate_a_forecast_mode: str,
    gate_b_known_orders_mode: str,
    gate_b_forecast_mode: str,
) -> dict[str, Any]:
    gate_a_runs = [
        simulate_orbit_wall_once(
            dataset=dataset,
            params=params,
            seed=seed,
            known_orders_mode=gate_a_known_orders_mode,
            item_type_shuffle_prob=gate_a_shuffle,
            forecast_mode=gate_a_forecast_mode,
        )
        for seed in seeds
    ]
    gate_b_runs = [
        simulate_orbit_wall_once(
            dataset=dataset,
            params=params,
            seed=seed,
            known_orders_mode=gate_b_known_orders_mode,
            item_type_shuffle_prob=gate_b_shuffle,
            forecast_mode=gate_b_forecast_mode,
        )
        for seed in seeds
    ]
    gate_a = aggregate_runs(gate_a_runs)
    gate_b = aggregate_runs(gate_b_runs)
    return {
        "params": params.to_dict(),
        "gate_a": gate_a,
        "gate_b": gate_b,
        "objective": float(objective(gate_a, gate_b)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune orbit-wall conveyor policy on expert live-mined maps")
    parser.add_argument(
        "--artifact-root",
        type=str,
        default=".seed_artifacts/nmiai/live_screen_expert_orbit_collect_20260307/expert",
        help="Root containing run_* expert artifacts to mine",
    )
    parser.add_argument("--dataset-snapshot", type=str, default="", help="Optional prebuilt dataset snapshot path")
    parser.add_argument(
        "--write-dataset-snapshot",
        type=str,
        default="",
        help="Optional path to save mined dataset snapshot",
    )
    parser.add_argument("--train-seeds", type=str, default="")
    parser.add_argument("--holdout-seeds", type=str, default="")
    parser.add_argument("--train-count", type=int, default=4)
    parser.add_argument("--holdout-count", type=int, default=4)
    parser.add_argument("--seed-base", type=int, default=7400)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--attempts", type=int, default=24)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--gate-a-shuffle", type=float, default=0.20)
    parser.add_argument("--gate-b-shuffle", type=float, default=0.45)
    parser.add_argument("--gate-a-known-orders-mode", type=str, default="weighted")
    parser.add_argument("--gate-a-forecast-mode", type=str, default="mined")
    parser.add_argument("--gate-b-known-orders-mode", type=str, default="latest")
    parser.add_argument("--gate-b-forecast-mode", type=str, default="live")
    parser.add_argument(
        "--out-dir",
        type=str,
        default="artifacts/orbit_wall_expert_tune",
        help="Output directory for tuning artifacts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dataset_snapshot:
        dataset = load_dataset_snapshot(args.dataset_snapshot)
        dataset_source = f"snapshot:{args.dataset_snapshot}"
    else:
        dataset = mine_dataset(args.artifact_root, recent_run_window=200, top_score_runs=50)
        dataset_source = f"mine:{args.artifact_root}"
    if args.write_dataset_snapshot:
        saved = save_dataset_snapshot(dataset, args.write_dataset_snapshot, source=dataset_source)
        print(f"[orbit-tune] dataset snapshot written: {saved}")

    train_seeds = _seed_list(args.train_seeds, default_start=args.seed_base, count=args.train_count)
    holdout_seeds = _seed_list(args.holdout_seeds, default_start=args.seed_base + 100, count=args.holdout_count)
    rng = random.Random(int(args.random_seed))

    print(
        f"[orbit-tune] dataset source={dataset_source} "
        f"items={len(dataset.items)} bots={len(dataset.bot_starts)} "
        f"orders_observed={len(dataset.observed_orders_exact)} total_orders={dataset.total_orders}"
    )
    print(f"[orbit-tune] train_seeds={train_seeds} holdout_seeds={holdout_seeds}")
    print(
        "[orbit-tune] gate_a="
        f"{args.gate_a_known_orders_mode}/{args.gate_a_forecast_mode} shuffle={float(args.gate_a_shuffle):.2f} "
        "gate_b="
        f"{args.gate_b_known_orders_mode}/{args.gate_b_forecast_mode} shuffle={float(args.gate_b_shuffle):.2f}"
    )

    baseline = OrbitTuneParams(
        reservation_horizon=2,
        migration_stage=5,
        delivery_quota_min=1,
        delivery_quota_max=3,
        forecast_buffer_cap=2,
        rejoin_slot_headroom=2,
    )
    candidate_pool: list[OrbitTuneParams] = [baseline]
    for _ in range(max(0, int(args.attempts) - 1)):
        candidate_pool.append(random_candidate(rng))

    seen_key: set[tuple[Any, ...]] = set()
    unique_candidates: list[OrbitTuneParams] = []
    for row in candidate_pool:
        key = (
            row.reservation_horizon,
            row.migration_stage,
            row.delivery_quota_min,
            row.delivery_quota_max,
            row.forecast_buffer_cap,
            row.rejoin_slot_headroom,
        )
        if key in seen_key:
            continue
        seen_key.add(key)
        unique_candidates.append(row)

    coarse_results: list[dict[str, Any]] = []
    for idx, params in enumerate(unique_candidates, start=1):
        result = evaluate_candidate(
            dataset=dataset,
            params=params,
            seeds=train_seeds,
            gate_a_shuffle=float(args.gate_a_shuffle),
            gate_b_shuffle=float(args.gate_b_shuffle),
            gate_a_known_orders_mode=str(args.gate_a_known_orders_mode),
            gate_a_forecast_mode=str(args.gate_a_forecast_mode),
            gate_b_known_orders_mode=str(args.gate_b_known_orders_mode),
            gate_b_forecast_mode=str(args.gate_b_forecast_mode),
        )
        result["stage"] = "coarse"
        coarse_results.append(result)
        if idx % 4 == 0 or idx == len(unique_candidates):
            print(
                f"[orbit-tune] coarse {idx}/{len(unique_candidates)} "
                f"obj={result['objective']:.3f} params={result['params']}"
            )

    coarse_results.sort(key=lambda row: float(row["objective"]), reverse=True)
    top_k = max(1, min(int(args.top_k), len(coarse_results)))
    finalists = coarse_results[:top_k]

    final_results: list[dict[str, Any]] = []
    for idx, row in enumerate(finalists, start=1):
        params = OrbitTuneParams(**row["params"])
        holdout_eval = evaluate_candidate(
            dataset=dataset,
            params=params,
            seeds=holdout_seeds,
            gate_a_shuffle=float(args.gate_a_shuffle),
            gate_b_shuffle=float(args.gate_b_shuffle),
            gate_a_known_orders_mode=str(args.gate_a_known_orders_mode),
            gate_a_forecast_mode=str(args.gate_a_forecast_mode),
            gate_b_known_orders_mode=str(args.gate_b_known_orders_mode),
            gate_b_forecast_mode=str(args.gate_b_forecast_mode),
        )
        combined_objective = 0.6 * float(row["objective"]) + 0.4 * float(holdout_eval["objective"])
        payload = {
            "params": row["params"],
            "coarse": {
                "objective": float(row["objective"]),
                "gate_a": row["gate_a"],
                "gate_b": row["gate_b"],
            },
            "holdout": {
                "objective": float(holdout_eval["objective"]),
                "gate_a": holdout_eval["gate_a"],
                "gate_b": holdout_eval["gate_b"],
            },
            "combined_objective": float(combined_objective),
        }
        final_results.append(payload)
        print(
            f"[orbit-tune] holdout {idx}/{top_k} combined={combined_objective:.3f} "
            f"coarse={row['objective']:.3f} holdout={holdout_eval['objective']:.3f}"
        )

    final_results.sort(key=lambda row: float(row["combined_objective"]), reverse=True)
    best = final_results[0]

    generated_at = datetime.now().isoformat(timespec="seconds")
    summary = {
        "generated_at": generated_at,
        "dataset_source": dataset_source,
        "train_seeds": train_seeds,
        "holdout_seeds": holdout_seeds,
        "gate_a": {
            "known_orders_mode": str(args.gate_a_known_orders_mode),
            "forecast_mode": str(args.gate_a_forecast_mode),
            "shuffle": float(args.gate_a_shuffle),
        },
        "gate_b": {
            "known_orders_mode": str(args.gate_b_known_orders_mode),
            "forecast_mode": str(args.gate_b_forecast_mode),
            "shuffle": float(args.gate_b_shuffle),
        },
        "candidate_count": len(unique_candidates),
        "best": best,
        "top_finalists": final_results[:10],
    }

    best_params_path = out_dir / "best_orbit_wall_params.json"
    report_path = out_dir / "tuning_report.json"
    finalists_path = out_dir / "finalists.json"
    best_params_path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "params": best["params"],
                "recommended_live_cmd": (
                    "python scripts/run_nmiai_grocery_bot.py --difficulty expert --runs 1 "
                    "--record --record-order-trace --record-decision-trace --orbit-wall "
                    "--orbit-branch-exit 8,15 --orbit-branch-continue 7,15 "
                    "--orbit-delivery-entry 8,16 --orbit-rejoin-branch 3,15 "
                    "--orbit-dropoff 1,16 --orbit-return-buffer-cells \"3,16;2,16\" "
                    f"--reservation-horizon {int(best['params']['reservation_horizon'])} "
                    f"--orbit-migration-stage {int(best['params']['migration_stage'])} "
                    f"--orbit-delivery-quota-min {int(best['params']['delivery_quota_min'])} "
                    f"--orbit-delivery-quota-max {int(best['params']['delivery_quota_max'])} "
                    f"--orbit-forecast-buffer-cap {int(best['params']['forecast_buffer_cap'])} "
                    f"--orbit-rejoin-slot-headroom {int(best['params']['rejoin_slot_headroom'])}"
                ),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    finalists_path.write_text(json.dumps(final_results, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"[orbit-tune] best params: {best['params']}")
    print(f"[orbit-tune] best combined objective: {best['combined_objective']:.3f}")
    print(f"[orbit-tune] wrote {best_params_path}")
    print(f"[orbit-tune] wrote {report_path}")
    print(f"[orbit-tune] wrote {finalists_path}")


if __name__ == "__main__":
    main()
