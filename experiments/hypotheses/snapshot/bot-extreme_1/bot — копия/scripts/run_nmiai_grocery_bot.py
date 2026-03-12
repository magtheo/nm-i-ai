"""Live runner for NMiAI Grocery Bot with artifact recording."""
from __future__ import annotations

import argparse
import asyncio
import json
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

from bot.client import GameWSClient
from bot.decision_engine import DecisionConfig, DecisionEngine
from bot.endpoint import GameSession, redact_ws_url, request_game_session
from bot.max_score import OrderTracker, max_score_for_game
from bot.models import BotAction, GameOver, GameState
from bot.telemetry import RoundLogger


DEFAULT_MAX_LIVE_RUNS = 30


@dataclass
class LiveRunSummary:
    run_index: int
    score: int
    items_delivered: int
    orders_completed: int
    rounds_played: int
    idle_steps: int
    collisions_avoided: int
    avg_decision_ms: float
    max_score_exact: int | None
    max_score_upper_bound: int
    max_score_lower_bound: int
    all_orders_observed: bool
    artifact_dir: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NMiAI Grocery Bot live runner")
    parser.add_argument("--difficulty", type=str, default="medium", choices=["easy", "medium", "hard", "expert"])
    parser.add_argument("--runs", type=int, default=1, help="Number of live sessions to run")
    parser.add_argument("--cooldown-sec", type=float, default=3.0, help="Cooldown between runs")
    parser.add_argument("--max-live-runs", type=int, default=DEFAULT_MAX_LIVE_RUNS, help="Safety cap for live runs")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic tie-break seed (0 keeps strict deterministic ordering)")
    parser.add_argument("--show-max", action="store_true", help="Print max-score exact value or bound from round 0")
    parser.add_argument("--record", action="store_true", help="Write run artifacts under .seed_artifacts/")
    parser.add_argument(
        "--record-order-trace",
        action="store_true",
        help="Record compact per-round order snapshots to artifact",
    )
    parser.add_argument(
        "--save-states",
        action="store_true",
        help="Store full game state in round logs (large files)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose client/engine logging")
    parser.add_argument(
        "--params-file",
        type=str,
        default="",
        help="Optional JSON file with DecisionConfig params to override CLI strategy flags",
    )
    parser.add_argument("--use-astar", action="store_true", help="Use A* for movement pathfinding")
    parser.add_argument("--lookahead-k", type=int, default=2, help="Order lookahead depth")
    parser.add_argument("--active-weight", type=float, default=10.0, help="Active-order utility weight")
    parser.add_argument("--preview-weight", type=float, default=4.0, help="Preview-order utility weight")
    parser.add_argument(
        "--zone-penalty",
        type=float,
        default=0.0,
        help="Extra distance penalty to keep each bot near its lane/zone",
    )
    parser.add_argument(
        "--dropoff-threshold",
        type=float,
        default=0.67,
        help="Fraction of active order completion that triggers delivery priority",
    )
    parser.add_argument(
        "--collision-aggressiveness",
        type=str,
        default="wait",
        choices=["wait", "detour"],
        help="Collision fallback behavior for blocked bots",
    )
    parser.add_argument("--dist-weight", type=float, default=1.0, help="Distance penalty weight bot->pickup")
    parser.add_argument("--dropoff-dist-weight", type=float, default=0.35, help="Distance penalty weight pickup->dropoff")
    parser.add_argument("--congestion-weight", type=float, default=1.0, help="Congestion penalty weight")
    parser.add_argument("--collision-risk-weight", type=float, default=1.0, help="Collision-risk penalty weight")
    parser.add_argument("--replan-penalty-weight", type=float, default=1.0, help="Target switch (replan) penalty weight")
    parser.add_argument(
        "--carry-home-bias-weight",
        type=float,
        default=0.0,
        help="Penalty for continuing to pick while already carrying active-matching items far from drop-off",
    )
    parser.add_argument("--urgency-weight", type=float, default=1.0, help="Urgency multiplier for target utility")
    parser.add_argument(
        "--trip-chain-bonus-weight",
        type=float,
        default=0.0,
        help="Bonus for pickups that enable a short second active-item pickup before drop-off",
    )
    parser.add_argument("--future-depth-decay", type=float, default=1.0, help="Future-order utility decay by depth")
    parser.add_argument(
        "--future-count-weight",
        type=float,
        default=0.0,
        help="Additional utility per outstanding future demand count for a prefetch item type",
    )
    parser.add_argument("--future-prefetch-bonus", type=int, default=0, help="Extra prefetch slot budget in oracle mode")
    parser.add_argument(
        "--future-priority-mode",
        type=str,
        default="depth",
        choices=["depth", "flat"],
        help="Future-order rank priority mode",
    )
    parser.add_argument(
        "--prefetch-min-completion",
        type=float,
        default=0.0,
        help="Minimum active-order completion ratio before prefetch is allowed",
    )
    parser.add_argument(
        "--prefetch-spare-slots",
        type=int,
        default=0,
        help="Global spare inventory slots kept free while active order is incomplete",
    )
    parser.add_argument(
        "--prefetch-nonmatching-cap",
        type=int,
        default=3,
        help="Max non-matching items a bot may hold while still prefetching",
    )
    parser.add_argument(
        "--strict-active-priority",
        dest="strict_active_priority",
        action="store_true",
        default=False,
        help="If active targets are reachable, avoid preview prefetch assignments",
    )
    parser.add_argument(
        "--disable-strict-active-priority",
        dest="strict_active_priority",
        action="store_false",
        help="Allow preview prefetch even while active targets are reachable",
    )
    parser.add_argument(
        "--strict-active-release-completion",
        type=float,
        default=1.0,
        help="When strict-active mode is on, allow prefetch once active completion reaches this ratio",
    )
    parser.add_argument(
        "--force-dropoff-for-full-nonmatching",
        dest="force_dropoff_for_full_nonmatching",
        action="store_true",
        default=False,
        help="If bot inventory is full with non-matching items, force it to stage at drop-off",
    )
    parser.add_argument(
        "--disable-force-dropoff-for-full-nonmatching",
        dest="force_dropoff_for_full_nonmatching",
        action="store_false",
        help="Disable drop-off staging for full non-matching inventory",
    )
    parser.add_argument(
        "--always-deliver-matching",
        dest="always_deliver_matching",
        action="store_true",
        default=False,
        help="Always route bots with active-matching items to drop-off",
    )
    parser.add_argument(
        "--disable-always-deliver-matching",
        dest="always_deliver_matching",
        action="store_false",
        help="Allow bots to keep collecting before delivering matching items",
    )
    parser.add_argument(
        "--avoid-dropoff-block-when-matching",
        dest="avoid_dropoff_block_when_matching",
        action="store_true",
        default=True,
        help="Keep full non-matching bots away from drop-off if teammates can deliver active items",
    )
    parser.add_argument(
        "--disable-avoid-dropoff-block-when-matching",
        dest="avoid_dropoff_block_when_matching",
        action="store_false",
        help="Allow full non-matching bots to stage at drop-off even if teammates have matching items",
    )
    parser.add_argument(
        "--max-concurrent-deliverers",
        type=int,
        default=2,
        help="Limit bots simultaneously assigned to deliver each tick (<=0 means unlimited)",
    )
    parser.add_argument(
        "--adaptive-deliver-queue",
        dest="adaptive_deliver_queue",
        action="store_true",
        default=False,
        help="Adapt deliver queue size to active-order progress and endgame pressure",
    )
    parser.add_argument(
        "--disable-adaptive-deliver-queue",
        dest="adaptive_deliver_queue",
        action="store_false",
        help="Use fixed max-concurrent-deliverers without adaptive queue sizing",
    )
    parser.add_argument("--deliver-queue-min", type=int, default=1, help="Minimum adaptive delivery queue size")
    parser.add_argument("--deliver-queue-max", type=int, default=3, help="Maximum adaptive delivery queue size")
    parser.add_argument(
        "--assignment-strategy",
        type=str,
        default="greedy",
        choices=["greedy", "auction"],
        help="Item assignment strategy",
    )
    parser.add_argument("--reservation-horizon", type=int, default=2, help="Reservation horizon for collision handling")
    parser.add_argument("--hysteresis-penalty", type=float, default=2.0, help="Anti-oscillation hysteresis penalty")
    parser.add_argument(
        "--sticky-target-bonus",
        type=float,
        default=0.0,
        help="Utility bonus for keeping the same target item id as previous tick",
    )
    parser.add_argument(
        "--early-deliver-matching-count",
        type=int,
        default=0,
        help="If >0, allow early deliver when bot has at least this many active-matching items",
    )
    parser.add_argument(
        "--early-deliver-inventory-threshold",
        type=int,
        default=2,
        help="Minimum inventory size required for early-deliver rule",
    )
    parser.add_argument(
        "--endgame-disable-prefetch-rounds",
        type=int,
        default=0,
        help="Rounds-to-end threshold where preview prefetch is disabled",
    )
    parser.add_argument(
        "--endgame-force-deliver-rounds",
        type=int,
        default=0,
        help="Rounds-to-end threshold where bots with matching items prioritize delivery",
    )
    parser.add_argument(
        "--endgame-strict-active",
        dest="endgame_strict_active",
        action="store_true",
        default=False,
        help="Force strict active-order priority in endgame window",
    )
    parser.add_argument(
        "--disable-endgame-strict-active",
        dest="endgame_strict_active",
        action="store_false",
        help="Disable strict active-order priority in endgame window",
    )
    parser.add_argument(
        "--avoid-immediate-backtrack",
        dest="avoid_immediate_backtrack",
        action="store_true",
        default=True,
        help="Avoid immediate A-B-A reversals when an alternative step is available",
    )
    parser.add_argument(
        "--disable-avoid-immediate-backtrack",
        dest="avoid_immediate_backtrack",
        action="store_false",
        help="Disable immediate backtrack guard",
    )
    parser.add_argument(
        "--backtrack-slack",
        type=int,
        default=1,
        help="Allowed extra distance when selecting non-backtracking alternative step",
    )
    parser.add_argument(
        "--wait-on-backtrack-conflict",
        dest="wait_on_backtrack_conflict",
        action="store_true",
        default=False,
        help="If no good non-backtracking move exists, wait instead of reversing",
    )
    parser.add_argument(
        "--disable-wait-on-backtrack-conflict",
        dest="wait_on_backtrack_conflict",
        action="store_false",
        help="Allow reverse step when no good alternative exists",
    )
    parser.add_argument(
        "--pickup-fail-blacklist-threshold",
        type=int,
        default=2,
        help="Number of failed pick_up attempts before item id is temporarily blacklisted",
    )
    parser.add_argument(
        "--pickup-fail-blacklist-rounds",
        type=int,
        default=40,
        help="Temporary blacklist duration (rounds) for repeatedly failing item ids",
    )
    parser.add_argument(
        "--stall-round-threshold",
        type=int,
        default=24,
        help="Rounds without active-order progress before entering recovery mode",
    )
    parser.add_argument(
        "--stall-recovery-rounds",
        type=int,
        default=40,
        help="Duration of recovery mode once triggered",
    )
    parser.add_argument(
        "--stall-recovery-preview-weight",
        type=float,
        default=0.0,
        help="Preview weight override in recovery mode",
    )
    parser.add_argument(
        "--stall-recovery-force-dropoff",
        dest="stall_recovery_force_dropoff",
        action="store_true",
        default=True,
        help="Force full non-matching bots to drop-off while in recovery mode",
    )
    parser.add_argument(
        "--disable-stall-recovery-force-dropoff",
        dest="stall_recovery_force_dropoff",
        action="store_false",
        help="Disable forced drop-off in recovery mode",
    )
    parser.add_argument(
        "--stall-recovery-strict-active",
        dest="stall_recovery_strict_active",
        action="store_true",
        default=True,
        help="Enforce strict active-order priority while in recovery mode",
    )
    parser.add_argument(
        "--disable-stall-recovery-strict-active",
        dest="stall_recovery_strict_active",
        action="store_false",
        help="Disable strict active-order priority in recovery mode",
    )
    parser.add_argument(
        "--clear-adjacent-dropoff-lane",
        dest="clear_adjacent_dropoff_lane",
        action="store_true",
        default=False,
        help="Move non-matching adjacent bots away from drop-off when carriers are approaching",
    )
    parser.add_argument(
        "--disable-clear-adjacent-dropoff-lane",
        dest="clear_adjacent_dropoff_lane",
        action="store_false",
        help="Disable adjacent drop-off lane clearing",
    )
    parser.add_argument(
        "--clear-lane-distance",
        type=int,
        default=4,
        help="Max Manhattan distance from drop-off for matching carriers that trigger lane clearing",
    )
    parser.add_argument(
        "--allow-same-shelf-for-same-type",
        dest="allow_same_shelf_for_same_type",
        action="store_true",
        default=False,
        help="Allow multiple bots to target the same shelf for duplicate active item types",
    )
    parser.add_argument(
        "--disable-allow-same-shelf-for-same-type",
        dest="allow_same_shelf_for_same_type",
        action="store_false",
        help="Keep spreading duplicate item picks across different shelves when possible",
    )
    parser.add_argument(
        "--stage-nonmatching-when-active-covered",
        dest="stage_nonmatching_when_active_covered",
        action="store_true",
        default=False,
        help="When active order is already covered by team inventory, stage non-matching bots toward drop-off",
    )
    parser.add_argument(
        "--disable-stage-nonmatching-when-active-covered",
        dest="stage_nonmatching_when_active_covered",
        action="store_false",
        help="Disable non-matching staging while active order is inventory-covered",
    )
    parser.add_argument(
        "--stage-nonmatching-endgame-rounds",
        type=int,
        default=0,
        help="Enable non-matching staging in the final N rounds even when global staging is disabled",
    )
    parser.add_argument(
        "--order-forecast-source",
        type=str,
        default="none",
        choices=["none", "snapshot", "mine", "simulate"],
        help="Optional order forecast source (works for any difficulty when artifacts exist)",
    )
    parser.add_argument(
        "--order-forecast-snapshot",
        type=str,
        default="artifacts/medium/dataset_snapshot_v1.json",
        help="Snapshot path used when --order-forecast-source=snapshot",
    )
    parser.add_argument(
        "--order-forecast-artifact-root",
        type=str,
        default="",
        help="Artifact root used when --order-forecast-source=mine/simulate (default: .seed_artifacts/nmiai/<difficulty>)",
    )
    parser.add_argument("--tie-break-dynamic", action="store_true", help="Use dynamic tie-break salt by active order index")
    parser.add_argument(
        "--artifact-root",
        type=str,
        default=".seed_artifacts/nmiai",
        help="Artifact root directory",
    )
    return parser.parse_args()


