from __future__ import annotations

import json
from pathlib import Path

from scripts.check_conversion_acceptance import GateThresholds, check_artifact_root


def _write_run(
    run_dir: Path,
    *,
    rounds: int,
    score: int,
    orders_completed: int,
    items_delivered: int,
    wait_no_target: float,
    wait_no_assignment: float,
    coupling_break: float,
    commitment_stagnation: float,
    throughput_breach: float,
    delivery_lane_breach: float,
    picks_per_round: int,
    drops_per_round: int,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "score": score,
        "orders_completed": orders_completed,
        "items_delivered": items_delivered,
        "rounds_played": rounds,
    }
    (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    with (run_dir / "decision_trace.jsonl").open("w", encoding="utf-8") as handle:
        for idx in range(rounds):
            actions = []
            actions.extend([{"bot": bot, "action": "pick_up"} for bot in range(picks_per_round)])
            actions.extend([{"bot": 100 + bot, "action": "drop_off"} for bot in range(drops_per_round)])
            telemetry = {
                "wait_due_to_no_target": wait_no_target,
                "wait_due_to_no_assignment": wait_no_assignment,
                "conversion_guard_pickup_drop_coupling_break": coupling_break,
                "conversion_guard_commitment_stagnation": commitment_stagnation,
                "conversion_guard_throughput_lane_floor_breach": throughput_breach,
                "conversion_guard_delivery_lane_breach": delivery_lane_breach,
                "conversion_guard_weak_conversion_window": 0.0,
            }
            row = {
                "round": idx,
                "actions": actions,
                "telemetry": telemetry,
                "state": {"bots": [{"id": bot} for bot in range(10)]},
            }
            handle.write(json.dumps(row) + "\n")


def test_conversion_acceptance_detects_pass_and_fail(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "expert"
    _write_run(
        root / "run_20260310_120000",
        rounds=20,
        score=88,
        orders_completed=9,
        items_delivered=40,
        wait_no_target=0.0,
        wait_no_assignment=2.0,
        coupling_break=0.0,
        commitment_stagnation=0.0,
        throughput_breach=0.0,
        delivery_lane_breach=0.0,
        picks_per_round=2,
        drops_per_round=1,
    )
    _write_run(
        root / "run_20260310_120100",
        rounds=20,
        score=2,
        orders_completed=0,
        items_delivered=1,
        wait_no_target=8.0,
        wait_no_assignment=4.0,
        coupling_break=1.0,
        commitment_stagnation=1.0,
        throughput_breach=1.0,
        delivery_lane_breach=1.0,
        picks_per_round=2,
        drops_per_round=0,
    )

    payload = check_artifact_root(
        artifact_root=tmp_path / "artifacts",
        difficulty="expert",
        limit=2,
        thresholds=GateThresholds(),
    )

    assert payload["summary"]["runs"] == 2
    assert payload["summary"]["overall_pass_runs"] == 1
    assert payload["summary"]["gate_fail_counts"]["target_liveness"] >= 1
    assert payload["summary"]["gate_fail_counts"]["drop_conversion_floor"] >= 1
    assert payload["summary"]["gate_fail_counts"]["pickup_drop_coupling"] >= 1
