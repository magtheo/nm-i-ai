"""Compare recorded live runs and summarize stability/throughput metrics."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .orbit_wall_log_analyzer import analyze as analyze_run_trace
except ImportError:  # pragma: no cover - direct script execution fallback
    from orbit_wall_log_analyzer import analyze as analyze_run_trace


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


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


def _compare_rows(
    *,
    run_dirs: list[Path],
    include_analyzer: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        result = _load_json(run_dir / "result.json") or {}
        config = _load_json(run_dir / "config.json") or {}
        decision_trace = run_dir / "decision_trace.jsonl"
        order_trace = run_dir / "order_trace.json"

        analysis: dict[str, Any] | None = None
        if include_analyzer and decision_trace.exists():
            analysis = analyze_run_trace(
                decision_trace_path=decision_trace,
                order_trace_path=order_trace if order_trace.exists() else None,
                result_path=run_dir / "result.json",
            )

        metrics = analysis.get("metrics", {}) if isinstance(analysis, dict) else {}
        failures = analysis.get("failures", []) if isinstance(analysis, dict) else []
        failure_reasons = []
        if isinstance(failures, list):
            failure_reasons = [str(row.get("reason", "")) for row in failures if isinstance(row, dict)]

        row = {
            "run_dir": str(run_dir.resolve()),
            "run_name": run_dir.name,
            "engine_mode": str(config.get("engine_mode", "unknown")),
            "score": _safe_int(result.get("score")),
            "items_delivered": _safe_int(result.get("items_delivered")),
            "orders_completed": _safe_int(result.get("orders_completed")),
            "idle_steps": _safe_int(result.get("idle_steps")),
            "rounds_played": _safe_int(result.get("rounds_played")),
            "avg_decision_ms": _safe_float(result.get("avg_decision_ms")),
            "d0_busy_ratio": _safe_float(metrics.get("d0_busy_ratio")),
            "items_per_drop_action": _safe_float(metrics.get("items_per_drop_action")),
            "deliver_bots_avg": _safe_float(metrics.get("deliver_bots_avg")),
            "rejoin_backlog_avg": _safe_float(metrics.get("rejoin_backlog_avg")),
            "branch_to_delivery_rate": _safe_float(metrics.get("branch_to_delivery_rate")),
            "wait_due_to_spacing_guard": _safe_float(metrics.get("wait_due_to_spacing_guard")),
            "wait_due_to_collision_block": _safe_float(metrics.get("wait_due_to_collision_block")),
            "queue_semantics_violation": _safe_float(metrics.get("queue_semantics_violation")),
            "ring_direction_violation": _safe_float(metrics.get("ring_direction_violation")),
            "failure_reasons": failure_reasons,
        }
        rows.append(row)
    return rows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "runs": 0,
            "mean_score": 0.0,
            "median_score": 0.0,
            "best_score": 0,
            "mean_orders_completed": 0.0,
            "mean_items_delivered": 0.0,
            "mean_idle_steps": 0.0,
            "mean_d0_busy_ratio": 0.0,
            "mean_spacing_wait": 0.0,
            "queue_semantics_violations": 0.0,
            "ring_direction_violations": 0.0,
            "failure_reason_counts": {},
            "by_engine_mode": {},
        }

    scores = [int(row["score"]) for row in rows]
    by_engine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reason_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        by_engine[str(row["engine_mode"])].append(row)
        for reason in row.get("failure_reasons", []):
            if reason:
                reason_counts[str(reason)] += 1

    def _mean(name: str) -> float:
        return float(statistics.fmean([_safe_float(row.get(name)) for row in rows]))

    by_engine_payload: dict[str, Any] = {}
    for engine_mode, engine_rows in sorted(by_engine.items()):
        by_engine_payload[engine_mode] = {
            "runs": len(engine_rows),
            "mean_score": float(statistics.fmean([_safe_float(row.get("score")) for row in engine_rows])),
            "mean_orders_completed": float(statistics.fmean([_safe_float(row.get("orders_completed")) for row in engine_rows])),
            "mean_items_delivered": float(statistics.fmean([_safe_float(row.get("items_delivered")) for row in engine_rows])),
            "mean_idle_steps": float(statistics.fmean([_safe_float(row.get("idle_steps")) for row in engine_rows])),
            "mean_d0_busy_ratio": float(statistics.fmean([_safe_float(row.get("d0_busy_ratio")) for row in engine_rows])),
        }

    return {
        "runs": len(rows),
        "mean_score": float(statistics.fmean(scores)),
        "median_score": float(statistics.median(scores)),
        "best_score": int(max(scores)),
        "mean_orders_completed": _mean("orders_completed"),
        "mean_items_delivered": _mean("items_delivered"),
        "mean_idle_steps": _mean("idle_steps"),
        "mean_d0_busy_ratio": _mean("d0_busy_ratio"),
        "mean_spacing_wait": _mean("wait_due_to_spacing_guard"),
        "queue_semantics_violations": float(sum(_safe_float(row.get("queue_semantics_violation")) for row in rows)),
        "ring_direction_violations": float(sum(_safe_float(row.get("ring_direction_violation")) for row in rows)),
        "failure_reason_counts": dict(sorted(reason_counts.items())),
        "by_engine_mode": by_engine_payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare recorded live runs under one artifact root")
    parser.add_argument(
        "--artifact-root",
        type=str,
        default=".seed_artifacts/nmiai",
        help="Root with run artifacts (or root containing <difficulty>/run_* subdirs)",
    )
    parser.add_argument("--difficulty", type=str, default="expert", help="Difficulty subdir used under artifact root")
    parser.add_argument("--limit", type=int, default=20, help="How many newest runs to compare")
    parser.add_argument(
        "--engine-mode",
        type=str,
        default="",
        help="Optional engine_mode filter from config.json (e.g. decision_engine, orbit_wall_conveyor_v2)",
    )
    parser.add_argument(
        "--skip-analyzer",
        action="store_true",
        help="Skip decision-trace analyzer (faster, but fewer stability metrics)",
    )
    parser.add_argument("--output", type=str, default="", help="Optional JSON output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dirs = _discover_run_dirs(
        artifact_root=Path(args.artifact_root).resolve(),
        difficulty=str(args.difficulty).strip().lower(),
        limit=max(1, int(args.limit)),
    )
    rows = _compare_rows(run_dirs=run_dirs, include_analyzer=not bool(args.skip_analyzer))
    if str(args.engine_mode).strip():
        wanted = str(args.engine_mode).strip()
        rows = [row for row in rows if str(row.get("engine_mode")) == wanted]
    summary = _aggregate(rows)

    payload = {
        "inputs": {
            "artifact_root": str(Path(args.artifact_root).resolve()),
            "difficulty": str(args.difficulty).strip().lower(),
            "limit": max(1, int(args.limit)),
            "engine_mode": str(args.engine_mode).strip() or None,
            "analyzer_enabled": not bool(args.skip_analyzer),
        },
        "summary": summary,
        "runs": rows,
    }

    encoded = json.dumps(payload, indent=2, ensure_ascii=True)
    if str(args.output).strip():
        out = Path(args.output).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(encoded + "\n", encoding="utf-8")
        print(f"[compare-runs] wrote {out}")
    else:
        print(encoded)


if __name__ == "__main__":
    main()