def _safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _extract_items_orders(game_over: GameOver | None) -> tuple[int, int]:
    if game_over is None:
        return 0, 0
    items = game_over.items_delivered
    if items is None:
        items = game_over.items or 0
    orders = game_over.orders_completed
    if orders is None:
        orders = game_over.orders or 0
    return int(items or 0), int(orders or 0)


def _apply_params_file_to_args(args: argparse.Namespace) -> None:
    if not args.params_file:
        return
    payload = json.loads(Path(args.params_file).read_text(encoding="utf-8"))
    raw: dict[str, Any] = {}
    if isinstance(payload, dict):
        if isinstance(payload.get("params"), dict):
            raw = dict(payload["params"])
        elif isinstance(payload.get("config"), dict):
            raw = dict(payload["config"])
        else:
            raw = dict(payload)
    mapping = {
        "lookahead_orders": "lookahead_k",
        "dropoff_completion_threshold": "dropoff_threshold",
        "zone_penalty_weight": "zone_penalty",
        "tie_break_seed": "seed",
    }
    for key, value in raw.items():
        target = mapping.get(key, key)
        if hasattr(args, target):
            setattr(args, target, value)


def _load_order_forecast(
    args: argparse.Namespace,
    *,
    session: GameSession | None = None,
) -> dict[int, list[str]] | None:
    source = str(getattr(args, "order_forecast_source", "none") or "none").strip().lower()
    if source == "none":
        return None
    difficulty = str(getattr(args, "difficulty", "")).strip().lower()
    default_artifact_root = (
        f".seed_artifacts/nmiai/{difficulty}" if difficulty else ".seed_artifacts/nmiai/medium"
    )
    artifact_root = str(
        getattr(args, "order_forecast_artifact_root", "") or default_artifact_root
    )
    try:
        from bot._simulator import (
            default_generator_from_dataset,
            load_medium_dataset_snapshot,
            mine_medium_dataset,
            synthesize_orders,
        )
    except Exception:
        return None

    try:
        if source == "snapshot":
            snap_path = Path(str(getattr(args, "order_forecast_snapshot", "")))
            if snap_path.exists():
                dataset = load_medium_dataset_snapshot(snap_path)
            else:
                dataset = mine_medium_dataset(artifact_root)
        else:
            dataset = mine_medium_dataset(artifact_root)
    except Exception:
        return None

    if source == "simulate":
        try:
            map_seed = int(session.map_seed) if session is not None else 7002
            generator = default_generator_from_dataset(
                dataset,
                seed=map_seed,
                known_orders_mode="latest",
            )
            orders = synthesize_orders(dataset, generator)
        except Exception:
            return None
        return {
            idx: list(order.get("items_required", []))
            for idx, order in enumerate(orders)
        }

    return {idx: list(req) for idx, req in enumerate(dataset.observed_orders_exact)}


