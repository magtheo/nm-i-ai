from __future__ import annotations

import json
from pathlib import Path

from bot._simulator import (
    _evaluate_params,
    baseline_decision,
    decision_to_params,
    default_generator_from_dataset,
    load_dataset_snapshot,
    load_medium_dataset_snapshot,
    mine_dataset,
    mine_medium_dataset,
    save_dataset_snapshot,
    save_medium_dataset_snapshot,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_mine_medium_dataset_reads_order_trace_and_variants(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260303_000001"
    state0 = {
        "grid": {"width": 6, "height": 6, "walls": []},
        "items": [{"id": "item_0", "type": "eggs", "position": [2, 2]}],
        "drop_off": [1, 1],
        "bots": [
            {"id": 0, "position": [1, 1], "inventory": []},
            {"id": 1, "position": [1, 1], "inventory": []},
            {"id": 2, "position": [1, 1], "inventory": []},
        ],
        "orders": [
            {"id": "order_0", "items_required": ["eggs", "cream", "bread"]},
            {"id": "order_1", "items_required": ["cheese", "milk", "cheese"]},
        ],
        "total_orders": 50,
        "max_rounds": 300,
    }
    _write_json(run_dir / "state0.json", state0)

    order_trace = {
        "trace": [
            {
                "round": 0,
                "active": {"id": "order_0", "items_required": ["eggs", "cream", "bread"]},
                "preview": {"id": "order_1", "items_required": ["cheese", "milk", "cheese"]},
            },
            {
                "round": 8,
                "active": {"id": "order_1", "items_required": ["milk", "milk", "cheese"]},
                "preview": {"id": "order_2", "items_required": ["pasta", "pasta", "rice"]},
            },
            {
                "round": 16,
                "active": {"id": "order_2", "items_required": ["pasta", "pasta", "rice"]},
                "preview": {"id": "order_3", "items_required": ["yogurt", "yogurt", "milk"]},
            },
        ]
    }
    _write_json(run_dir / "order_trace.json", order_trace)

    dataset = mine_medium_dataset(tmp_path)

    assert len(dataset.observed_orders_exact) >= 4
    assert dataset.observed_orders_exact[2] == ["pasta", "pasta", "rice"]
    assert dataset.observed_orders_exact[3] == ["yogurt", "yogurt", "milk"]

    variants_order_1 = dataset.observed_order_variants[1]
    assert variants_order_1[0]["items_required"] == ["cheese", "milk", "cheese"]
    assert variants_order_1[0]["count"] == 1
    assert variants_order_1[1]["items_required"] == ["milk", "milk", "cheese"]
    assert variants_order_1[1]["count"] == 1


def test_dataset_snapshot_roundtrip(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260303_000002"
    state0 = {
        "grid": {"width": 8, "height": 7, "walls": [[0, 0], [1, 1]]},
        "items": [{"id": "item_0", "type": "milk", "position": [2, 2]}],
        "drop_off": [1, 5],
        "bots": [
            {"id": 0, "position": [6, 5], "inventory": []},
            {"id": 1, "position": [6, 5], "inventory": []},
            {"id": 2, "position": [6, 5], "inventory": []},
        ],
        "orders": [
            {"id": "order_0", "items_required": ["milk", "bread", "eggs"]},
        ],
        "total_orders": 50,
        "max_rounds": 300,
    }
    _write_json(run_dir / "state0.json", state0)
    dataset = mine_medium_dataset(tmp_path)

    snapshot_path = tmp_path / "frozen_medium_dataset.json"
    save_medium_dataset_snapshot(dataset, snapshot_path, source="test")
    loaded = load_medium_dataset_snapshot(snapshot_path)

    assert loaded.grid.width == dataset.grid.width
    assert loaded.grid.height == dataset.grid.height
    assert loaded.grid.walls == dataset.grid.walls
    assert loaded.drop_off == dataset.drop_off
    assert loaded.bot_starts == dataset.bot_starts
    assert loaded.items == dataset.items
    assert loaded.observed_orders_exact == dataset.observed_orders_exact
    assert loaded.observed_order_variants == dataset.observed_order_variants
    assert loaded.order_templates == dataset.order_templates
    assert loaded.total_orders == dataset.total_orders
    assert loaded.max_rounds == dataset.max_rounds


def test_generic_dataset_api_roundtrip_matches_legacy_aliases(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260303_000002"
    state0 = {
        "grid": {"width": 8, "height": 7, "walls": [[0, 0], [1, 1]]},
        "items": [{"id": "item_0", "type": "milk", "position": [2, 2]}],
        "drop_off": [1, 5],
        "bots": [
            {"id": 0, "position": [6, 5], "inventory": []},
            {"id": 1, "position": [6, 5], "inventory": []},
            {"id": 2, "position": [6, 5], "inventory": []},
        ],
        "orders": [
            {"id": "order_0", "items_required": ["milk", "bread", "eggs"]},
        ],
        "total_orders": 50,
        "max_rounds": 300,
    }
    _write_json(run_dir / "state0.json", state0)

    dataset = mine_dataset(tmp_path)
    legacy = mine_medium_dataset(tmp_path)

    snapshot_path = tmp_path / "generic_dataset.json"
    save_dataset_snapshot(dataset, snapshot_path, source="generic-test")
    loaded = load_dataset_snapshot(snapshot_path)

    assert dataset == legacy
    assert loaded == dataset


def test_eval_cache_separates_known_orders_mode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260303_000003"
    state0 = {
        "grid": {"width": 7, "height": 7, "walls": []},
        "items": [
            {"id": "item_0", "type": "milk", "position": [2, 2]},
            {"id": "item_1", "type": "bread", "position": [4, 2]},
        ],
        "drop_off": [1, 5],
        "bots": [
            {"id": 0, "position": [6, 5], "inventory": []},
            {"id": 1, "position": [6, 5], "inventory": []},
            {"id": 2, "position": [6, 5], "inventory": []},
        ],
        "orders": [
            {"id": "order_0", "items_required": ["milk", "bread", "milk"]},
            {"id": "order_1", "items_required": ["bread", "bread", "milk"]},
        ],
        "total_orders": 50,
        "max_rounds": 300,
    }
    _write_json(run_dir / "state0.json", state0)
    _write_json(
        run_dir / "order_trace.json",
        {
            "trace": [
                {
                    "round": 0,
                    "active": {"id": "order_0", "items_required": ["milk", "bread", "milk"]},
                    "preview": {"id": "order_1", "items_required": ["bread", "bread", "milk"]},
                },
                {
                    "round": 5,
                    "active": {"id": "order_0", "items_required": ["bread", "milk", "bread"]},
                    "preview": {"id": "order_1", "items_required": ["bread", "bread", "milk"]},
                },
            ]
        },
    )
    dataset = mine_medium_dataset(tmp_path)
    params = decision_to_params(baseline_decision())
    cache: dict[tuple[object, ...], object] = {}
    seeds = [7002]

    latest_template = default_generator_from_dataset(dataset, seed=7002, known_orders_mode="latest")
    weighted_template = default_generator_from_dataset(dataset, seed=7002, known_orders_mode="weighted")

    _evaluate_params(
        dataset,
        params=params,
        generator_template=latest_template,
        seeds=seeds,
        cache=cache,  # type: ignore[arg-type]
        forecast_mode="live",
    )
    _evaluate_params(
        dataset,
        params=params,
        generator_template=weighted_template,
        seeds=seeds,
        cache=cache,  # type: ignore[arg-type]
        forecast_mode="live",
    )

    # One seed evaluated in two distinct known-orders modes => two cached entries.
    assert len(cache) == 2


def test_default_generator_tracks_observed_order_sizes_for_expert_like_data(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_20260303_000004"
    state0 = {
        "grid": {"width": 10, "height": 8, "walls": []},
        "items": [
            {"id": "item_0", "type": "milk", "position": [2, 2]},
            {"id": "item_1", "type": "bread", "position": [4, 2]},
        ],
        "drop_off": [1, 6],
        "bots": [
            {"id": bot_id, "position": [8, 6], "inventory": []}
            for bot_id in range(10)
        ],
        "orders": [
            {"id": "order_0", "items_required": ["milk", "bread", "eggs", "cheese"]},
            {"id": "order_1", "items_required": ["milk", "bread", "eggs", "cheese", "rice", "pasta"]},
        ],
        "total_orders": 50,
        "max_rounds": 300,
    }
    _write_json(run_dir / "state0.json", state0)
    dataset = mine_dataset(tmp_path)
    generator = default_generator_from_dataset(dataset, seed=7002, known_orders_mode="latest")

    assert dict(generator.order_size_weights) == {4: 1, 6: 1}
