"""Run a small expert coordination bundle matrix against the legacy DecisionEngine.

This script keeps scope narrow:
- fixed preset bundles from configs/expert_coordination_presets/
- legacy DecisionEngine only (default expert path remains untouched)
- per-bundle live batches with recorded artifacts
- compact median-focused summary
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRESETS = (
    "legacy_baseline",
    "bundle_a_active_completion",
    "bundle_b_dropoff_corridor",
    "bundle_c_startup_anti_idle",
)


@dataclass(frozen=True)
class RunMetrics:
    run_dir: str
    score: int
    orders_completed: int
    items_delivered: int
    idle_steps: int
    first_completion_round: int | None
    second_completion_round: int | None
    active_tail_rounds: int
    no_target_waits: int
    no_assignment_waits: int
    collision_waits: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "score": self.score,
            "orders_completed": self.orders_completed,
            "items_delivered": self.items_delivered,
            "idle_steps": self.idle_steps,
            "first_completion_round": self.first_completion_round,
            "second_completion_round": self.second_completion_round,
            "active_tail_rounds": self.active_tail_rounds,
            "no_target_waits": self.no_target_waits,
            "no_assignment_waits": self.no_assignment_waits,
            "collision_waits": self.collision_waits,
        }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected object in {path}")
    return payload


def _remaining_total(active_snapshot: dict[str, Any]) -> int:
    need = Counter(str(t) for t in active_snapshot.get("items_required", []))
    for item_type in active_snapshot.get("items_delivered", []):
        item_type = str(item_type)
        if need.get(item_type, 0) > 0:
            need[item_type] -= 1
    return int(sum(v for v in need.values() if int(v) > 0))


def _extract_completion_rounds(order_trace: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    prev_idx: int | None = None
    for row in order_trace:
        idx = int(row.get("active_order_index", 0))
        if prev_idx is None:
            prev_idx = idx
            continue
        if idx > prev_idx:
            out.append(int(row.get("round", 0)))
            prev_idx = idx
    return out


def _parse_run_metrics(run_dir: Path) -> RunMetrics:
    result = _read_json(run_dir / "result.json")
    order_trace_payload = _read_json(run_dir / "order_trace.json")
    trace_rows = order_trace_payload.get("trace", [])
    if not isinstance(trace_rows, list):
        trace_rows = []

    completion_rounds = _extract_completion_rounds([row for row in trace_rows if isinstance(row, dict)])
    active_tail_rounds = 0
    for row in trace_rows:
        if not isinstance(row, dict):
            continue
        active = row.get("active")
        if not isinstance(active, dict):
            continue
        rem = _remaining_total(active)
        if 0 < rem <= 2:
            active_tail_rounds += 1

    no_target_waits = 0
    no_assignment_waits = 0
    collision_waits = 0
    with (run_dir / "decision_trace.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            waits = row.get("wait_reason_by_bot", {})
            if not isinstance(waits, dict):
                continue
            for reason in waits.values():
                reason = str(reason)
                if reason == "wait_due_to_no_target":
                    no_target_waits += 1
                elif reason == "wait_due_to_no_assignment":
                    no_assignment_waits += 1
                elif reason == "wait_due_to_collision_block":
                    collision_waits += 1

    return RunMetrics(
        run_dir=str(run_dir.resolve()),
        score=int(result.get("score", 0)),
        orders_completed=int(result.get("orders_completed", 0)),
        items_delivered=int(result.get("items_delivered", 0)),
        idle_steps=int(result.get("idle_steps", 0)),
        first_completion_round=int(completion_rounds[0]) if completion_rounds else None,
        second_completion_round=int(completion_rounds[1]) if len(completion_rounds) > 1 else None,
        active_tail_rounds=int(active_tail_rounds),
        no_target_waits=int(no_target_waits),
        no_assignment_waits=int(no_assignment_waits),
        collision_waits=int(collision_waits),
    )


def _metric_median(values: list[int | float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _round_median(values: list[int | None], fallback: int = 300) -> float:
    cooked = [int(v) if v is not None else int(fallback) for v in values]
    return _metric_median(cooked)


def _aggregate(rows: list[RunMetrics]) -> dict[str, Any]:
    if not rows:
        return {}
    scores = [row.score for row in rows]
    orders = [row.orders_completed for row in rows]
    items = [row.items_delivered for row in rows]
    idle = [row.idle_steps for row in rows]
    tails = [row.active_tail_rounds for row in rows]
    no_target = [row.no_target_waits for row in rows]
    no_assign = [row.no_assignment_waits for row in rows]
    collisions = [row.collision_waits for row in rows]
    return {
        "runs": len(rows),
        "score_median": _metric_median(scores),
        "score_mean": float(statistics.fmean(scores)),
        "score_best": int(max(scores)),
        "orders_median": _metric_median(orders),
        "orders_mean": float(statistics.fmean(orders)),
        "items_median": _metric_median(items),
        "items_mean": float(statistics.fmean(items)),
        "first_completion_median": _round_median([row.first_completion_round for row in rows]),
        "second_completion_median": _round_median([row.second_completion_round for row in rows]),
        "active_tail_rounds_median": _metric_median(tails),
        "idle_steps_median": _metric_median(idle),
        "no_target_waits_median": _metric_median(no_target),
        "no_assignment_waits_median": _metric_median(no_assign),
        "collision_waits_median": _metric_median(collisions),
    }


def _run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out = [path for path in root.iterdir() if path.is_dir() and path.name.startswith("run_")]
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _execute_preset(
    *,
    preset_name: str,
    preset_path: Path,
    artifact_root: Path,
    runs_per_preset: int,
    cooldown_sec: float,
) -> list[Path]:
    preset_artifact_root = artifact_root / preset_name
    expert_root = preset_artifact_root / "expert"
    before = {path.name for path in _run_dirs(expert_root)}
    cmd = [
        sys.executable,
        "-m",
        "scripts.run_nmiai_grocery_bot",
        "--difficulty",
        "expert",
        "--legacy-expert-decision-engine",
        "--params-file",
        str(preset_path),
        "--runs",
        str(runs_per_preset),
        "--cooldown-sec",
        str(cooldown_sec),
        "--record",
        "--record-order-trace",
        "--record-decision-trace",
        "--artifact-root",
        str(preset_artifact_root),
    ]
    print(f"[coord-matrix] running {preset_name}: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    after = _run_dirs(expert_root)
    new_runs = [path for path in after if path.name not in before]
    if len(new_runs) < runs_per_preset:
        new_runs = after[:runs_per_preset]
    new_runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return new_runs[:runs_per_preset]


def _load_reference_supply_runs(*, root: Path, limit: int) -> list[RunMetrics]:
    out: list[RunMetrics] = []
    for run_dir in _run_dirs(root):
        config_path = run_dir / "config.json"
        if not config_path.exists():
            continue
        try:
            config = _read_json(config_path)
        except Exception:
            continue
        if str(config.get("engine_mode", "")) != "expert_supply_strategy":
            continue
        try:
            out.append(_parse_run_metrics(run_dir))
        except Exception:
            continue
        if len(out) >= max(1, int(limit)):
            break
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run expert coordination bundle experiment matrix")
    parser.add_argument(
        "--presets-dir",
        type=str,
        default="configs/expert_coordination_presets",
        help="Directory with bundle params JSON presets",
    )
    parser.add_argument(
        "--artifact-root",
        type=str,
        default=".seed_artifacts/experiments/expert_coordination_matrix",
        help="Artifact root for this matrix",
    )
    parser.add_argument("--runs-per-preset", type=int, default=3, help="Live runs per preset")
    parser.add_argument("--cooldown-sec", type=float, default=1.0, help="Cooldown between runs")
    parser.add_argument(
        "--preset-order",
        type=str,
        default=",".join(DEFAULT_PRESETS),
        help="Comma-separated preset file stems in execution order",
    )
    parser.add_argument(
        "--supply-reference-root",
        type=str,
        default=".seed_artifacts/nmiai/expert",
        help="Reference root for current expert-supply baseline runs",
    )
    parser.add_argument("--supply-reference-limit", type=int, default=3, help="Reference run count")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runs_per_preset = max(1, int(args.runs_per_preset))
    cooldown_sec = max(0.0, float(args.cooldown_sec))
    presets_dir = (ROOT / str(args.presets_dir)).resolve()
    artifact_root = (ROOT / str(args.artifact_root)).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)

    preset_order = [part.strip() for part in str(args.preset_order).split(",") if part.strip()]
    if not preset_order:
        raise SystemExit("No presets selected")

    payload: dict[str, Any] = {
        "inputs": {
            "presets_dir": str(presets_dir),
            "artifact_root": str(artifact_root),
            "runs_per_preset": runs_per_preset,
            "cooldown_sec": cooldown_sec,
            "preset_order": preset_order,
        },
        "presets": {},
        "reference": {},
    }

    for preset_name in preset_order:
        preset_path = presets_dir / f"{preset_name}.json"
        if not preset_path.exists():
            raise SystemExit(f"Missing preset file: {preset_path}")
        run_dirs = _execute_preset(
            preset_name=preset_name,
            preset_path=preset_path,
            artifact_root=artifact_root,
            runs_per_preset=runs_per_preset,
            cooldown_sec=cooldown_sec,
        )
        rows = [_parse_run_metrics(path) for path in run_dirs]
        payload["presets"][preset_name] = {
            "params_file": str(preset_path),
            "aggregate": _aggregate(rows),
            "runs": [row.as_dict() for row in rows],
        }

    supply_reference_rows = _load_reference_supply_runs(
        root=(ROOT / str(args.supply_reference_root)).resolve(),
        limit=max(1, int(args.supply_reference_limit)),
    )
    payload["reference"] = {
        "expert_supply_strategy_latest": {
            "aggregate": _aggregate(supply_reference_rows),
            "runs": [row.as_dict() for row in supply_reference_rows],
        }
    }

    summary_path = artifact_root / "summary.json"
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"[coord-matrix] wrote {summary_path}")
    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