async def run_live_once(
    *,
    run_index: int,
    args: argparse.Namespace,
) -> LiveRunSummary:
    session: GameSession = request_game_session(args.difficulty)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifact_dir: Path | None = None
    log_dir = Path("logs/bot")
    if args.record:
        artifact_dir = Path(args.artifact_root) / args.difficulty / f"run_{ts}"
        _safe_mkdir(artifact_dir)
        log_dir = artifact_dir / "round_logs"
        _safe_mkdir(log_dir)

    cfg = DecisionConfig(
        lookahead_orders=args.lookahead_k,
        active_weight=args.active_weight,
        preview_weight=args.preview_weight,
        dropoff_completion_threshold=args.dropoff_threshold,
        zone_penalty_weight=args.zone_penalty,
        dist_weight=args.dist_weight,
        dropoff_dist_weight=args.dropoff_dist_weight,
        congestion_weight=args.congestion_weight,
        collision_risk_weight=args.collision_risk_weight,
        replan_penalty_weight=args.replan_penalty_weight,
        carry_home_bias_weight=args.carry_home_bias_weight,
        urgency_weight=args.urgency_weight,
        trip_chain_bonus_weight=args.trip_chain_bonus_weight,
        future_depth_decay=args.future_depth_decay,
        future_count_weight=args.future_count_weight,
        future_prefetch_bonus=args.future_prefetch_bonus,
        future_priority_mode=args.future_priority_mode,
        prefetch_min_completion=args.prefetch_min_completion,
        prefetch_spare_slots=args.prefetch_spare_slots,
        prefetch_nonmatching_cap=args.prefetch_nonmatching_cap,
        strict_active_priority=bool(args.strict_active_priority),
        strict_active_release_completion=args.strict_active_release_completion,
        force_dropoff_for_full_nonmatching=bool(args.force_dropoff_for_full_nonmatching),
        always_deliver_matching=bool(args.always_deliver_matching),
        avoid_dropoff_block_when_matching=bool(args.avoid_dropoff_block_when_matching),
        max_concurrent_deliverers=args.max_concurrent_deliverers,
        adaptive_deliver_queue=bool(args.adaptive_deliver_queue),
        deliver_queue_min=args.deliver_queue_min,
        deliver_queue_max=args.deliver_queue_max,
        assignment_strategy=args.assignment_strategy,
        reservation_horizon=args.reservation_horizon,
        hysteresis_penalty=args.hysteresis_penalty,
        sticky_target_bonus=args.sticky_target_bonus,
        early_deliver_matching_count=args.early_deliver_matching_count,
        early_deliver_inventory_threshold=args.early_deliver_inventory_threshold,
        endgame_disable_prefetch_rounds=args.endgame_disable_prefetch_rounds,
        endgame_force_deliver_rounds=args.endgame_force_deliver_rounds,
        endgame_strict_active=bool(args.endgame_strict_active),
        avoid_immediate_backtrack=bool(args.avoid_immediate_backtrack),
        backtrack_slack=args.backtrack_slack,
        wait_on_backtrack_conflict=bool(args.wait_on_backtrack_conflict),
        pickup_fail_blacklist_threshold=args.pickup_fail_blacklist_threshold,
        pickup_fail_blacklist_rounds=args.pickup_fail_blacklist_rounds,
        stall_round_threshold=args.stall_round_threshold,
        stall_recovery_rounds=args.stall_recovery_rounds,
        stall_recovery_preview_weight=args.stall_recovery_preview_weight,
        stall_recovery_force_dropoff=bool(args.stall_recovery_force_dropoff),
        stall_recovery_strict_active=bool(args.stall_recovery_strict_active),
        clear_adjacent_dropoff_lane=bool(args.clear_adjacent_dropoff_lane),
        clear_lane_distance=args.clear_lane_distance,
        allow_same_shelf_for_same_type=bool(args.allow_same_shelf_for_same_type),
        stage_nonmatching_when_active_covered=bool(args.stage_nonmatching_when_active_covered),
        stage_nonmatching_endgame_rounds=args.stage_nonmatching_endgame_rounds,
        collision_aggressiveness=args.collision_aggressiveness,
        tie_break_seed=args.seed,
        tie_break_dynamic=bool(args.tie_break_dynamic),
    )
    order_forecast = _load_order_forecast(args, session=session)
    engine = DecisionEngine(
        use_astar=args.use_astar,
        debug=args.debug,
        verbose=False,
        config=cfg,
        order_forecast=order_forecast,
    )
    logger = RoundLogger(
        log_dir=str(log_dir),
        difficulty=args.difficulty,
        save_states=bool(args.save_states),
    )

    state0_raw: dict[str, Any] | None = None
    order_trace: list[dict[str, Any]] = []
    game_over_obj: GameOver | None = None
    decision_samples: list[float] = []
    idle_steps = 0
    collisions_avoided = 0
    tracker = OrderTracker(difficulty=args.difficulty)
    show_max_printed = False

    def on_state(state: GameState, raw: dict[str, Any]) -> None:
        nonlocal state0_raw, show_max_printed
        tracker.update(state)
        if args.record_order_trace:
            active = None
            preview = None
            for order in raw.get("orders", []):
                if order.get("status") == "active":
                    active = {
                        "id": order.get("id"),
                        "items_required": list(order.get("items_required", [])),
                        "items_delivered": list(order.get("items_delivered", [])),
                    }
                elif order.get("status") == "preview":
                    preview = {
                        "id": order.get("id"),
                        "items_required": list(order.get("items_required", [])),
                        "items_delivered": list(order.get("items_delivered", [])),
                    }
            order_trace.append(
                {
                    "round": state.round,
                    "score": state.score,
                    "active_order_index": state.active_order_index,
                    "active": active,
                    "preview": preview,
                }
            )
        if state.round == 0 and state0_raw is None:
            state0_raw = dict(raw)
            if args.show_max and not show_max_printed:
                info = max_score_for_game(state, difficulty=args.difficulty)
                if info.exact:
                    print(
                        f"[max] total_orders={info.total_orders} "
                        f"total_items_needed={info.total_items_needed} "
                        f"max_score={info.max_score}"
                    )
                else:
                    print(
                        f"[max] total_orders={info.total_orders} "
                        f"observed_orders={info.observed_orders} "
                        f"observed_items={info.observed_items} "
                        f"score_bound=[{info.lower_bound_score},{info.upper_bound_score}]"
                    )
                show_max_printed = True

    def on_actions(_state: GameState, actions) -> None:
        nonlocal idle_steps, collisions_avoided
        idle_steps += sum(1 for action in actions.actions if action.action == BotAction.WAIT)
        decision_samples.append(engine.last_decision_ms)
        collisions_avoided += int(getattr(engine, "last_collisions_avoided", 0))

    def on_game_over(result: GameOver) -> None:
        nonlocal game_over_obj
        game_over_obj = result

    print(f"[run {run_index}] session map={session.map_label} seed={session.map_seed} ws={redact_ws_url(session.ws_url)}")
    client = GameWSClient(
        url=session.ws_url,
        engine=engine,
        logger=logger,
        debug=args.debug,
        on_state=on_state,
        on_actions=on_actions,
        on_game_over=on_game_over,
    )
    game_over = await client.play()
    game_over_obj = game_over_obj or game_over

    items_delivered, orders_completed = _extract_items_orders(game_over_obj)
    info = tracker.as_info()
    avg_decision_ms = sum(decision_samples) / len(decision_samples) if decision_samples else 0.0
    summary = LiveRunSummary(
        run_index=run_index,
        score=int(game_over_obj.score if game_over_obj else 0),
        items_delivered=items_delivered,
        orders_completed=orders_completed,
        rounds_played=len(decision_samples),
        idle_steps=idle_steps,
        collisions_avoided=collisions_avoided,
        avg_decision_ms=avg_decision_ms,
        max_score_exact=info.max_score,
        max_score_upper_bound=info.upper_bound_score,
        max_score_lower_bound=info.lower_bound_score,
        all_orders_observed=info.exact,
        artifact_dir=str(artifact_dir) if artifact_dir else None,
    )

    if artifact_dir is not None:
        _write_json(
            artifact_dir / "config.json",
            {
                "difficulty": args.difficulty,
                "run_index": run_index,
                "cooldown_sec": args.cooldown_sec,
                "seed": args.seed,
                "use_astar": bool(args.use_astar),
                "show_max": bool(args.show_max),
                "order_forecast_source": str(args.order_forecast_source),
                "strategy": cfg.to_dict(),
            },
        )
        _write_json(
            artifact_dir / "result.json",
            {
                **asdict(summary),
                "max_score_info": tracker.summary(),
            },
        )
        if state0_raw is not None:
            _write_json(artifact_dir / "state0.json", state0_raw)
        if game_over_obj is not None:
            _write_json(artifact_dir / "game_over.json", game_over_obj.model_dump())
        if args.record_order_trace:
            _write_json(artifact_dir / "order_trace.json", {"trace": order_trace})
        (artifact_dir / "log.txt").write_text(
            "\n".join(
                [
                    f"run_index={run_index}",
                    f"difficulty={args.difficulty}",
                    f"map_seed={session.map_seed}",
                    f"ws_url={redact_ws_url(session.ws_url)}",
                    f"order_forecast_source={args.order_forecast_source}",
                    f"score={summary.score}",
                    f"items_delivered={summary.items_delivered}",
                    f"orders_completed={summary.orders_completed}",
                    f"rounds_played={summary.rounds_played}",
                    f"idle_steps={summary.idle_steps}",
                    f"collisions_avoided={summary.collisions_avoided}",
                    f"avg_decision_ms={summary.avg_decision_ms:.3f}",
                    f"logger_path={logger.log_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    print(
        f"[run {run_index}] score={summary.score} "
        f"items={summary.items_delivered} orders={summary.orders_completed} "
        f"idle={summary.idle_steps} avg_ms={summary.avg_decision_ms:.2f}"
    )
    return summary


async def async_main() -> None:
    args = parse_args()
    _apply_params_file_to_args(args)

    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.max_live_runs < 1:
        raise SystemExit("--max-live-runs must be >= 1")
    if args.runs > args.max_live_runs:
        raise SystemExit(f"--runs={args.runs} exceeds --max-live-runs={args.max_live_runs}")
    if args.cooldown_sec < 0:
        raise SystemExit("--cooldown-sec must be >= 0")

    summaries: list[LiveRunSummary] = []
    for idx in range(args.runs):
        if idx > 0 and args.cooldown_sec > 0:
            await asyncio.sleep(args.cooldown_sec)
        run_summary = await run_live_once(run_index=idx + 1, args=args)
        summaries.append(run_summary)

    best = max(summaries, key=lambda s: s.score)
    print(
        f"[summary] runs={len(summaries)} best_score={best.score} "
        f"best_run={best.run_index} "
        f"max_score={'exact '+str(best.max_score_exact) if best.max_score_exact is not None else 'bound '+str(best.max_score_upper_bound)}"
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
