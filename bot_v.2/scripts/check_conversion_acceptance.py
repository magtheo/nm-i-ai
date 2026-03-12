"""Evaluate conversion-safety acceptance gates from recorded expert runs."""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _discover_run_dirs(artifact_root: Path, difficulty: str, limit: int) -> list[Path]:
    root = artifact_root
    difficulty_dir = artifact_root / difficulty
    if difficulty_dir.exists():
        root = difficulty_dir
    if not root.exists():
        return []
    runs = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("run_")]
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[: max(1, int(limit))]


@dataclass(frozen=True)
class GateThresholds:
    max_no_target_wait_ratio: float = 0.1
    max_no_target_waits: int = 120
    min_drop_actions: int = 12
    min_items_per_drop: float = 0.9
    min_drop_to_pick_ratio: float = 0.3
    max_coupling_break_rounds: int = 40
    max_commitment_stagnation_rounds: int = 80
    max_throughput_floor_breach_rounds: int = 90
    max_delivery_lane_breach_rounds: int = 20

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_no_target_wait_ratio": float(self.max_no_target_wait_ratio),
            "max_no_target_waits": int(self.max_no_target_waits),
            "min_drop_actions": int(self.min_drop_actions),
            "min_items_per_drop": float(self.min_items_per_drop),
            "min_drop_to_pick_ratio": float(self.min_drop_to_pick_ratio),
            "max_coupling_break_rounds": int(self.max_coupling_break_rounds),
            "max_commitment_stagnation_rounds": int(self.max_commitment_stagnation_rounds),
            "max_throughput_floor_breach_rounds": int(self.max_throughput_floor_breach_rounds),
            "max_delivery_lane_breach_rounds": int(self.max_delivery_lane_breach_rounds),
        }


