"""Analyze orbit-wall live traces and classify common conveyor failures."""
from __future__ import annotations

import argparse
import json
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


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _classify_failures(*, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    rounds = max(1, int(metrics.get("rounds", 0)))
    branch_exit_visits = _safe_float(metrics.get("branch_exit_visits"))
    branch_to_delivery = _safe_float(metrics.get("branch_to_delivery"))
    branch_waits = _safe_float(metrics.get("branch_waits"))
    to_delivery_rate = branch_to_delivery / max(1.0, branch_exit_visits)
    wait_rate = branch_waits / max(1.0, branch_exit_visits)

    wait_spacing = _safe_float(metrics.get("wait_due_to_spacing_guard"))
    wait_collision = _safe_float(metrics.get("wait_due_to_collision_block"))
    wait_no_assignment = _safe_float(metrics.get("wait_due_to_no_assignment"))
    wait_total = wait_spacing + wait_collision + wait_no_assignment
    spacing_share = wait_spacing / max(1.0, wait_total)

    queue_violations = _safe_float(metrics.get("queue_semantics_violation"))
    ring_direction_violations = _safe_float(metrics.get("ring_direction_violation"))
    rejoin_denials = _safe_float(metrics.get("rejoin_denials"))
    rejoin_backlog_avg = _safe_float(metrics.get("rejoin_backlog_avg"))
    rejoin_backlog_max = _safe_float(metrics.get("rejoin_backlog_max"))
    drop_off_actions = _safe_float(metrics.get("drop_off_actions"))
    deliver_bots_avg = _safe_float(metrics.get("deliver_bots_avg"))
    d0_busy_ratio = _safe_float(metrics.get("d0_busy_ratio"))

    if branch_exit_visits >= 5 and to_delivery_rate < 0.1:
        failures.append(
            {
                "reason": "branch_underutilized",
                "evidence": {
                    "branch_exit_visits": branch_exit_visits,
                    "branch_to_delivery": branch_to_delivery,
                    "to_delivery_rate": round(to_delivery_rate, 4),
                },
            }
        )
    if branch_exit_visits >= 5 and wait_rate > 0.2:
        failures.append(
            {
                "reason": "branch_wait_stall",
                "evidence": {
                    "branch_exit_visits": branch_exit_visits,
                    "branch_waits": branch_waits,
                    "branch_wait_rate": round(wait_rate, 4),
                },
            }
        )
    if queue_violations > 0:
        failures.append(
            {
                "reason": "queue_semantics_violation",
                "evidence": {"queue_semantics_violation": queue_violations},
            }
        )
    if ring_direction_violations > 0:
        failures.append(
            {
                "reason": "ring_direction_violation",
                "evidence": {"ring_direction_violation": ring_direction_violations},
            }
        )
    if rejoin_denials > max(2.0, rounds * 0.03) and rejoin_backlog_avg >= 1.0:
        failures.append(
            {
                "reason": "rejoin_admission_pressure",
                "evidence": {
                    "rejoin_denials": rejoin_denials,
                    "rejoin_backlog_avg": round(rejoin_backlog_avg, 4),
                    "rejoin_backlog_max": rejoin_backlog_max,
                },
            }
        )
    if spacing_share >= 0.4 and wait_spacing >= 5:
        failures.append(
            {
                "reason": "spacing_guard_dominant_wait",
                "evidence": {
                    "wait_due_to_spacing_guard": wait_spacing,
                    "wait_total": wait_total,
                    "spacing_wait_share": round(spacing_share, 4),
                },
            }
        )
    if drop_off_actions <= 1 and deliver_bots_avg >= 1.0:
        failures.append(
            {
                "reason": "delivery_conversion_low",
                "evidence": {
                    "drop_off_actions": drop_off_actions,
                    "deliver_bots_avg": round(deliver_bots_avg, 4),
                },
            }
        )
    if d0_busy_ratio > 0 and d0_busy_ratio < 0.05 and deliver_bots_avg >= 0.8:
        failures.append(
            {
                "reason": "dropoff_underutilized",
                "evidence": {
                    "d0_busy_ratio": round(d0_busy_ratio, 4),
                    "deliver_bots_avg": round(deliver_bots_avg, 4),
                },
            }
        )
    return failures


def analyze(
    *,
    decision_trace_path: Path,
    order_trace_path: Path | None = None,
    result_path: Path | None = None,
) -> dict[str, Any]:
    decision_rows = _load_jsonl(decision_trace_path)
    order_payload = _load_json(order_trace_path) if order_trace_path is not None else None
    result_payload = _load_json(result_path) if result_path is not None else None

    if not decision_rows:
        return {
            "inputs": {
                "decision_trace": str(decision_trace_path),
                "order_trace": str(order_trace_path) if order_trace_path is not None else None,
                "result": str(result_path) if result_path is not None else None,
            },
            "error": "decision_trace.jsonl has no readable rows",
        }

    totals: dict[str, float] = {
        "branch_exit_visits": 0.0,
        "branch_to_delivery": 0.0,
        "branch_continue_moves": 0.0,
        "branch_waits": 0.0,
        "rejoin_branch_visits": 0.0,
        "rejoin_admissions": 0.0,
        "rejoin_denials": 0.0,
        "wait_due_to_spacing_guard": 0.0,
        "wait_due_to_collision_block": 0.0,
        "wait_due_to_no_assignment": 0.0,
        "queue_semantics_violation": 0.0,
        "ring_direction_violation": 0.0,
        "drop_off_actions": 0.0,
        "deliver_bots_sum": 0.0,
        "rejoin_backlog_sum": 0.0,
        "rejoin_backlog_max": 0.0,
        "drop_queue_len_sum": 0.0,
    }

    rounds = 0
    first_round = _safe_int(decision_rows[0].get("round"), 0)
    last_round = _safe_int(decision_rows[-1].get("round"), first_round)
    for row in decision_rows:
        rounds += 1
        telemetry = row.get("telemetry", {})
        if not isinstance(telemetry, dict):
            telemetry = {}
        debug = row.get("round_debug", {})
        if not isinstance(debug, dict):
            debug = {}
        waits = row.get("wait_reason_by_bot", {})
        if not isinstance(waits, dict):
            waits = {}
        actions = row.get("actions", [])
        if not isinstance(actions, list):
            actions = []

        branch_exit_visits = _safe_float(telemetry.get("branch_exit_visits", debug.get("branch_exit_visits", 0.0)))
        branch_to_delivery = _safe_float(telemetry.get("branch_to_delivery", debug.get("branch_to_delivery", 0.0)))
        branch_continue_moves = _safe_float(telemetry.get("branch_continue_moves", debug.get("branch_continue_moves", 0.0)))
        branch_waits = _safe_float(telemetry.get("branch_waits", debug.get("branch_waits", 0.0)))

        rejoin_branch_visits = _safe_float(telemetry.get("rejoin_branch_visits", debug.get("rejoin_branch_visits", 0.0)))
        rejoin_admissions = _safe_float(telemetry.get("rejoin_admissions", debug.get("rejoin_admissions", 0.0)))
        rejoin_denials = _safe_float(telemetry.get("rejoin_denials", debug.get("rejoin_denials", 0.0)))
        rejoin_backlog = _safe_float(telemetry.get("rejoin_backlog", telemetry.get("rejoin_queue_len", 0.0)))

        wait_spacing = _safe_float(telemetry.get("wait_due_to_spacing_guard", 0.0))
        wait_collision = _safe_float(telemetry.get("wait_due_to_collision_block", 0.0))
        wait_no_assignment = _safe_float(telemetry.get("wait_due_to_no_assignment", 0.0))

        if wait_spacing <= 0:
            wait_spacing = float(sum(1 for reason in waits.values() if str(reason) == "wait_due_to_spacing_guard"))
        if wait_collision <= 0:
            wait_collision = float(sum(1 for reason in waits.values() if str(reason) == "wait_due_to_collision_block"))
        if wait_no_assignment <= 0:
            wait_no_assignment = float(sum(1 for reason in waits.values() if str(reason) == "wait_due_to_no_assignment"))

        queue_semantics_violation = _safe_float(telemetry.get("queue_semantics_violation", debug.get("queue_semantics_violation", 0.0)))
        ring_direction_violation = _safe_float(telemetry.get("ring_direction_violation", debug.get("ring_direction_violation", 0.0)))
        deliver_bots = _safe_float(telemetry.get("deliver_bots", 0.0))
        drop_queue_len = _safe_float(telemetry.get("drop_queue_len", 0.0))
        drop_off_actions = float(sum(1 for action in actions if str(action.get("action", "")) == "drop_off"))

        totals["branch_exit_visits"] += branch_exit_visits
        totals["branch_to_delivery"] += branch_to_delivery
        totals["branch_continue_moves"] += branch_continue_moves
        totals["branch_waits"] += branch_waits
        totals["rejoin_branch_visits"] += rejoin_branch_visits
        totals["rejoin_admissions"] += rejoin_admissions
        totals["rejoin_denials"] += rejoin_denials
        totals["wait_due_to_spacing_guard"] += wait_spacing
        totals["wait_due_to_collision_block"] += wait_collision
        totals["wait_due_to_no_assignment"] += wait_no_assignment
        totals["queue_semantics_violation"] += queue_semantics_violation
        totals["ring_direction_violation"] += ring_direction_violation
        totals["drop_off_actions"] += drop_off_actions
        totals["deliver_bots_sum"] += deliver_bots
        totals["rejoin_backlog_sum"] += rejoin_backlog
        totals["rejoin_backlog_max"] = max(totals["rejoin_backlog_max"], rejoin_backlog)
        totals["drop_queue_len_sum"] += drop_queue_len

    avg_div = float(max(1, rounds))
    metrics = {
        "rounds": int(rounds),
        "first_round": int(first_round),
        "last_round": int(last_round),
        "branch_exit_visits": float(totals["branch_exit_visits"]),
        "branch_to_delivery": float(totals["branch_to_delivery"]),
        "branch_continue_moves": float(totals["branch_continue_moves"]),
        "branch_waits": float(totals["branch_waits"]),
        "branch_to_delivery_rate": float(totals["branch_to_delivery"] / max(1.0, totals["branch_exit_visits"])),
        "rejoin_branch_visits": float(totals["rejoin_branch_visits"]),
        "rejoin_admissions": float(totals["rejoin_admissions"]),
        "rejoin_denials": float(totals["rejoin_denials"]),
        "rejoin_backlog_avg": float(totals["rejoin_backlog_sum"] / avg_div),
        "rejoin_backlog_max": float(totals["rejoin_backlog_max"]),
        "wait_due_to_spacing_guard": float(totals["wait_due_to_spacing_guard"]),
        "wait_due_to_collision_block": float(totals["wait_due_to_collision_block"]),
        "wait_due_to_no_assignment": float(totals["wait_due_to_no_assignment"]),
        "queue_semantics_violation": float(totals["queue_semantics_violation"]),
        "ring_direction_violation": float(totals["ring_direction_violation"]),
        "drop_off_actions": float(totals["drop_off_actions"]),
        "d0_busy_ratio": float(totals["drop_off_actions"] / avg_div),
        "deliver_bots_avg": float(totals["deliver_bots_sum"] / avg_div),
        "drop_queue_len_avg": float(totals["drop_queue_len_sum"] / avg_div),
    }
    failures = _classify_failures(metrics=metrics)

    order_summary: dict[str, Any] = {}
    if isinstance(order_payload, dict):
        trace = order_payload.get("trace", [])
        if isinstance(trace, list) and trace:
            active_switch_rounds: list[int] = []
            prev_active_idx: int | None = None
            for row in trace:
                if not isinstance(row, dict):
                    continue
                active_idx = row.get("active_order_index")
                if not isinstance(active_idx, int):
                    continue
                if prev_active_idx is not None and active_idx != prev_active_idx:
                    active_switch_rounds.append(_safe_int(row.get("round"), 0))
                prev_active_idx = active_idx
            order_summary = {
                "order_trace_rounds": len(trace),
                "active_order_switch_rounds": active_switch_rounds,
            }

    result_summary: dict[str, Any] = {}
    if isinstance(result_payload, dict):
        for key in ("score", "items_delivered", "orders_completed", "idle_steps", "rounds_played"):
            if key in result_payload:
                result_summary[key] = result_payload[key]
    delivered_items = _safe_float(result_summary.get("items_delivered"))
    drop_actions = _safe_float(metrics.get("drop_off_actions"))
    metrics["items_per_drop_action"] = float(delivered_items / drop_actions) if drop_actions > 0 else 0.0

    return {
        "inputs": {
            "decision_trace": str(decision_trace_path),
            "order_trace": str(order_trace_path) if order_trace_path is not None else None,
            "result": str(result_path) if result_path is not None else None,
        },
        "summary": {
            **result_summary,
            **order_summary,
        },
        "metrics": metrics,
        "failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze orbit-wall live logs and classify conveyor failures")
    parser.add_argument("--decision-trace", type=str, required=True, help="Path to decision_trace.jsonl")
    parser.add_argument("--order-trace", type=str, default="", help="Optional path to order_trace.json")
    parser.add_argument("--result", type=str, default="", help="Optional path to result.json")
    parser.add_argument("--output", type=str, default="", help="Optional JSON output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    decision_trace_path = Path(args.decision_trace).resolve()
    order_trace_path = Path(args.order_trace).resolve() if str(args.order_trace).strip() else None
    result_path = Path(args.result).resolve() if str(args.result).strip() else None
    payload = analyze(
        decision_trace_path=decision_trace_path,
        order_trace_path=order_trace_path,
        result_path=result_path,
    )
    encoded = json.dumps(payload, indent=2, ensure_ascii=True)
    if str(args.output).strip():
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(encoded + "\n", encoding="utf-8")
        print(f"[orbit-wall-log-analyzer] wrote {output_path}")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
