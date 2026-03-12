from __future__ import annotations

import json
from pathlib import Path

from scripts.order_cycle_diagnostics import build_order_cycle_diagnostics


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def test_order_cycle_diagnostics_outputs_rounds_events_and_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260311_000000"
    run_dir.mkdir(parents=True, exist_ok=True)
    decision_trace = run_dir / "decision_trace.jsonl"
    order_trace = run_dir / "order_trace.json"
    result_path = run_dir / "result.json"

    rows = [
        {
            "round": 0,
            "state": {
                "round": 0,
                "active_order_index": 0,
                "bots": [{"id": 0, "inventory": []}, {"id": 1, "inventory": []}],
                "orders": [
                    {
                        "id": "order_0",
                        "status": "active",
                        "items_required": ["apples"],
                        "items_delivered": [],
                    },
                    {
                        "id": "order_1",
                        "status": "preview",
                        "items_required": ["milk"],
                        "items_delivered": [],
                    },
                ],
            },
            "actions": [{"bot": 0, "action": "pick_up"}],
            "telemetry": {"wait_due_to_no_target": 0.0},
            "assignment_snapshot": {
                "0": {"target_type": "pick_item", "pickup_pos": [2, 2], "source": "greedy_candidate"},
                "1": {"target_type": "idle", "pickup_pos": None, "source": "idle_fallback"},
            },
            "wait_reason_by_bot": {},
        },
        {
            "round": 1,
            "state": {
                "round": 1,
                "active_order_index": 1,
                "bots": [{"id": 0, "inventory": []}, {"id": 1, "inventory": []}],
                "orders": [
                    {
                        "id": "order_1",
                        "status": "active",
                        "items_required": ["milk"],
                        "items_delivered": [],
                    },
                    {
                        "id": "order_2",
                        "status": "preview",
                        "items_required": ["bread"],
                        "items_delivered": [],
                    },
                ],
            },
            "actions": [{"bot": 0, "action": "drop_off"}],
            "telemetry": {"wait_due_to_no_target": 0.0},
            "assignment_snapshot": {
                "0": {"target_type": "deliver", "pickup_pos": None, "source": "deliver_dropoff_ready"},
                "1": {"target_type": "pre_pick", "pickup_pos": [3, 3], "source": "greedy_candidate"},
            },
            "wait_reason_by_bot": {},
        },
    ]
    _write_jsonl(decision_trace, rows)
    order_trace.write_text(json.dumps({"trace": [{"round": 0}, {"round": 1}]}, ensure_ascii=True), encoding="utf-8")
    result_path.write_text(
        json.dumps(
            {
                "score": 10,
                "orders_completed": 1,
                "items_delivered": 1,
                "rounds_played": 2,
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    output_dir = run_dir / "order_cycle_diagnostics"
    summary = build_order_cycle_diagnostics(
        decision_trace_path=decision_trace,
        order_trace_path=order_trace,
        result_path=result_path,
        output_dir=output_dir,
    )

    round_diag = output_dir / "round_diagnostics.jsonl"
    events = output_dir / "order_events.jsonl"
    report = output_dir / "order_cycle_report.json"

    assert round_diag.exists()
    assert events.exists()
    assert report.exists()

    event_rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_names = {str(row.get("event", "")) for row in event_rows}
    assert "order_started" in event_names
    assert "order_completed" in event_names

    assert summary["result"]["score"] == 10
    assert summary["diagnostics"]["rounds"] == 2
