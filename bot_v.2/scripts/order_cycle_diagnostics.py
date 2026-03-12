"""Build normalized order-cycle diagnostics from run artifacts."""
from __future__ import annotations

import argparse
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _remaining_counter(order_payload: dict[str, Any]) -> Counter[str]:
    required = list(order_payload.get("items_required", [])) if isinstance(order_payload, dict) else []
    delivered = list(order_payload.get("items_delivered", [])) if isinstance(order_payload, dict) else []
    need = Counter(str(item_type) for item_type in required)
    for item_type in delivered:
        item_key = str(item_type)
        if need.get(item_key, 0) > 0:
            need[item_key] -= 1
    return Counter({k: v for k, v in need.items() if int(v) > 0})


def _active_and_preview(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    orders = list(state.get("orders", [])) if isinstance(state.get("orders"), list) else []
    active = {}
    preview = {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status", "")).strip().lower()
        if status == "active" and not active:
            active = order
        elif status == "preview" and not preview:
            preview = order
    return active, preview


def build_order_cycle_diagnostics(
    *,
    decision_trace_path: Path,
    order_trace_path: Path | None,
    result_path: Path | None,
    output_dir: Path,
) -> dict[str, Any]:
    rows = _load_jsonl(decision_trace_path)
    order_trace = _load_json(order_trace_path) if order_trace_path is not None else {}
    result = _load_json(result_path) if result_path is not None else {}
    output_dir.mkdir(parents=True, exist_ok=True)

    round_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    wait_totals = Counter()
    delivered_total_prev = 0
    delivered_delta_window: deque[int] = deque(maxlen=8)
    pickup_window: deque[int] = deque(maxlen=8)
    drop_window: deque[int] = deque(maxlen=8)
    commitment_stagnation_streak = 0
    coupling_break_streak = 0
    tail_open_prev = False
    secured_prev = False
    preview_enabled_prev = False
    active_order_id_prev: str | None = None
    active_order_index_prev: int | None = None
    active_total_prev = 0
    completion_rounds: list[int] = []

    for row in rows:
        state = row.get("state", {})
        if not isinstance(state, dict):
            state = {}
        telemetry = row.get("telemetry", {})
        if not isinstance(telemetry, dict):
            telemetry = {}
        assignment_snapshot = row.get("assignment_snapshot", {})
        if not isinstance(assignment_snapshot, dict):
            assignment_snapshot = {}
        wait_reason_by_bot = row.get("wait_reason_by_bot", {})
        if not isinstance(wait_reason_by_bot, dict):
            wait_reason_by_bot = {}
        actions = row.get("actions", [])
        if not isinstance(actions, list):
            actions = []

        round_num = _safe_int(row.get("round"), _safe_int(state.get("round"), 0))
        active_order, preview_order = _active_and_preview(state)
        active_remaining = _remaining_counter(active_order)
        preview_remaining = _remaining_counter(preview_order)
        active_remaining_delivered_only = int(sum(active_remaining.values()))
        preview_remaining_total = int(sum(preview_remaining.values()))
        active_distinct_missing = int(sum(1 for v in active_remaining.values() if int(v) > 0))

        bots = list(state.get("bots", [])) if isinstance(state.get("bots"), list) else []
        bot_inventory_by_id: dict[int, list[str]] = {}
        active_carry = Counter()
        bots_with_active_cargo = 0
        bots_with_preview_only_cargo = 0
        for bot in bots:
            if not isinstance(bot, dict):
                continue
            bot_id = _safe_int(bot.get("id"), -1)
            inventory = [str(item_type) for item_type in (bot.get("inventory", []) or [])]
            bot_inventory_by_id[bot_id] = inventory
            bot_has_active = False
            for item_type in inventory:
                if active_remaining.get(item_type, 0) > active_carry.get(item_type, 0):
                    active_carry[item_type] += 1
                    bot_has_active = True
            if bot_has_active:
                bots_with_active_cargo += 1
            elif inventory:
                bots_with_preview_only_cargo += 1

        active_committed_reliable = int(sum(active_carry.values()))
        active_secured = bool(
            active_remaining_delivered_only <= 0
            or active_committed_reliable >= active_remaining_delivered_only
        )
        active_tail_open = bool(
            active_remaining_delivered_only > 0
            and (active_remaining_delivered_only <= 3 or active_distinct_missing <= 2)
        )

        conversion_floor_target = 0
        if active_remaining_delivered_only > 0 and bots_with_active_cargo > 0:
            conversion_floor_target = max(1, min(3, bots_with_active_cargo))

        action_pickups = sum(1 for action in actions if str(action.get("action", "")) == "pick_up")
        action_dropoffs = sum(1 for action in actions if str(action.get("action", "")) == "drop_off")

        total_delivered = 0
        for order in list(state.get("orders", [])) if isinstance(state.get("orders"), list) else []:
            if isinstance(order, dict):
                total_delivered += len(list(order.get("items_delivered", [])))
        delivered_delta = max(0, int(total_delivered - delivered_total_prev))
        delivered_total_prev = int(total_delivered)

        pickup_window.append(int(action_pickups))
        drop_window.append(int(action_dropoffs))
        delivered_delta_window.append(int(delivered_delta))

        pickup_window_total = int(sum(pickup_window))
        drop_window_total = int(sum(drop_window))
        delivered_delta_window_total = int(sum(delivered_delta_window))

        role_retriever = 0
        role_converter = 0
        role_finisher = 0
        bots_without_target = 0
        preview_assignments = 0
        throughput_assignments = 0
        target_shelves: Counter[tuple[int, int]] = Counter()
        for bot_id_raw, assign in assignment_snapshot.items():
            if not isinstance(assign, dict):
                continue
            target_type = str(assign.get("target_type", "none"))
            source = str(assign.get("source", ""))
            bot_id = _safe_int(bot_id_raw, -1)
            if target_type in {"pick_item", "pre_pick"}:
                role_retriever += 1
            elif target_type == "deliver":
                role_converter += 1
                inventory = bot_inventory_by_id.get(bot_id, [])
                if any(active_remaining.get(item_type, 0) > 0 for item_type in inventory):
                    role_finisher += 1
            if target_type in {"none", "idle"}:
                bots_without_target += 1
            if target_type == "pre_pick":
                preview_assignments += 1
            if target_type in {"pre_pick", "secondary_reposition"} or source.startswith("secondary_"):
                throughput_assignments += 1
            pickup_pos = assign.get("pickup_pos")
            if isinstance(pickup_pos, list) and len(pickup_pos) >= 2:
                target_shelves[(int(pickup_pos[0]), int(pickup_pos[1]))] += 1
        duplicated_target_shelves = int(sum(1 for count in target_shelves.values() if int(count) > 1))

        wait_no_target = int(
            _safe_float(
                telemetry.get(
                    "wait_due_to_no_target",
                    sum(1 for reason in wait_reason_by_bot.values() if str(reason) == "wait_due_to_no_target"),
                )
            )
        )
        wait_no_assignment = int(
            _safe_float(
                telemetry.get(
                    "wait_due_to_no_assignment",
                    sum(
                        1
                        for reason in wait_reason_by_bot.values()
                        if str(reason) == "wait_due_to_no_assignment"
                    ),
                )
            )
        )
        wait_collision = int(_safe_float(telemetry.get("wait_due_to_collision_block", 0.0)))
        wait_queue = int(
            _safe_float(telemetry.get("wait_due_to_stopline", 0.0))
            + _safe_float(telemetry.get("wait_due_to_vacate_dropoff_failed", 0.0))
        )
        wait_totals.update(
            {
                "no_target": wait_no_target,
                "no_assignment": wait_no_assignment,
                "collision": wait_collision,
                "queue": wait_queue,
            }
        )

        if active_remaining_delivered_only > active_committed_reliable and delivered_delta <= 0:
            commitment_stagnation_streak += 1
        else:
            commitment_stagnation_streak = 0
        commitment_stagnation = bool(
            _safe_float(telemetry.get("conversion_guard_commitment_stagnation", 0.0)) > 0.5
            or commitment_stagnation_streak >= 8
        )

        derived_coupling_break = bool(
            active_remaining_delivered_only > 0
            and pickup_window_total >= 5
            and drop_window_total <= 0
            and delivered_delta_window_total <= 0
        )
        coupling_break = bool(
            _safe_float(telemetry.get("conversion_guard_pickup_drop_coupling_break", 0.0)) > 0.5
            or derived_coupling_break
        )
        if coupling_break:
            coupling_break_streak += 1
        else:
            coupling_break_streak = 0

        throughput_lane_breach = bool(
            _safe_float(telemetry.get("conversion_guard_throughput_lane_floor_breach", 0.0)) > 0.5
            or (
                active_remaining_delivered_only > 0
                and throughput_assignments <= 0
                and commitment_stagnation_streak >= 4
            )
        )
        delivery_lane_guarantee_breached = bool(
            active_remaining_delivered_only > 0
            and bots_with_active_cargo > 0
            and role_converter < conversion_floor_target
            and action_dropoffs <= 0
        )

        round_record = {
            "round": int(round_num),
            "active_order_index": _safe_int(state.get("active_order_index"), _safe_int(row.get("active_order_index"), 0)),
            "active_order_id": str(active_order.get("id", "")),
            "preview_order_id": str(preview_order.get("id", "")),
            "active_remaining_delivered_only": int(active_remaining_delivered_only),
            "active_committed_reliable": int(active_committed_reliable),
            "active_tail_open": bool(active_tail_open),
            "active_secured": bool(active_secured),
            "conversion_floor_target": int(conversion_floor_target),
            "role_counts": {
                "retriever": int(role_retriever),
                "converter": int(role_converter),
                "finisher": int(role_finisher),
            },
            "bots_with_active_cargo": int(bots_with_active_cargo),
            "bots_with_preview_only_cargo": int(bots_with_preview_only_cargo),
            "drop_off_actions": int(action_dropoffs),
            "delivered_items_delta": int(delivered_delta),
            "bots_without_target": int(bots_without_target),
            "duplicated_target_shelves": int(duplicated_target_shelves),
            "waits": {
                "no_target": int(wait_no_target),
                "no_assignment": int(wait_no_assignment),
                "collision": int(wait_collision),
                "queue": int(wait_queue),
            },
            "commitment_stagnation": bool(commitment_stagnation),
            "coupling_break": bool(coupling_break),
            "throughput_lane_breach": bool(throughput_lane_breach),
            "delivery_lane_guarantee_breach": bool(delivery_lane_guarantee_breached),
            "preview_assignments": int(preview_assignments),
            "preview_remaining": int(preview_remaining_total),
        }
        round_rows.append(round_record)

        active_order_id = str(active_order.get("id", ""))
        active_order_index = _safe_int(state.get("active_order_index"), _safe_int(row.get("active_order_index"), 0))
        active_total = len(list(active_order.get("items_required", []))) if isinstance(active_order, dict) else 0
        if active_order_id_prev is None:
            events.append(
                {
                    "round": int(round_num),
                    "event": "order_started",
                    "active_order_id": active_order_id,
                    "active_order_index": int(active_order_index),
                }
            )
        elif active_order_id != active_order_id_prev or active_order_index != active_order_index_prev:
            completion_rounds.append(int(round_num))
            events.append(
                {
                    "round": int(round_num),
                    "event": "order_completed",
                    "active_order_id": str(active_order_id_prev or ""),
                    "active_order_index": int(active_order_index_prev or 0),
                    "active_order_total_items": int(active_total_prev),
                }
            )
            events.append(
                {
                    "round": int(round_num),
                    "event": "order_started",
                    "active_order_id": active_order_id,
                    "active_order_index": int(active_order_index),
                }
            )

        if active_tail_open and not tail_open_prev:
            events.append(
                {
                    "round": int(round_num),
                    "event": "tail_opened",
                    "active_order_id": active_order_id,
                    "remaining_items": int(active_remaining_delivered_only),
                }
            )
        if active_secured and not secured_prev:
            events.append(
                {
                    "round": int(round_num),
                    "event": "active_secured",
                    "active_order_id": active_order_id,
                    "remaining_items": int(active_remaining_delivered_only),
                }
            )
        preview_enabled = bool(preview_assignments > 0)
        if preview_enabled and not preview_enabled_prev:
            events.append(
                {
                    "round": int(round_num),
                    "event": "preview_enabled",
                    "active_order_id": active_order_id,
                    "preview_order_id": str(preview_order.get("id", "")),
                }
            )
        if delivery_lane_guarantee_breached:
            events.append(
                {
                    "round": int(round_num),
                    "event": "conversion_floor_breached",
                    "active_order_id": active_order_id,
                    "conversion_floor_target": int(conversion_floor_target),
                    "converter_role_count": int(role_converter),
                }
            )
        if coupling_break and coupling_break_streak == 1:
            events.append(
                {
                    "round": int(round_num),
                    "event": "pickup_drop_coupling_broken",
                    "active_order_id": active_order_id,
                    "pickup_window": int(pickup_window_total),
                    "drop_window": int(drop_window_total),
                    "delivered_delta_window": int(delivered_delta_window_total),
                }
            )

        tail_open_prev = bool(active_tail_open)
        secured_prev = bool(active_secured)
        preview_enabled_prev = bool(preview_enabled)
        active_order_id_prev = active_order_id
        active_order_index_prev = int(active_order_index)
        active_total_prev = int(active_total)

    round_path = output_dir / "round_diagnostics.jsonl"
    with round_path.open("w", encoding="utf-8") as handle:
        for row in round_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    event_path = output_dir / "order_events.jsonl"
    with event_path.open("w", encoding="utf-8") as handle:
        for row in events:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    summary = {
        "artifact_dir": str(decision_trace_path.parent.resolve()),
        "result": {
            "score": _safe_int(result.get("score")),
            "orders_completed": _safe_int(result.get("orders_completed")),
            "items_delivered": _safe_int(result.get("items_delivered")),
            "rounds_played": _safe_int(result.get("rounds_played"), len(round_rows)),
        },
        "diagnostics": {
            "rounds": len(round_rows),
            "tail_open_rounds": sum(1 for row in round_rows if bool(row.get("active_tail_open"))),
            "secured_rounds": sum(1 for row in round_rows if bool(row.get("active_secured"))),
            "coupling_break_rounds": sum(1 for row in round_rows if bool(row.get("coupling_break"))),
            "commitment_stagnation_rounds": sum(1 for row in round_rows if bool(row.get("commitment_stagnation"))),
            "throughput_lane_breach_rounds": sum(1 for row in round_rows if bool(row.get("throughput_lane_breach"))),
            "delivery_lane_guarantee_breach_rounds": sum(
                1 for row in round_rows if bool(row.get("delivery_lane_guarantee_breach"))
            ),
            "wait_totals": {
                "no_target": int(wait_totals.get("no_target", 0)),
                "no_assignment": int(wait_totals.get("no_assignment", 0)),
                "collision": int(wait_totals.get("collision", 0)),
                "queue": int(wait_totals.get("queue", 0)),
            },
            "completion_rounds": [int(r) for r in completion_rounds[:3]],
        },
        "order_trace_rounds": len(list(order_trace.get("trace", []))) if isinstance(order_trace.get("trace"), list) else 0,
    }

    summary_path = output_dir / "order_cycle_report.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate normalized order-cycle diagnostics.")
    parser.add_argument("--run-dir", type=str, required=True, help="Path to run_* artifact directory")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="",
        help="Optional output directory (default: <run-dir>/order_cycle_diagnostics)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")
    decision_trace = run_dir / "decision_trace.jsonl"
    if not decision_trace.exists():
        raise SystemExit(f"Missing decision_trace.jsonl: {decision_trace}")
    output_dir = (
        Path(args.output_dir).resolve()
        if str(args.output_dir).strip()
        else run_dir / "order_cycle_diagnostics"
    )
    summary = build_order_cycle_diagnostics(
        decision_trace_path=decision_trace,
        order_trace_path=(run_dir / "order_trace.json"),
        result_path=(run_dir / "result.json"),
        output_dir=output_dir,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