def _analyze_run(run_dir: Path, thresholds: GateThresholds) -> dict[str, Any]:
    result = _load_json(run_dir / "result.json")
    decision_trace_path = run_dir / "decision_trace.jsonl"

    rounds = max(0, _safe_int(result.get("rounds_played")))
    picks = 0
    drops = 0
    waits = 0
    wait_no_target = 0.0
    wait_no_assignment = 0.0
    coupling_break_rounds = 0
    commitment_stagnation_rounds = 0
    throughput_floor_breach_rounds = 0
    delivery_lane_breach_rounds = 0
    weak_conversion_rounds = 0
    bot_count = 0
    parsed_rounds = 0

    if decision_trace_path.exists():
        with decision_trace_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                parsed_rounds += 1
                state = row.get("state", {})
                if isinstance(state, dict):
                    bots = state.get("bots", [])
                    if isinstance(bots, list) and bots and bot_count <= 0:
                        bot_count = len(bots)

                actions = row.get("actions", [])
                if isinstance(actions, list):
                    for action in actions:
                        if not isinstance(action, dict):
                            continue
                        cmd = str(action.get("action", ""))
                        if cmd == "pick_up":
                            picks += 1
                        elif cmd == "drop_off":
                            drops += 1
                        elif cmd == "wait":
                            waits += 1

                telemetry = row.get("telemetry", {})
                if not isinstance(telemetry, dict):
                    telemetry = {}
                wait_reason_by_bot = row.get("wait_reason_by_bot", {})
                if not isinstance(wait_reason_by_bot, dict):
                    wait_reason_by_bot = {}
                if "wait_due_to_no_target" in telemetry:
                    round_wait_no_target = _safe_float(telemetry.get("wait_due_to_no_target", 0.0))
                else:
                    round_wait_no_target = float(
                        sum(1 for reason in wait_reason_by_bot.values() if str(reason) == "wait_due_to_no_target")
                    )
                if "wait_due_to_no_assignment" in telemetry:
                    round_wait_no_assignment = _safe_float(telemetry.get("wait_due_to_no_assignment", 0.0))
                else:
                    round_wait_no_assignment = float(
                        sum(1 for reason in wait_reason_by_bot.values() if str(reason) == "wait_due_to_no_assignment")
                    )

                wait_no_target += float(round_wait_no_target)
                wait_no_assignment += float(round_wait_no_assignment)
                coupling_break_rounds += 1 if _safe_float(telemetry.get("conversion_guard_pickup_drop_coupling_break", 0.0)) > 0.5 else 0
                commitment_stagnation_rounds += 1 if _safe_float(telemetry.get("conversion_guard_commitment_stagnation", 0.0)) > 0.5 else 0
                throughput_floor_breach_rounds += 1 if _safe_float(telemetry.get("conversion_guard_throughput_lane_floor_breach", 0.0)) > 0.5 else 0
                delivery_lane_breach_rounds += 1 if _safe_float(telemetry.get("conversion_guard_delivery_lane_breach", 0.0)) > 0.5 else 0
                weak_conversion_rounds += 1 if _safe_float(telemetry.get("conversion_guard_weak_conversion_window", 0.0)) > 0.5 else 0

    if rounds <= 0:
        rounds = parsed_rounds
    if bot_count <= 0:
        bot_count = 10

    delivered = max(0, _safe_int(result.get("items_delivered")))
    score = _safe_int(result.get("score"))
    orders_completed = _safe_int(result.get("orders_completed"))

    items_per_drop = float(delivered / drops) if drops > 0 else 0.0
    drop_to_pick_ratio = float(drops / picks) if picks > 0 else 0.0
    wait_no_target_ratio = float(wait_no_target / float(max(1, rounds * bot_count)))
    wait_no_assignment_ratio = float(wait_no_assignment / float(max(1, rounds * bot_count)))

    gates = {
        "target_liveness": bool(
            wait_no_target_ratio <= float(thresholds.max_no_target_wait_ratio)
            and wait_no_target <= float(thresholds.max_no_target_waits)
        ),
        "drop_conversion_floor": bool(
            drops >= int(thresholds.min_drop_actions)
            and items_per_drop >= float(thresholds.min_items_per_drop)
        ),
        "pickup_drop_coupling": bool(
            drop_to_pick_ratio >= float(thresholds.min_drop_to_pick_ratio)
            and coupling_break_rounds <= int(thresholds.max_coupling_break_rounds)
        ),
        "commitment_realism": bool(
            commitment_stagnation_rounds <= int(thresholds.max_commitment_stagnation_rounds)
        ),
        "throughput_lane_floor": bool(
            throughput_floor_breach_rounds <= int(thresholds.max_throughput_floor_breach_rounds)
        ),
        "delivery_lane_guarantee": bool(
            delivery_lane_breach_rounds <= int(thresholds.max_delivery_lane_breach_rounds)
        ),
    }
    pass_all = all(bool(v) for v in gates.values())

    return {
        "run_name": run_dir.name,
        "run_dir": str(run_dir.resolve()),
        "score": int(score),
        "orders_completed": int(orders_completed),
        "items_delivered": int(delivered),
        "rounds": int(rounds),
        "bot_count": int(bot_count),
        "action_counts": {
            "pick_up": int(picks),
            "drop_off": int(drops),
            "wait": int(waits),
        },
        "conversion_metrics": {
            "items_per_drop_action": float(items_per_drop),
            "drop_to_pick_ratio": float(drop_to_pick_ratio),
            "wait_due_to_no_target": float(wait_no_target),
            "wait_due_to_no_target_ratio": float(wait_no_target_ratio),
            "wait_due_to_no_assignment": float(wait_no_assignment),
            "wait_due_to_no_assignment_ratio": float(wait_no_assignment_ratio),
            "coupling_break_rounds": int(coupling_break_rounds),
            "commitment_stagnation_rounds": int(commitment_stagnation_rounds),
            "throughput_floor_breach_rounds": int(throughput_floor_breach_rounds),
            "delivery_lane_breach_rounds": int(delivery_lane_breach_rounds),
            "weak_conversion_window_rounds": int(weak_conversion_rounds),
        },
        "gates": gates,
        "pass_all_gates": bool(pass_all),
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def check_artifact_root(
    *,
    artifact_root: Path,
    difficulty: str,
    limit: int,
    thresholds: GateThresholds,
) -> dict[str, Any]:
    run_dirs = _discover_run_dirs(artifact_root=artifact_root, difficulty=difficulty, limit=limit)
    runs = [_analyze_run(run_dir, thresholds) for run_dir in run_dirs]

    gate_names = [
        "target_liveness",
        "drop_conversion_floor",
        "pickup_drop_coupling",
        "commitment_realism",
        "throughput_lane_floor",
        "delivery_lane_guarantee",
    ]
    fail_counts = {gate: 0 for gate in gate_names}
    pass_all_count = 0
    for run in runs:
        if run.get("pass_all_gates"):
            pass_all_count += 1
        gates = run.get("gates", {})
        if isinstance(gates, dict):
            for gate in gate_names:
                if not bool(gates.get(gate)):
                    fail_counts[gate] += 1

    conversion_rows = [run.get("conversion_metrics", {}) for run in runs]

    def _med_metric(name: str) -> float:
        values = [_safe_float(row.get(name, 0.0)) for row in conversion_rows if isinstance(row, dict)]
        return _median(values)

    summary = {
        "runs": len(runs),
        "score_median": _median([_safe_float(run.get("score", 0.0)) for run in runs]),
        "orders_completed_median": _median([_safe_float(run.get("orders_completed", 0.0)) for run in runs]),
        "items_delivered_median": _median([_safe_float(run.get("items_delivered", 0.0)) for run in runs]),
        "overall_pass_runs": int(pass_all_count),
        "overall_pass_rate": float(pass_all_count / float(max(1, len(runs)))),
        "gate_fail_counts": fail_counts,
        "conversion_medians": {
            "items_per_drop_action": _med_metric("items_per_drop_action"),
            "drop_to_pick_ratio": _med_metric("drop_to_pick_ratio"),
            "wait_due_to_no_target": _med_metric("wait_due_to_no_target"),
            "wait_due_to_no_target_ratio": _med_metric("wait_due_to_no_target_ratio"),
            "wait_due_to_no_assignment": _med_metric("wait_due_to_no_assignment"),
            "wait_due_to_no_assignment_ratio": _med_metric("wait_due_to_no_assignment_ratio"),
            "coupling_break_rounds": _med_metric("coupling_break_rounds"),
            "commitment_stagnation_rounds": _med_metric("commitment_stagnation_rounds"),
            "throughput_floor_breach_rounds": _med_metric("throughput_floor_breach_rounds"),
            "delivery_lane_breach_rounds": _med_metric("delivery_lane_breach_rounds"),
            "weak_conversion_window_rounds": _med_metric("weak_conversion_window_rounds"),
        },
    }
    return {
        "inputs": {
            "artifact_root": str(artifact_root.resolve()),
            "difficulty": str(difficulty).strip().lower(),
            "limit": max(1, int(limit)),
        },
        "thresholds": thresholds.as_dict(),
        "summary": summary,
        "runs": runs,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check conversion acceptance gates for recorded runs.")
    parser.add_argument("--artifact-root", type=str, required=True, help="Artifact root containing run directories.")
    parser.add_argument("--difficulty", type=str, default="expert", help="Difficulty subdir name.")
    parser.add_argument("--limit", type=int, default=3, help="Number of newest runs to inspect.")
    parser.add_argument("--max-no-target-wait-ratio", type=float, default=0.1)
    parser.add_argument("--max-no-target-waits", type=int, default=120)
    parser.add_argument("--min-drop-actions", type=int, default=12)
    parser.add_argument("--min-items-per-drop", type=float, default=0.9)
    parser.add_argument("--min-drop-to-pick-ratio", type=float, default=0.3)
    parser.add_argument("--max-coupling-break-rounds", type=int, default=40)
    parser.add_argument("--max-commitment-stagnation-rounds", type=int, default=80)
    parser.add_argument("--max-throughput-floor-breach-rounds", type=int, default=90)
    parser.add_argument("--max-delivery-lane-breach-rounds", type=int, default=20)
    parser.add_argument("--output", type=str, default="", help="Optional output JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    thresholds = GateThresholds(
        max_no_target_wait_ratio=float(args.max_no_target_wait_ratio),
        max_no_target_waits=int(args.max_no_target_waits),
        min_drop_actions=int(args.min_drop_actions),
        min_items_per_drop=float(args.min_items_per_drop),
        min_drop_to_pick_ratio=float(args.min_drop_to_pick_ratio),
        max_coupling_break_rounds=int(args.max_coupling_break_rounds),
        max_commitment_stagnation_rounds=int(args.max_commitment_stagnation_rounds),
        max_throughput_floor_breach_rounds=int(args.max_throughput_floor_breach_rounds),
        max_delivery_lane_breach_rounds=int(args.max_delivery_lane_breach_rounds),
    )
    payload = check_artifact_root(
        artifact_root=Path(args.artifact_root),
        difficulty=str(args.difficulty).strip().lower(),
        limit=max(1, int(args.limit)),
        thresholds=thresholds,
    )
    encoded = json.dumps(payload, indent=2, ensure_ascii=True)
    if str(args.output).strip():
        out = Path(args.output).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(encoded + "\n", encoding="utf-8")
        print(f"[conversion-acceptance] wrote {out}")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
