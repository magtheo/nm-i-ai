"""Deterministic local Medium simulator + autotune harness.

This module uses only local artifacts and deterministic generation.
It is intended for fast offline optimization without live sessions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from json import JSONDecodeError
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

from .decision_engine import DecisionConfig, DecisionEngine
from .experimental_dispatch_engine import ExperimentalDispatchConfig, ExperimentalDispatchEngine
from .models import (
    BotAction,
    BotActionCommand,
    BotInfo,
    GameState,
    GridInfo,
    ItemInfo,
    OrderInfo,
    OrderStatus,
    RoundActions,
)


ORDER_BONUS = 5
RECENT_RUN_WINDOW = 20
TOP_SCORE_RUNS = 20
ARTIFACTS_ROOT = ROOT / "artifacts"
BASELINE_PATH = ARTIFACTS_ROOT / "baseline_medium.json"
MEDIUM_DIR = ARTIFACTS_ROOT / "medium"
TRIALS_CSV_PATH = MEDIUM_DIR / "trials.csv"
BEST_PARAMS_PATH = MEDIUM_DIR / "best_params.json"
BEST_REPORT_PATH = MEDIUM_DIR / "best_report.json"
TOP_CONFIGS_PATH = MEDIUM_DIR / "top_configs.json"
DASHBOARD_PATH = ROOT / "DASHBOARD.md"
TASKS_PATH = ROOT / "TASKS.md"
REPORT_PATH = ROOT / "REPORT_MEDIUM_OPTIMIZATION.md"
LIVE_BEST_CONFIG_PATH = ROOT / "app" / "integrations" / "nmiai_grocery_bot" / "best_configs" / "medium.json"
LIVE_BEST_EXPERT_CONFIG_PATH = ROOT / "app" / "integrations" / "nmiai_grocery_bot" / "best_configs" / "expert.json"


@dataclass(frozen=True)
class MediumDataset:
    grid: GridInfo
    items: list[dict[str, Any]]
    drop_off: list[int]
    bot_starts: list[list[int]]
    observed_orders_exact: list[list[str]]
    observed_order_variants: dict[int, list[dict[str, Any]]]
    order_templates: list[list[str]]
    total_orders: int
    max_rounds: int
    item_type_variants_by_position: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


ArtifactDataset = MediumDataset


def medium_dataset_to_payload(dataset: MediumDataset) -> dict[str, Any]:
    return {
        "grid": {
            "width": dataset.grid.width,
            "height": dataset.grid.height,
            "walls": [list(w) for w in dataset.grid.walls],
        },
        "items": [dict(item) for item in dataset.items],
        "drop_off": list(dataset.drop_off),
        "bot_starts": [list(pos) for pos in dataset.bot_starts],
        "observed_orders_exact": [list(order) for order in dataset.observed_orders_exact],
        "observed_order_variants": {
            str(idx): [dict(variant) for variant in variants]
            for idx, variants in sorted(dataset.observed_order_variants.items(), key=lambda kv: kv[0])
        },
        "order_templates": [list(tpl) for tpl in dataset.order_templates],
        "total_orders": int(dataset.total_orders),
        "max_rounds": int(dataset.max_rounds),
        "item_type_variants_by_position": {
            str(pos_key): [dict(variant) for variant in variants]
            for pos_key, variants in sorted(dataset.item_type_variants_by_position.items(), key=lambda kv: kv[0])
        },
    }


def medium_dataset_from_payload(payload: dict[str, Any]) -> MediumDataset:
    grid_payload = payload.get("grid", {})
    grid = GridInfo(
        width=int(grid_payload.get("width", 0)),
        height=int(grid_payload.get("height", 0)),
        walls=[list(w) for w in grid_payload.get("walls", [])],
    )

    variants_raw = payload.get("observed_order_variants", {})
    variants: dict[int, list[dict[str, Any]]] = {}
    if isinstance(variants_raw, dict):
        for raw_idx, raw_rows in variants_raw.items():
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw_rows, list):
                continue
            cleaned_rows: list[dict[str, Any]] = []
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                req = row.get("items_required", [])
                if not isinstance(req, list):
                    continue
                try:
                    count = int(row.get("count", 1))
                except (TypeError, ValueError):
                    count = 1
                cleaned_rows.append(
                    {
                        "items_required": [str(item_type) for item_type in req],
                        "count": max(1, count),
                    }
                )
            if cleaned_rows:
                variants[idx] = cleaned_rows

    item_variants_raw = payload.get("item_type_variants_by_position", {})
    item_variants: dict[str, list[dict[str, Any]]] = {}
    if isinstance(item_variants_raw, dict):
        for raw_pos, raw_rows in item_variants_raw.items():
            pos_key = str(raw_pos)
            if not isinstance(raw_rows, list):
                continue
            cleaned_rows: list[dict[str, Any]] = []
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                item_type = str(row.get("type", "")).strip()
                if not item_type:
                    continue
                try:
                    count = int(row.get("count", 1))
                except (TypeError, ValueError):
                    count = 1
                cleaned_rows.append({"type": item_type, "count": max(1, count)})
            if cleaned_rows:
                item_variants[pos_key] = cleaned_rows

    return MediumDataset(
        grid=grid,
        items=[dict(item) for item in payload.get("items", [])],
        drop_off=[int(v) for v in payload.get("drop_off", [0, 0])],
        bot_starts=[[int(v) for v in pos] for pos in payload.get("bot_starts", [])],
        observed_orders_exact=[
            [str(item_type) for item_type in order]
            for order in payload.get("observed_orders_exact", [])
        ],
        observed_order_variants=variants,
        order_templates=[
            [str(item_type) for item_type in tpl]
            for tpl in payload.get("order_templates", [])
        ],
        total_orders=int(payload.get("total_orders", 50)),
        max_rounds=int(payload.get("max_rounds", 300)),
        item_type_variants_by_position=item_variants,
    )


def save_medium_dataset_snapshot(
    dataset: MediumDataset,
    path: str | Path,
    *,
    source: str = "",
) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nmiai_medium_dataset_snapshot_v1",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "dataset": medium_dataset_to_payload(dataset),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return out_path


def load_medium_dataset_snapshot(path: str | Path) -> MediumDataset:
    snapshot_path = Path(path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("dataset"), dict):
        return medium_dataset_from_payload(payload["dataset"])
    if isinstance(payload, dict):
        return medium_dataset_from_payload(payload)
    raise RuntimeError(f"Invalid dataset snapshot format: {snapshot_path}")


def dataset_to_payload(dataset: ArtifactDataset) -> dict[str, Any]:
    return medium_dataset_to_payload(dataset)


def dataset_from_payload(payload: dict[str, Any]) -> ArtifactDataset:
    return medium_dataset_from_payload(payload)


def save_dataset_snapshot(
    dataset: ArtifactDataset,
    path: str | Path,
    *,
    source: str = "",
) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nmiai_dataset_snapshot_v1",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "dataset": dataset_to_payload(dataset),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return out_path


def load_dataset_snapshot(path: str | Path) -> ArtifactDataset:
    snapshot_path = Path(path)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("dataset"), dict):
        return dataset_from_payload(payload["dataset"])
    if isinstance(payload, dict):
        return dataset_from_payload(payload)
    raise RuntimeError(f"Invalid dataset snapshot format: {snapshot_path}")


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 7002
    known_orders_mode: str = "weighted"  # weighted | latest
    size3_bias: float = 0.80
    template_repeat_prob: float = 0.65
    hot_type_bias: float = 0.70
    item_type_shuffle_prob: float = 0.0
    order_size_weights: tuple[tuple[int, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "known_orders_mode": self.known_orders_mode,
            "size3_bias": self.size3_bias,
            "template_repeat_prob": self.template_repeat_prob,
            "hot_type_bias": self.hot_type_bias,
            "item_type_shuffle_prob": self.item_type_shuffle_prob,
            "order_size_weights": [[size, count] for size, count in self.order_size_weights],
        }


@dataclass(frozen=True)
class PolicyCandidate:
    decision: DecisionConfig
    generator: GeneratorConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "generator": self.generator.to_dict(),
        }


@dataclass(frozen=True)
class MaxScoreInfo:
    total_orders: int
    total_items_needed: int
    max_score: int
    exact: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_orders": self.total_orders,
            "total_items_needed": self.total_items_needed,
            "max_score": self.max_score,
            "exact": self.exact,
            "score_formula": "max_score = delivered_items + 5 * completed_orders",
        }


@dataclass
class SimulationResult:
    score: int
    items_delivered: int
    orders_completed: int
    rounds_used: int
    avg_decision_ms: float
    p95_decision_ms: float
    idle_steps_per_bot: dict[int, int]
    blocked_moves: int
    collisions_avoided: int
    swaps_prevented: int
    replans: int
    fallback_rounds: int
    escape_rounds: int
    escape_episodes: int
    max_escape_streak: int
    whca_rounds: int
    whca_avg_ms: float
    round_telemetry: list[dict[str, Any]]
    policy: PolicyCandidate
    max_info: MaxScoreInfo

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "items_delivered": self.items_delivered,
            "orders_completed": self.orders_completed,
            "rounds_used": self.rounds_used,
            "avg_decision_ms": round(self.avg_decision_ms, 4),
            "p95_decision_ms": round(self.p95_decision_ms, 4),
            "idle_steps_per_bot": {str(k): v for k, v in sorted(self.idle_steps_per_bot.items())},
            "blocked_moves": self.blocked_moves,
            "collisions_avoided": self.collisions_avoided,
            "swaps_prevented": self.swaps_prevented,
            "replans": self.replans,
            "fallback_rounds": self.fallback_rounds,
            "escape_rounds": self.escape_rounds,
            "escape_episodes": self.escape_episodes,
            "max_escape_streak": self.max_escape_streak,
            "whca_rounds": self.whca_rounds,
            "whca_avg_ms": round(self.whca_avg_ms, 4),
            "round_telemetry": self.round_telemetry,
            "policy": self.policy.to_dict(),
            "max_info": self.max_info.to_dict(),
        }


def _discover_runs(artifact_root: Path) -> list[Path]:
    if not artifact_root.exists():
        return []
    runs = [p for p in artifact_root.iterdir() if p.is_dir() and p.name.startswith("run_")]
    runs.sort(key=lambda p: p.name)
    return runs


def mine_medium_dataset(
    artifact_root: str | Path = ".seed_artifacts/nmiai/medium",
    *,
    recent_run_window: int = RECENT_RUN_WINDOW,
    top_score_runs: int = TOP_SCORE_RUNS,
) -> MediumDataset:
    root = Path(artifact_root)
    all_runs = _discover_runs(root)
    recent_window = max(1, int(recent_run_window))
    ranked_take = max(0, int(top_score_runs))
    runs = list(all_runs[-recent_window:])
    if ranked_take > 0:
        ranked: list[tuple[int, str, Path]] = []
        for run in all_runs:
            result_path = run / "result.json"
            score = -1
            if result_path.exists():
                try:
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                    score = int(payload.get("score", -1))
                except (JSONDecodeError, ValueError, TypeError):
                    score = -1
            ranked.append((score, run.name, run))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        selected = {p.name: p for p in runs}
        for _score, _name, run in ranked[:ranked_take]:
            selected.setdefault(run.name, run)
        runs = sorted(selected.values(), key=lambda p: p.name)
    if not runs:
        raise RuntimeError(f"No run directories found in {root}")

    state0_payloads: list[dict[str, Any]] = []
    for run in runs:
        state0 = run / "state0.json"
        if state0.exists():
            state0_payloads.append(json.loads(state0.read_text(encoding="utf-8")))
    if not state0_payloads:
        raise RuntimeError(f"No state0.json files found in {root}")

    base = state0_payloads[-1]
    grid = GridInfo(
        width=base["grid"]["width"],
        height=base["grid"]["height"],
        walls=base["grid"]["walls"],
    )
    items = list(base["items"])
    drop_off = list(base["drop_off"])
    bot_starts = [list(bot["position"]) for bot in base["bots"]]
    total_orders = int(base.get("total_orders", 50))
    max_rounds = int(base.get("max_rounds", 300))

    observed_by_id: dict[int, list[str]] = {}
    observed_counts_by_id: dict[int, dict[tuple[str, ...], int]] = {}
    latest_by_id: dict[int, list[str]] = {}
    item_type_counts_by_pos: dict[tuple[int, int], dict[str, int]] = {}

    def record_order(
        order: dict[str, Any] | None,
        *,
        run_seen: set[tuple[int, tuple[str, ...]]] | None = None,
    ) -> None:
        if not isinstance(order, dict):
            return
        oid = str(order.get("id", ""))
        if not oid.startswith("order_"):
            return
        try:
            idx = int(oid.split("_", 1)[1])
        except (TypeError, ValueError):
            return
        raw_items = order.get("items_required", [])
        if not isinstance(raw_items, list):
            return
        items_key = tuple(str(item_type) for item_type in raw_items)
        if run_seen is not None:
            dedupe_key = (idx, items_key)
            if dedupe_key in run_seen:
                return
            run_seen.add(dedupe_key)
        latest_by_id[idx] = list(items_key)
        observed_counts_by_id.setdefault(idx, {})
        observed_counts_by_id[idx][items_key] = observed_counts_by_id[idx].get(items_key, 0) + 1

    def record_items(payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        for row in payload.get("items", []):
            if not isinstance(row, dict):
                continue
            pos = row.get("position", [])
            if not isinstance(pos, list) or len(pos) != 2:
                continue
            try:
                px = int(pos[0])
                py = int(pos[1])
            except (TypeError, ValueError):
                continue
            item_type = str(row.get("type", "")).strip()
            if not item_type:
                continue
            key = (px, py)
            item_type_counts_by_pos.setdefault(key, {})
            item_type_counts_by_pos[key][item_type] = item_type_counts_by_pos[key].get(item_type, 0) + 1

    for run in runs:
        run_seen: set[tuple[int, tuple[str, ...]]] = set()

        state0 = run / "state0.json"
        if state0.exists():
            try:
                payload = json.loads(state0.read_text(encoding="utf-8"))
            except JSONDecodeError:
                payload = {}
            record_items(payload)
            for order in payload.get("orders", []):
                record_order(order, run_seen=run_seen)

        order_trace = run / "order_trace.json"
        if order_trace.exists():
            try:
                trace_payload = json.loads(order_trace.read_text(encoding="utf-8"))
            except JSONDecodeError:
                trace_payload = {}
            for row in trace_payload.get("trace", []):
                if not isinstance(row, dict):
                    continue
                record_order(row.get("active"), run_seen=run_seen)
                record_order(row.get("preview"), run_seen=run_seen)

        round_logs = run / "round_logs"
        if not round_logs.exists():
            continue
        for log_path in round_logs.glob("*.jsonl"):
            with log_path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except JSONDecodeError:
                        continue
                    state = row.get("state")
                    if not state:
                        continue
                    for order in state.get("orders", []):
                        record_order(order, run_seen=run_seen)

    for idx, counts in observed_counts_by_id.items():
        if not counts:
            continue
        if idx in latest_by_id:
            observed_by_id[idx] = list(latest_by_id[idx])
            continue
        top_items = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        observed_by_id[idx] = list(top_items)

    if not observed_by_id:
        raise RuntimeError("No observed orders found in state0/order_trace/round logs")

    observed_exact = [observed_by_id[idx] for idx in sorted(observed_by_id)]
    observed_variants: dict[int, list[dict[str, Any]]] = {}
    for idx, counts in observed_counts_by_id.items():
        variants = [
            {"items_required": list(items), "count": int(cnt)}
            for items, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        if variants:
            observed_variants[idx] = variants
    templates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for req in observed_exact:
        key = tuple(req)
        if key not in seen:
            seen.add(key)
            templates.append(list(req))

    item_variants_by_pos: dict[str, list[dict[str, Any]]] = {}
    for (px, py), counts in sorted(item_type_counts_by_pos.items(), key=lambda kv: kv[0]):
        variants = [
            {"type": item_type, "count": int(cnt)}
            for item_type, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        if variants:
            item_variants_by_pos[f"{px},{py}"] = variants

    return MediumDataset(
        grid=grid,
        items=items,
        drop_off=drop_off,
        bot_starts=bot_starts,
        observed_orders_exact=observed_exact,
        observed_order_variants=observed_variants,
        order_templates=templates,
        total_orders=total_orders,
        max_rounds=max_rounds,
        item_type_variants_by_position=item_variants_by_pos,
    )


def mine_dataset(
    artifact_root: str | Path,
    *,
    recent_run_window: int = RECENT_RUN_WINDOW,
    top_score_runs: int = TOP_SCORE_RUNS,
) -> ArtifactDataset:
    return mine_medium_dataset(
        artifact_root,
        recent_run_window=recent_run_window,
        top_score_runs=top_score_runs,
    )


def _choose_order_size(
    rng: random.Random,
    size3_bias: float,
    *,
    order_size_weights: tuple[tuple[int, int], ...] = (),
) -> int:
    if order_size_weights:
        total = sum(max(1, int(count)) for _size, count in order_size_weights)
        pick = rng.randint(1, max(1, total))
        acc = 0
        for size, count in order_size_weights:
            acc += max(1, int(count))
            if pick <= acc:
                return max(1, int(size))
    p3 = min(max(size3_bias, 0.0), 0.95)
    p4 = max(0.03, 0.90 - p3)
    p5 = 1.0 - p3 - p4
    roll = rng.random()
    if roll < p3:
        return 3
    if roll < p3 + p4:
        return 4
    return 5


def synthesize_orders(dataset: MediumDataset, cfg: GeneratorConfig) -> list[dict[str, Any]]:
    rng = random.Random(cfg.seed)
    known_mode = str(getattr(cfg, "known_orders_mode", "weighted")).strip().lower()
    item_types = sorted({item["type"] for item in dataset.items})
    hot_counter: dict[str, int] = {}
    for tpl in dataset.order_templates:
        for typ in tpl:
            hot_counter[typ] = hot_counter.get(typ, 0) + 1

    hot_types = sorted(item_types, key=lambda t: (-hot_counter.get(t, 0), t))
    hot_cutoff = max(2, int(round(len(hot_types) * cfg.hot_type_bias)))
    hot_pool = hot_types[:hot_cutoff]
    if not hot_pool:
        hot_pool = item_types

    orders: list[dict[str, Any]] = []
    for idx in range(dataset.total_orders):
        variants = dataset.observed_order_variants.get(idx, [])
        if idx < len(dataset.observed_orders_exact):
            if known_mode == "latest" or not variants:
                required = list(dataset.observed_orders_exact[idx])
            else:
                total = sum(max(1, int(v.get("count", 1))) for v in variants)
                pick = rng.randint(1, max(1, total))
                acc = 0
                chosen: list[str] | None = None
                for variant in variants:
                    cnt = max(1, int(variant.get("count", 1)))
                    acc += cnt
                    if pick <= acc:
                        chosen = list(variant.get("items_required", []))
                        break
                required = chosen or list(dataset.observed_orders_exact[idx])
        elif variants:
            ranked = sorted(
                variants,
                key=lambda v: (
                    -max(1, int(v.get("count", 1))),
                    tuple(v.get("items_required", [])),
                ),
            )
            required = list(ranked[0].get("items_required", []))
            if not required:
                required = list(dataset.observed_orders_exact[-1])
        else:
            if rng.random() < cfg.template_repeat_prob:
                required = list(rng.choice(dataset.order_templates))
            else:
                size = _choose_order_size(
                    rng,
                    cfg.size3_bias,
                    order_size_weights=tuple(getattr(cfg, "order_size_weights", ()) or ()),
                )
                required = []
                for _ in range(size):
                    pool = hot_pool if rng.random() < cfg.hot_type_bias else item_types
                    required.append(rng.choice(pool))
        orders.append(
            {
                "id": f"order_{idx}",
                "items_required": required,
            }
        )
    return orders


def synthesize_items(dataset: MediumDataset, cfg: GeneratorConfig) -> list[dict[str, Any]]:
    items = [dict(item) for item in dataset.items]
    shuffle_prob = min(max(float(getattr(cfg, "item_type_shuffle_prob", 0.0)), 0.0), 1.0)
    if shuffle_prob <= 0.0:
        return items

    rng = random.Random((int(cfg.seed) * 1_000_003) ^ 0x9E3779B9)
    global_counts: dict[str, int] = {}
    for row in items:
        item_type = str(row.get("type", "")).strip()
        if not item_type:
            continue
        global_counts[item_type] = global_counts.get(item_type, 0) + 1

    def choose_type(pos_key: str) -> str | None:
        variants = dataset.item_type_variants_by_position.get(pos_key, [])
        if variants:
            total = 0
            weighted: list[tuple[str, int]] = []
            for row in variants:
                item_type = str(row.get("type", "")).strip()
                if not item_type:
                    continue
                try:
                    count = max(1, int(row.get("count", 1)))
                except (TypeError, ValueError):
                    count = 1
                weighted.append((item_type, count))
                total += count
            if total > 0 and weighted:
                pick = rng.randint(1, total)
                acc = 0
                for item_type, count in weighted:
                    acc += count
                    if pick <= acc:
                        return item_type
        if not global_counts:
            return None
        weighted_global = sorted(global_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(max(1, int(cnt)) for _, cnt in weighted_global)
        pick = rng.randint(1, max(1, total))
        acc = 0
        for item_type, count in weighted_global:
            acc += max(1, int(count))
            if pick <= acc:
                return item_type
        return weighted_global[0][0]

    for row in items:
        if rng.random() > shuffle_prob:
            continue
        pos = row.get("position", [])
        if not isinstance(pos, list) or len(pos) != 2:
            continue
        try:
            px = int(pos[0])
            py = int(pos[1])
        except (TypeError, ValueError):
            continue
        sampled = choose_type(f"{px},{py}")
        if sampled:
            row["type"] = sampled

    return items


def max_score_for_orders(orders: list[dict[str, Any]]) -> MaxScoreInfo:
    total_orders = len(orders)
    total_items = sum(len(order["items_required"]) for order in orders)
    return MaxScoreInfo(
        total_orders=total_orders,
        total_items_needed=total_items,
        max_score=total_items + ORDER_BONUS * total_orders,
        exact=True,
    )


class LocalMediumGame:
    """Local simulation of Medium rules for 3 bots."""

    def __init__(
        self,
        dataset: MediumDataset,
        orders: list[dict[str, Any]],
        *,
        items: list[dict[str, Any]] | None = None,
    ):
        self.grid = dataset.grid
        self.items = [dict(item) for item in (items if items is not None else dataset.items)]
        self.orders = list(orders)
        self.drop_off = tuple(dataset.drop_off)
        self.max_rounds = dataset.max_rounds

        self.bot_ids = list(range(len(dataset.bot_starts)))
        self.bot_pos: dict[int, list[int]] = {
            bot_id: list(dataset.bot_starts[bot_id]) for bot_id in self.bot_ids
        }
        self.bot_inv: dict[int, list[str]] = {bot_id: [] for bot_id in self.bot_ids}

        self.round = 0
        self.score = 0
        self.items_delivered = 0
        self.orders_completed = 0
        self.active_idx = 0
        self.order_delivered: dict[str, list[str]] = {order["id"]: [] for order in self.orders}

        self.wall_cells = {(w[0], w[1]) for w in self.grid.walls}
        self.item_cells = {(item["position"][0], item["position"][1]) for item in self.items}
        self.item_by_id = {item["id"]: item for item in self.items}

    @property
    def game_over(self) -> bool:
        return self.round >= self.max_rounds

    def _active_order(self) -> dict[str, Any] | None:
        if self.active_idx >= len(self.orders):
            return None
        return self.orders[self.active_idx]

    def _preview_order(self) -> dict[str, Any] | None:
        idx = self.active_idx + 1
        if idx >= len(self.orders):
            return None
        return self.orders[idx]

    def _remaining_for(self, order: dict[str, Any]) -> list[str]:
        remaining = list(order["items_required"])
        delivered = self.order_delivered.get(order["id"], [])
        for item_type in delivered:
            if item_type in remaining:
                remaining.remove(item_type)
        return remaining

    def _is_walkable(self, x: int, y: int) -> bool:
        if x < 0 or y < 0 or x >= self.grid.width or y >= self.grid.height:
            return False
        if (x, y) in self.wall_cells:
            return False
        if (x, y) in self.item_cells:
            return False
        return True

    def _occupied_by_other_bot(self, bot_id: int, cell: tuple[int, int]) -> bool:
        for other_id in self.bot_ids:
            if other_id == bot_id:
                continue
            if tuple(self.bot_pos[other_id]) == cell:
                return True
        return False

    def _move(self, bot_id: int, dx: int, dy: int) -> bool:
        cx, cy = self.bot_pos[bot_id]
        nx, ny = cx + dx, cy + dy
        target = (nx, ny)
        if not self._is_walkable(nx, ny):
            return False
        if self._occupied_by_other_bot(bot_id, target):
            return False
        self.bot_pos[bot_id] = [nx, ny]
        return True

    def _pickup(self, bot_id: int, item_id: str | None) -> None:
        if item_id is None:
            return
        inv = self.bot_inv[bot_id]
        if len(inv) >= 3:
            return
        item = self.item_by_id.get(item_id)
        if item is None:
            return
        bx, by = self.bot_pos[bot_id]
        ix, iy = item["position"]
        if abs(bx - ix) + abs(by - iy) != 1:
            return
        inv.append(item["type"])

    def _deliver_matching(self, bot_id: int) -> int:
        active = self._active_order()
        if active is None:
            return 0
        oid = active["id"]
        remaining = self._remaining_for(active)
        if not remaining:
            return 0

        delivered = 0
        inv = self.bot_inv[bot_id]
        kept: list[str] = []
        for item_type in inv:
            if item_type in remaining:
                remaining.remove(item_type)
                self.order_delivered[oid].append(item_type)
                self.score += 1
                self.items_delivered += 1
                delivered += 1
            else:
                kept.append(item_type)
        self.bot_inv[bot_id] = kept
        return delivered

    def _complete_if_needed(self) -> bool:
        active = self._active_order()
        if active is None:
            return False
        if self._remaining_for(active):
            return False
        self.orders_completed += 1
        self.score += ORDER_BONUS
        self.active_idx += 1
        return True

    def _auto_deliver_for_bot(self, bot_id: int) -> None:
        while True:
            active = self._active_order()
            if active is None:
                return
            before = self.score
            self._deliver_matching(bot_id)
            if not self._complete_if_needed():
                return
            if self.score == before:
                return

    def _drop_off(self, bot_id: int) -> None:
        if tuple(self.bot_pos[bot_id]) != self.drop_off:
            return
        if not self.bot_inv[bot_id]:
            return
        if self._active_order() is None:
            return
        self._deliver_matching(bot_id)
        if self._complete_if_needed():
            self._auto_deliver_for_bot(bot_id)

    def _assert_invariants(self) -> None:
        for bid in self.bot_ids:
            if len(self.bot_inv[bid]) > 3:
                raise AssertionError(f"Invariant failed: bot {bid} inventory > 3")
        expected_score = self.items_delivered + ORDER_BONUS * self.orders_completed
        if self.score != expected_score:
            raise AssertionError(
                f"Invariant failed: score mismatch score={self.score} expected={expected_score}"
            )

    def get_state(self) -> GameState:
        orders_payload: list[OrderInfo] = []
        active = self._active_order()
        if active is not None:
            orders_payload.append(
                OrderInfo(
                    id=active["id"],
                    items_required=list(active["items_required"]),
                    items_delivered=list(self.order_delivered[active["id"]]),
                    complete=False,
                    status=OrderStatus.ACTIVE,
                )
            )
        preview = self._preview_order()
        if preview is not None:
            orders_payload.append(
                OrderInfo(
                    id=preview["id"],
                    items_required=list(preview["items_required"]),
                    items_delivered=list(self.order_delivered[preview["id"]]),
                    complete=False,
                    status=OrderStatus.PREVIEW,
                )
            )

        return GameState(
            type="game_state",
            round=self.round,
            max_rounds=self.max_rounds,
            grid=self.grid,
            bots=[
                BotInfo(
                    id=bot_id,
                    position=list(self.bot_pos[bot_id]),
                    inventory=list(self.bot_inv[bot_id]),
                )
                for bot_id in self.bot_ids
            ],
            items=[ItemInfo(**item) for item in self.items],
            orders=orders_payload,
            drop_off=[self.drop_off[0], self.drop_off[1]],
            score=self.score,
            active_order_index=self.active_idx,
            total_orders=len(self.orders),
        )

    def step(self, actions: RoundActions) -> dict[str, Any]:
        action_by_bot: dict[int, BotActionCommand] = {a.bot: a for a in actions.actions}
        idle_by_bot: dict[int, int] = {bot_id: 0 for bot_id in self.bot_ids}
        blocked_moves = 0

        for bot_id in sorted(self.bot_ids):
            cmd = action_by_bot.get(bot_id)
            if cmd is None:
                idle_by_bot[bot_id] += 1
                continue
            action = cmd.action
            if action == BotAction.MOVE_UP:
                if not self._move(bot_id, 0, -1):
                    blocked_moves += 1
                    idle_by_bot[bot_id] += 1
            elif action == BotAction.MOVE_DOWN:
                if not self._move(bot_id, 0, 1):
                    blocked_moves += 1
                    idle_by_bot[bot_id] += 1
            elif action == BotAction.MOVE_LEFT:
                if not self._move(bot_id, -1, 0):
                    blocked_moves += 1
                    idle_by_bot[bot_id] += 1
            elif action == BotAction.MOVE_RIGHT:
                if not self._move(bot_id, 1, 0):
                    blocked_moves += 1
                    idle_by_bot[bot_id] += 1
            elif action == BotAction.PICK_UP:
                self._pickup(bot_id, cmd.item_id)
            elif action == BotAction.DROP_OFF:
                self._drop_off(bot_id)
            else:
                idle_by_bot[bot_id] += 1

        self.round += 1
        self._assert_invariants()
        return {
            "idle_steps_by_bot": idle_by_bot,
            "blocked_moves": blocked_moves,
            "score": self.score,
        }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((p / 100.0) * (len(ordered) - 1)))
    idx = max(0, min(idx, len(ordered) - 1))
    return float(ordered[idx])


def simulate_policy(
    dataset: MediumDataset,
    policy: PolicyCandidate,
    *,
    forecast_mode: str = "oracle",
    engine_mode: str = "decision_engine",
    debug: bool = False,
) -> SimulationResult:
    orders = synthesize_orders(dataset, policy.generator)
    items = synthesize_items(dataset, policy.generator)
    max_info = max_score_for_orders(orders)
    game = LocalMediumGame(dataset, orders, items=items)
    mode = (forecast_mode or "oracle").strip().lower()
    if mode == "live":
        order_forecast: dict[int, list[str]] = {}
    elif mode == "mined":
        order_forecast = {
            idx: list(req)
            for idx, req in enumerate(dataset.observed_orders_exact)
        }
    else:
        order_forecast = {idx: list(order["items_required"]) for idx, order in enumerate(orders)}
    mode_engine = str(engine_mode or "decision_engine").strip().lower()
    if mode_engine == "experimental_dispatch_engine":
        exp_cfg = ExperimentalDispatchConfig.from_overrides(policy.decision.to_dict())
        engine = ExperimentalDispatchEngine(
            config=exp_cfg,
            debug=False,
            reservation_horizon=max(1, int(policy.decision.reservation_horizon)),
            order_forecast=order_forecast,
        )
    else:
        engine = DecisionEngine(
            config=policy.decision,
            debug=False,
            verbose=False,
            order_forecast=order_forecast,
        )

    decision_ms: list[float] = []
    idle_by_bot: dict[int, int] = {bid: 0 for bid in game.bot_ids}
    blocked_moves = 0
    collisions_avoided = 0
    swaps_prevented = 0
    replans = 0
    fallback_rounds = 0
    escape_rounds = 0
    escape_episodes = 0
    max_escape_streak = 0
    current_escape_streak = 0
    whca_rounds = 0
    whca_ms: list[float] = []
    round_telemetry: list[dict[str, Any]] = []
    progress_window: list[float] = []

    while not game.game_over:
        state = game.get_state()
        actions = engine.decide(state)
        decision_ms.append(engine.last_decision_ms)
        collisions_avoided += int(getattr(engine, "last_collisions_avoided", 0))
        swaps_prevented += int(getattr(engine, "last_swaps_prevented", 0))
        replans += int(getattr(engine, "last_replans", 0))
        if bool(getattr(engine, "last_fallback_used", False)):
            fallback_rounds += 1
        if getattr(engine, "last_escape_mode_active", False):
            escape_rounds += 1
            current_escape_streak += 1
            if current_escape_streak == 1:
                escape_episodes += 1
            if current_escape_streak > max_escape_streak:
                max_escape_streak = current_escape_streak
        else:
            current_escape_streak = 0
        if getattr(engine, "last_whca_used", False):
            whca_rounds += 1
            whca_ms.append(float(getattr(engine, "last_whca_ms", 0.0)))

        score_before = state.score
        step_info = game.step(actions)
        round_blocked = int(step_info["blocked_moves"])
        blocked_moves += round_blocked
        per_bot = step_info["idle_steps_by_bot"]
        for bid, val in per_bot.items():
            idle_by_bot[bid] = idle_by_bot.get(bid, 0) + int(val)
        score_after = int(step_info["score"])
        score_delta = float(score_after - score_before)
        progress_window.append(score_delta)
        if len(progress_window) > 8:
            progress_window.pop(0)
        progress_rate = float(sum(progress_window) / len(progress_window)) if progress_window else 0.0
        telem = dict(getattr(engine, "last_round_telemetry", {}) or {})
        telem["round"] = int(state.round)
        telem["score_before"] = int(score_before)
        telem["score_after"] = int(score_after)
        telem["score_delta"] = float(score_delta)
        telem["progress_rate"] = float(progress_rate)
        telem["blocked_moves"] = float(round_blocked)
        telem["swaps_prevented"] = float(getattr(engine, "last_swaps_prevented", 0))
        telem["collisions_avoided"] = float(getattr(engine, "last_collisions_avoided", 0))
        round_telemetry.append(telem)

    avg_ms = statistics.mean(decision_ms) if decision_ms else 0.0
    p95_ms = _percentile(decision_ms, 95.0)
    result = SimulationResult(
        score=game.score,
        items_delivered=game.items_delivered,
        orders_completed=game.orders_completed,
        rounds_used=game.round,
        avg_decision_ms=avg_ms,
        p95_decision_ms=p95_ms,
        idle_steps_per_bot=idle_by_bot,
        blocked_moves=blocked_moves,
        collisions_avoided=collisions_avoided,
        swaps_prevented=swaps_prevented,
        replans=replans,
        fallback_rounds=fallback_rounds,
        escape_rounds=escape_rounds,
        escape_episodes=escape_episodes,
        max_escape_streak=max_escape_streak,
        whca_rounds=whca_rounds,
        whca_avg_ms=(statistics.mean(whca_ms) if whca_ms else 0.0),
        round_telemetry=round_telemetry,
        policy=policy,
        max_info=max_info,
    )
    if debug:
        print(
            f"score={result.score} items={result.items_delivered} orders={result.orders_completed} "
            f"avg_ms={result.avg_decision_ms:.3f} p95_ms={result.p95_decision_ms:.3f}"
        )
    return result


def default_generator_from_dataset(
    dataset: MediumDataset,
    *,
    seed: int = 7002,
    known_orders_mode: str = "weighted",
    item_type_shuffle_prob: float = 0.0,
) -> GeneratorConfig:
    sizes = [len(order) for order in dataset.observed_orders_exact]
    size_counts: dict[int, int] = {}
    for size in sizes:
        size_counts[int(size)] = size_counts.get(int(size), 0) + 1
    size3_bias = 0.70
    if sizes:
        size3_bias = sum(1 for sz in sizes if sz == 3) / len(sizes)

    seen: set[tuple[str, ...]] = set()
    repeats = 0
    for order in dataset.observed_orders_exact:
        key = tuple(order)
        if key in seen:
            repeats += 1
        else:
            seen.add(key)
    template_repeat_prob = repeats / len(sizes) if sizes else 0.5
    template_repeat_prob = min(max(template_repeat_prob, 0.30), 0.90)

    type_counts: dict[str, int] = {}
    for order in dataset.observed_orders_exact:
        for item_type in order:
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
    if type_counts:
        top = sorted(type_counts.values(), reverse=True)
        top_half = top[: max(1, len(top) // 2)]
        hot_type_bias = sum(top_half) / max(1, sum(top))
    else:
        hot_type_bias = 0.70
    hot_type_bias = min(max(hot_type_bias, 0.45), 0.90)

    return GeneratorConfig(
        seed=seed,
        known_orders_mode=known_orders_mode,
        size3_bias=round(size3_bias, 2),
        template_repeat_prob=round(template_repeat_prob, 2),
        hot_type_bias=round(hot_type_bias, 2),
        item_type_shuffle_prob=round(min(max(item_type_shuffle_prob, 0.0), 1.0), 3),
        order_size_weights=tuple(sorted(size_counts.items())),
    )


def baseline_decision() -> DecisionConfig:
    return DecisionConfig(
        lookahead_orders=2,
        active_weight=10.0,
        preview_weight=4.0,
        dropoff_completion_threshold=0.67,
        zone_penalty_weight=0.0,
        dist_weight=1.0,
        dropoff_dist_weight=0.35,
        congestion_weight=1.0,
        collision_risk_weight=1.0,
        replan_penalty_weight=1.0,
        carry_home_bias_weight=0.0,
        urgency_weight=1.0,
        trip_chain_bonus_weight=0.0,
        future_depth_decay=1.0,
        future_count_weight=0.0,
        future_prefetch_bonus=0,
        future_priority_mode="depth",
        prefetch_min_completion=0.0,
        prefetch_spare_slots=0,
        prefetch_nonmatching_cap=3,
        strict_active_priority=False,
        strict_active_release_completion=1.0,
        force_dropoff_for_full_nonmatching=False,
        always_deliver_matching=False,
        avoid_dropoff_block_when_matching=True,
        max_concurrent_deliverers=2,
        adaptive_deliver_queue=False,
        deliver_queue_min=1,
        deliver_queue_max=3,
        assignment_strategy="greedy",
        auction_option_depth=12,
        auction_allow_skip=True,
        reservation_horizon=2,
        hysteresis_penalty=1.0,
        sticky_target_bonus=0.0,
        early_deliver_matching_count=0,
        early_deliver_inventory_threshold=2,
        endgame_disable_prefetch_rounds=0,
        endgame_force_deliver_rounds=0,
        endgame_strict_active=False,
        avoid_immediate_backtrack=True,
        backtrack_slack=1,
        wait_on_backtrack_conflict=False,
        collision_aggressiveness="wait",
        decision_soft_budget_ms=20.0,
        decision_hard_cap_ms=50.0,
        pickup_fail_blacklist_threshold=2,
        pickup_fail_blacklist_rounds=40,
        stall_round_threshold=24,
        stall_recovery_rounds=40,
        stall_recovery_preview_weight=0.0,
        stall_recovery_force_dropoff=True,
        stall_recovery_strict_active=True,
        clear_adjacent_dropoff_lane=False,
        clear_lane_distance=4,
        allow_same_shelf_for_same_type=False,
        stage_nonmatching_when_active_covered=False,
        stage_nonmatching_endgame_rounds=0,
        tie_break_seed=1,
        tie_break_dynamic=False,
        escape_mode_enabled=False,
        whca_enabled=False,
        whca_subset_conflicts_only=True,
        whca_conflict_component_min_size=3,
        whca_soft_budget_ms=6.0,
        congestion_auction_enabled=False,
        congestion_auction_dropoff_trigger=0.67,
        congestion_auction_corridor_trigger=0.67,
        congestion_auction_blocked_trigger=2,
        congestion_auction_option_depth=9,
        congestion_auction_dropoff_penalty=0.75,
        congestion_auction_corridor_penalty=0.75,
        one_way_aisle_enabled=False,
        one_way_aisle_trigger_density=0.67,
        one_way_aisle_blocked_trigger=1,
        two_step_trip_weight=0.0,
        two_step_trip_min_gain=2,
        two_step_order_bonus_weight=1.0,
        two_step_max_extra_steps=2,
        two_step_completion_delay_threshold=1,
        predicted_dropoff_density_weight=0.0,
        predicted_corridor_density_weight=0.0,
        dropoff_stop_line_enabled=False,
        dropoff_stop_line_k=2,
        dropoff_stop_line_radius=2,
        dropoff_stop_line_trigger_density=0.67,
    )


TUNABLE_SPACE: dict[str, list[Any]] = {
    "lookahead_orders": [0, 1, 2, 3, 4, 5, 6],
    "active_weight": [6.0, 8.0, 10.0, 12.0, 14.0, 18.0, 24.0],
    "preview_weight": [0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
    "dropoff_completion_threshold": [0.33, 0.50, 0.67, 0.80, 0.90, 1.00],
    "zone_penalty_weight": [0.0, 0.5, 1.0, 1.5, 2.0],
    "dist_weight": [0.4, 0.7, 0.9, 1.0, 1.2, 1.4, 1.8],
    "dropoff_dist_weight": [0.0, 0.15, 0.25, 0.35, 0.50, 0.75, 1.0],
    "congestion_weight": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0],
    "collision_risk_weight": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0],
    "replan_penalty_weight": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0],
    "carry_home_bias_weight": [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
    "urgency_weight": [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0],
    "trip_chain_bonus_weight": [0.0, 0.5, 1.0, 1.5, 2.0],
    "future_depth_decay": [0.0, 0.25, 0.5, 1.0, 1.5, 2.0],
    "future_count_weight": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0],
    "future_prefetch_bonus": [0, 1, 2, 3],
    "future_priority_mode": ["depth", "flat"],
    "prefetch_min_completion": [0.0, 0.33, 0.5, 0.67, 0.8],
    "prefetch_spare_slots": [0, 1, 2, 3],
    "prefetch_nonmatching_cap": [0, 1, 2, 3],
    "strict_active_priority": [False, True],
    "strict_active_release_completion": [0.0, 0.25, 0.5, 0.67, 0.8, 1.0],
    "force_dropoff_for_full_nonmatching": [False, True],
    "always_deliver_matching": [False, True],
    "avoid_dropoff_block_when_matching": [False, True],
    "max_concurrent_deliverers": [0, 1, 2, 3],
    "adaptive_deliver_queue": [False, True],
    "deliver_queue_min": [1, 2],
    "deliver_queue_max": [2, 3],
    "assignment_strategy": ["greedy", "auction", "hungarian"],
    "auction_option_depth": [8, 12, 16],
    "reservation_horizon": [1, 2, 3, 4],
    "hysteresis_penalty": [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0],
    "sticky_target_bonus": [0.0, 0.5, 1.0, 2.0, 3.0],
    "early_deliver_matching_count": [0, 1, 2, 3],
    "early_deliver_inventory_threshold": [1, 2, 3],
    "endgame_disable_prefetch_rounds": [0, 30, 45, 60, 80, 100],
    "endgame_force_deliver_rounds": [0, 15, 25, 35, 45, 60],
    "endgame_strict_active": [False, True],
    "avoid_immediate_backtrack": [False, True],
    "backtrack_slack": [0, 1, 2, 3],
    "wait_on_backtrack_conflict": [False, True],
    "collision_aggressiveness": ["wait", "detour"],
    "pickup_fail_blacklist_threshold": [1, 2, 3],
    "pickup_fail_blacklist_rounds": [10, 20, 40, 80],
    "stall_round_threshold": [12, 18, 24, 30, 40],
    "anti_no_assignment_enabled": [False, True],
    "secondary_assignment_enabled": [False, True],
    "secondary_reposition_empty_only": [False, True],
    "stall_recovery_rounds": [20, 30, 40, 60, 80],
    "stall_recovery_preview_weight": [0.0, 0.5, 1.0, 2.0],
    "stall_recovery_force_dropoff": [False, True],
    "stall_recovery_strict_active": [False, True],
    "clear_adjacent_dropoff_lane": [False, True],
    "clear_lane_distance": [2, 3, 4, 5, 6],
    "allow_same_shelf_for_same_type": [False, True],
    "stage_nonmatching_when_active_covered": [False, True],
    "stage_nonmatching_endgame_rounds": [0, 10, 20, 30, 45, 60, 80, 100],
    "tie_break_seed": [0, 1, 2, 3, 4, 5, 6, 7],
    "tie_break_dynamic": [False, True],
    "two_step_trip_weight": [0.0, 0.25, 0.5, 0.75, 1.0],
    "two_step_trip_min_gain": [1, 2, 3],
}

TUNABLE_KEYS = list(TUNABLE_SPACE.keys())


def decision_to_params(cfg: DecisionConfig) -> dict[str, Any]:
    full = cfg.to_dict()
    return {k: full[k] for k in TUNABLE_KEYS}


def params_to_decision(params: dict[str, Any]) -> DecisionConfig:
    base = baseline_decision().to_dict()
    base.update(params)
    return DecisionConfig(**base)


def params_hash(params: dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("ascii", errors="ignore")).hexdigest()
    return digest[:12]


def _random_params(rng: random.Random) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, options in TUNABLE_SPACE.items():
        if _is_bool_space(options) or _is_str_space(options):
            out[key] = rng.choice(options)
            continue
        if _is_int_space(options):
            lo = int(min(options))
            hi = int(max(options))
            if rng.random() < 0.7:
                out[key] = rng.randint(lo, hi)
            else:
                out[key] = int(rng.choice(options))
            continue
        if _is_float_space(options):
            lo = float(min(options))
            hi = float(max(options))
            if rng.random() < 0.7:
                out[key] = round(rng.uniform(lo, hi), 2)
            else:
                out[key] = float(rng.choice(options))
            continue
        out[key] = rng.choice(options)
    return out


def _mutate_params(rng: random.Random, parent: dict[str, Any]) -> dict[str, Any]:
    child = dict(parent)
    n_changes = rng.randint(1, 4)
    keys = rng.sample(TUNABLE_KEYS, n_changes)
    for key in keys:
        options = TUNABLE_SPACE[key]
        cur = child.get(key, rng.choice(options))
        if _is_bool_space(options) or _is_str_space(options):
            child[key] = rng.choice(options)
            continue
        if _is_int_space(options):
            lo = int(min(options))
            hi = int(max(options))
            step = rng.choice([1, 1, 2])
            base = int(round(float(cur))) if isinstance(cur, (int, float)) else int(rng.choice(options))
            mutated = base + rng.choice([-step, step])
            if rng.random() < 0.15:
                mutated = rng.randint(lo, hi)
            child[key] = max(lo, min(hi, mutated))
            continue
        if _is_float_space(options):
            lo = float(min(options))
            hi = float(max(options))
            base = float(cur) if isinstance(cur, (int, float)) else float(rng.choice(options))
            sigma = max(0.05, (hi - lo) / 8.0)
            mutated = base + rng.gauss(0.0, sigma)
            if rng.random() < 0.2:
                mutated = rng.uniform(lo, hi)
            mutated = max(lo, min(hi, mutated))
            child[key] = round(mutated, 2)
            continue
        if cur not in options:
            child[key] = rng.choice(options)
            continue
        idx = options.index(cur)
        if len(options) >= 3 and rng.random() < 0.75:
            delta = rng.choice([-1, 1])
            new_idx = max(0, min(len(options) - 1, idx + delta))
            child[key] = options[new_idx]
        else:
            child[key] = rng.choice(options)
    return child


def _is_bool_space(options: list[Any]) -> bool:
    return bool(options) and all(isinstance(v, bool) for v in options)


def _is_str_space(options: list[Any]) -> bool:
    return bool(options) and all(isinstance(v, str) for v in options)


def _is_int_space(options: list[Any]) -> bool:
    if not options:
        return False
    if any(isinstance(v, bool) for v in options):
        return False
    return all(isinstance(v, int) for v in options)


def _is_float_space(options: list[Any]) -> bool:
    if not options:
        return False
    if any(isinstance(v, bool) for v in options):
        return False
    return all(isinstance(v, (int, float)) for v in options) and any(isinstance(v, float) for v in options)


def _extract_seed_params(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict):
        if "best" in payload and isinstance(payload["best"], dict):
            maybe = payload["best"].get("params")
            if isinstance(maybe, dict):
                subset = {k: maybe[k] for k in TUNABLE_KEYS if k in maybe}
                if subset:
                    return subset
        if "config" in payload and isinstance(payload["config"], dict):
            mapped = dict(payload["config"])
            if "lookahead_k" in mapped and "lookahead_orders" not in mapped:
                mapped["lookahead_orders"] = mapped["lookahead_k"]
            if "dropoff_threshold" in mapped and "dropoff_completion_threshold" not in mapped:
                mapped["dropoff_completion_threshold"] = mapped["dropoff_threshold"]
            if "zone_penalty" in mapped and "zone_penalty_weight" not in mapped:
                mapped["zone_penalty_weight"] = mapped["zone_penalty"]
            if "seed" in mapped and "tie_break_seed" not in mapped:
                mapped["tie_break_seed"] = mapped["seed"]
            subset = {k: mapped[k] for k in TUNABLE_KEYS if k in mapped}
            if subset:
                return subset
        if "params" in payload and isinstance(payload["params"], dict):
            subset = {k: payload["params"][k] for k in TUNABLE_KEYS if k in payload["params"]}
            if subset:
                return subset
        subset = {k: payload[k] for k in TUNABLE_KEYS if k in payload}
        if subset:
            return subset
    return None


def _initial_param_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = [decision_to_params(baseline_decision())]
    for path in [
        BEST_PARAMS_PATH,
        ROOT / "_tmp_params_138.json",
        ROOT / "_tmp_live138.json",
        ROOT / "_tmp_live_like_200.json",
        ROOT / "_tmp_sim200_local.json",
        LIVE_BEST_CONFIG_PATH,
        LIVE_BEST_EXPERT_CONFIG_PATH,
    ]:
        params = _extract_seed_params(path)
        if params:
            normalized = decision_to_params(params_to_decision(params))
            candidates.append(normalized)
    uniq: list[dict[str, Any]] = []
    seen: set[str] = set()
    for params in candidates:
        p_hash = params_hash(params)
        if p_hash in seen:
            continue
        seen.add(p_hash)
        uniq.append(params)
    return uniq


def _seed_list(raw: str, *, default_start: int, count: int) -> list[int]:
    text = raw.strip()
    if text:
        out: list[int] = []
        for tok in text.split(","):
            tok = tok.strip()
            if not tok:
                continue
            out.append(int(tok))
        if out:
            return out
    return [default_start + i for i in range(count)]


def _ensure_artifact_dirs() -> None:
    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    MEDIUM_DIR.mkdir(parents=True, exist_ok=True)


def _append_trial_row(row: dict[str, Any]) -> None:
    _ensure_artifact_dirs()
    header = [
        "timestamp",
        "seed",
        "params_hash",
        "score",
        "orders_completed",
        "items_delivered",
        "steps_used",
        "decision_ms_p95",
        "notes",
    ]
    is_new = not TRIALS_CSV_PATH.exists()
    with TRIALS_CSV_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _score_key(summary: dict[str, Any]) -> tuple:
    primary_mean = float(summary["train_mean_score"])
    primary_min = float(summary.get("train_min_score", 0.0))
    primary_orders = float(summary["train_mean_orders"])
    primary_canonical = float(summary["canonical_score"])
    p95_penalty = -float(summary["train_p95_ms"])
    blocked_penalty = -float(summary["train_mean_blocked"])

    secondary_mean_raw = summary.get("secondary_train_mean_score")
    if secondary_mean_raw is None:
        return (
            primary_mean,
            primary_min,
            primary_orders,
            primary_canonical,
            p95_penalty,
            blocked_penalty,
        )

    secondary_mean = float(secondary_mean_raw)
    secondary_min = float(summary.get("secondary_train_min_score", secondary_mean))
    secondary_canonical = float(summary.get("secondary_canonical_score", 0.0))
    robust_mean = min(primary_mean, secondary_mean)
    robust_min = min(primary_min, secondary_min)
    balanced_mean = (primary_mean + secondary_mean) / 2.0
    return (
        robust_mean,
        robust_min,
        balanced_mean,
        primary_canonical,
        secondary_canonical,
        primary_orders,
        p95_penalty,
        blocked_penalty,
    )


def _evaluate_params(
    dataset: MediumDataset,
    *,
    params: dict[str, Any],
    generator_template: GeneratorConfig,
    seeds: list[int],
    cache: dict[tuple[Any, ...], SimulationResult],
    forecast_mode: str = "oracle",
) -> tuple[dict[str, Any], dict[int, SimulationResult]]:
    cfg = params_to_decision(params)
    p_hash = params_hash(params)
    per_seed: dict[int, SimulationResult] = {}

    for seed in seeds:
        key = (
            p_hash,
            int(seed),
            str(generator_template.known_orders_mode),
            float(generator_template.size3_bias),
            float(generator_template.template_repeat_prob),
            float(generator_template.hot_type_bias),
            float(generator_template.item_type_shuffle_prob),
            str(forecast_mode),
        )
        if key in cache:
            per_seed[seed] = cache[key]
            continue
        gen = GeneratorConfig(
            seed=seed,
            known_orders_mode=generator_template.known_orders_mode,
            size3_bias=generator_template.size3_bias,
            template_repeat_prob=generator_template.template_repeat_prob,
            hot_type_bias=generator_template.hot_type_bias,
            item_type_shuffle_prob=generator_template.item_type_shuffle_prob,
        )
        policy = PolicyCandidate(decision=cfg, generator=gen)
        result = simulate_policy(dataset, policy, forecast_mode=forecast_mode)
        cache[key] = result
        per_seed[seed] = result

    scores = [r.score for r in per_seed.values()]
    orders = [r.orders_completed for r in per_seed.values()]
    items = [r.items_delivered for r in per_seed.values()]
    p95s = [r.p95_decision_ms for r in per_seed.values()]
    blocked = [r.blocked_moves for r in per_seed.values()]
    rounds = [r.rounds_used for r in per_seed.values()]

    summary = {
        "params": params,
        "params_hash": p_hash,
        "train_mean_score": statistics.mean(scores) if scores else 0.0,
        "train_mean_orders": statistics.mean(orders) if orders else 0.0,
        "train_mean_items": statistics.mean(items) if items else 0.0,
        "train_mean_rounds": statistics.mean(rounds) if rounds else 0.0,
        "train_p95_ms": statistics.mean(p95s) if p95s else 0.0,
        "train_mean_blocked": statistics.mean(blocked) if blocked else 0.0,
        "train_min_score": min(scores) if scores else 0,
        "train_max_score": max(scores) if scores else 0,
    }
    return summary, per_seed


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _update_dashboard(
    *,
    best: dict[str, Any],
    max_info: MaxScoreInfo,
    reproduction_cmd: str,
    last_trials: list[dict[str, Any]],
    forecast_mode: str = "oracle",
) -> None:
    lines: list[str] = []
    lines.append("# MEDIUM Optimization Dashboard")
    lines.append("")
    lines.append(f"- Updated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Best score (canonical): {best['canonical_score']}")
    lines.append(f"- Max score (exact formula): {max_info.max_score}")
    lines.append(f"- Forecast mode: {forecast_mode}")
    secondary_mode = best.get("secondary_known_orders_mode")
    if secondary_mode:
        lines.append(
            f"- Secondary objective ({secondary_mode}): "
            f"canonical={best.get('secondary_canonical_score')} "
            f"mean={best.get('secondary_train_mean_score', 0.0):.3f}"
        )
    lines.append(f"- Best params hash: {best['params_hash']}")
    lines.append(f"- Reproduction: `{reproduction_cmd}`")
    lines.append("")
    lines.append("## Last 10 trials")
    lines.append("")
    lines.append("| hash | mean_score | canonical | mean_orders | p95_ms |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in last_trials[-10:]:
        lines.append(
            f"| {row['params_hash']} | {row['train_mean_score']:.2f} | {row['canonical_score']} | "
            f"{row['train_mean_orders']:.2f} | {row['train_p95_ms']:.3f} |"
        )
    DASHBOARD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_tasks(*, status_line: str, next_steps: list[str]) -> None:
    lines: list[str] = []
    lines.append("# TASKS")
    lines.append("")
    lines.append(f"- Status: {status_line}")
    lines.append("- Next:")
    for step in next_steps:
        lines.append(f"  - {step}")
    TASKS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_final_report(
    *,
    baseline: SimulationResult,
    best: dict[str, Any],
    max_info: MaxScoreInfo,
    train_seeds: list[int],
    holdout_seeds: list[int],
    forecast_mode: str = "oracle",
) -> None:
    lines: list[str] = []
    lines.append("# REPORT MEDIUM OPTIMIZATION")
    lines.append("")
    lines.append("## Final policy")
    lines.append("The assignment utility is optimized as:")
    lines.append(
        "utility = w6*urgency - (w1*dist(bot,target) + w2*dist(target,drop_off) + "
        "w3*congestion + w4*collision_risk + w5*replan_penalty + zone_penalty)"
    )
    lines.append("with deterministic tie-breaks and per-tick reservation collision resolution.")
    lines.append("")
    lines.append("## Why it works")
    lines.append("- Reduces duplicate chasing via global candidate ranking across all bots.")
    lines.append("- Penalizes rapid target switching (hysteresis/replan penalty).")
    lines.append("- Uses deterministic collision reservation and swap prevention.")
    lines.append("")
    lines.append("## Max score method")
    lines.append("Exact score formula for known full order list:")
    lines.append("max_score = total_items_needed + 5 * total_orders")
    lines.append(f"- total_orders = {max_info.total_orders}")
    lines.append(f"- total_items_needed = {max_info.total_items_needed}")
    lines.append(f"- max_score = {max_info.max_score}")
    lines.append("")
    lines.append("## Results")
    lines.append(f"- Forecast mode: {forecast_mode}")
    lines.append(f"- Baseline canonical score: {baseline.score}")
    lines.append(f"- Best canonical score: {best['canonical_score']}")
    lines.append(f"- Best mean train score: {best['train_mean_score']:.3f}")
    if best.get("secondary_known_orders_mode"):
        lines.append(
            f"- Secondary mode ({best['secondary_known_orders_mode']}) canonical: "
            f"{best.get('secondary_canonical_score')}"
        )
        lines.append(
            f"- Secondary mode ({best['secondary_known_orders_mode']}) mean train: "
            f"{best.get('secondary_train_mean_score', 0.0):.3f}"
        )
    if best.get("holdout_mean_score") is not None:
        lines.append(f"- Holdout mean score: {best['holdout_mean_score']:.3f}")
    lines.append(f"- Train seeds: {train_seeds}")
    lines.append(f"- Holdout seeds: {holdout_seeds}")
    lines.append("")
    lines.append("## Repro")
    lines.append("1. `python _simulator.py --mode baseline --show-max`")
    lines.append("2. `python _simulator.py --mode tune --max-attempts 500 --max-stale 50`")
    lines.append("3. `python _simulator.py --mode single --params-file artifacts/medium/best_params.json`")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_baseline(
    dataset: MediumDataset,
    *,
    canonical_seed: int,
    known_orders_mode: str = "weighted",
    item_type_shuffle_prob: float = 0.0,
    forecast_mode: str = "oracle",
    debug: bool = False,
) -> SimulationResult:
    _ensure_artifact_dirs()
    generator = default_generator_from_dataset(
        dataset,
        seed=canonical_seed,
        known_orders_mode=known_orders_mode,
        item_type_shuffle_prob=item_type_shuffle_prob,
    )
    baseline_policy = PolicyCandidate(decision=baseline_decision(), generator=generator)
    result = simulate_policy(dataset, baseline_policy, forecast_mode=forecast_mode, debug=debug)
    _save_json(
        BASELINE_PATH,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "mode": "baseline",
            "forecast_mode": forecast_mode,
            "canonical_seed": canonical_seed,
            **result.to_dict(),
        },
    )
    return result


def autotune_medium(
    dataset: MediumDataset,
    *,
    canonical_seed: int,
    known_orders_mode: str,
    secondary_known_orders_mode: str,
    item_type_shuffle_prob: float,
    train_seeds: list[int],
    holdout_seeds: list[int],
    max_attempts: int,
    max_stale: int,
    random_seed: int,
    target_score: int,
    forecast_mode: str = "oracle",
) -> dict[str, Any]:
    _ensure_artifact_dirs()
    cache: dict[tuple[Any, ...], SimulationResult] = {}

    generator_template = default_generator_from_dataset(
        dataset,
        seed=canonical_seed,
        known_orders_mode=known_orders_mode,
        item_type_shuffle_prob=item_type_shuffle_prob,
    )
    secondary_mode = (secondary_known_orders_mode or "").strip().lower()
    secondary_generator_template: GeneratorConfig | None = None
    if secondary_mode and secondary_mode != generator_template.known_orders_mode:
        secondary_generator_template = default_generator_from_dataset(
            dataset,
            seed=canonical_seed,
            known_orders_mode=secondary_mode,
            item_type_shuffle_prob=item_type_shuffle_prob,
        )
    canonical_orders = synthesize_orders(dataset, generator_template)
    canonical_max = max_score_for_orders(canonical_orders)

    def attach_secondary_metrics(summary: dict[str, Any], params: dict[str, Any]) -> None:
        if secondary_generator_template is None:
            summary["secondary_known_orders_mode"] = None
            summary["secondary_train_mean_score"] = None
            summary["secondary_train_min_score"] = None
            summary["secondary_canonical_score"] = None
            return
        secondary_summary, secondary_per_seed = _evaluate_params(
            dataset,
            params=params,
            generator_template=secondary_generator_template,
            seeds=train_seeds,
            cache=cache,
            forecast_mode=forecast_mode,
        )
        summary["secondary_known_orders_mode"] = secondary_mode
        summary["secondary_train_mean_score"] = secondary_summary["train_mean_score"]
        summary["secondary_train_min_score"] = secondary_summary.get("train_min_score")
        summary["secondary_canonical_score"] = (
            secondary_per_seed[canonical_seed].score if canonical_seed in secondary_per_seed else 0
        )

    initial_candidates = _initial_param_candidates()
    best_summary: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    top_configs: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for init_idx, params in enumerate(initial_candidates):
        summary, per_seed = _evaluate_params(
            dataset,
            params=params,
            generator_template=generator_template,
            seeds=train_seeds,
            cache=cache,
            forecast_mode=forecast_mode,
        )
        summary["canonical_score"] = per_seed[canonical_seed].score if canonical_seed in per_seed else 0
        summary["canonical_max_score"] = canonical_max.max_score
        summary["holdout_mean_score"] = None
        attach_secondary_metrics(summary, params)
        if holdout_seeds:
            holdout_summary, _ = _evaluate_params(
                dataset,
                params=params,
                generator_template=generator_template,
                seeds=holdout_seeds,
                cache=cache,
                forecast_mode=forecast_mode,
            )
            summary["holdout_mean_score"] = holdout_summary["train_mean_score"]

        history.append(dict(summary))
        top_configs.append(dict(summary))
        p_hash = summary["params_hash"]
        seen_hashes.add(p_hash)
        _append_trial_row(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "seed": ",".join(str(s) for s in train_seeds),
                "params_hash": p_hash,
                "score": round(summary["train_mean_score"], 4),
                "orders_completed": round(summary["train_mean_orders"], 4),
                "items_delivered": round(summary["train_mean_items"], 4),
                "steps_used": round(summary["train_mean_rounds"], 4),
                "decision_ms_p95": round(summary["train_p95_ms"], 4),
                "notes": (
                    f"{forecast_mode} init#{init_idx} canonical={summary['canonical_score']} "
                    f"secondary={summary.get('secondary_canonical_score')}"
                ),
            }
        )
        if best_summary is None or _score_key(summary) > _score_key(best_summary):
            best_summary = dict(summary)

    if best_summary is None:
        raise RuntimeError("No initial candidates were evaluated")

    top_configs.sort(key=_score_key, reverse=True)
    top_configs = top_configs[:10]

    _save_json(
        BEST_PARAMS_PATH,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "params_hash": best_summary["params_hash"],
            "params": best_summary["params"],
            "canonical_score": best_summary["canonical_score"],
            "canonical_max_score": best_summary["canonical_max_score"],
            "train_mean_score": best_summary["train_mean_score"],
            "secondary_known_orders_mode": best_summary.get("secondary_known_orders_mode"),
            "secondary_train_mean_score": best_summary.get("secondary_train_mean_score"),
            "secondary_train_min_score": best_summary.get("secondary_train_min_score"),
            "secondary_canonical_score": best_summary.get("secondary_canonical_score"),
            "forecast_mode": forecast_mode,
            "known_orders_mode": known_orders_mode,
            "train_seeds": train_seeds,
            "holdout_seeds": holdout_seeds,
        },
    )
    _save_json(
        BEST_REPORT_PATH,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "best": best_summary,
            "forecast_mode": forecast_mode,
            "known_orders_mode": known_orders_mode,
            "secondary_known_orders_mode": secondary_mode or None,
            "train_seeds": train_seeds,
            "holdout_seeds": holdout_seeds,
        },
    )
    _save_json(TOP_CONFIGS_PATH, {"top_configs": top_configs[:10]})

    reproduction_cmd = "python _simulator.py --mode single --params-file artifacts/medium/best_params.json"
    _update_dashboard(
        best=best_summary,
        max_info=canonical_max,
        reproduction_cmd=reproduction_cmd,
        last_trials=history,
        forecast_mode=forecast_mode,
    )
    _update_tasks(
        status_line="autotune running",
        next_steps=[
            "Continue random+hillclimb search",
            "Stop on max score or stale >= max_stale",
            "Keep artifacts/medium files updated",
        ],
    )

    rng = random.Random(random_seed)
    stale = 0
    attempts = len(history)
    explore_cutoff = attempts + 25

    while attempts < max_attempts:
        attempts += 1
        if attempts <= explore_cutoff:
            params = _random_params(rng)
        else:
            parent = rng.choice(top_configs[: min(5, len(top_configs))])
            params = _mutate_params(rng, parent["params"])

        p_hash = params_hash(params)
        if p_hash in seen_hashes:
            continue
        seen_hashes.add(p_hash)

        summary, per_seed = _evaluate_params(
            dataset,
            params=params,
            generator_template=generator_template,
            seeds=train_seeds,
            cache=cache,
            forecast_mode=forecast_mode,
        )
        canonical_score = per_seed[canonical_seed].score if canonical_seed in per_seed else 0
        summary["canonical_score"] = canonical_score
        summary["canonical_max_score"] = canonical_max.max_score
        summary["holdout_mean_score"] = None
        attach_secondary_metrics(summary, params)

        improved = _score_key(summary) > _score_key(best_summary)
        if improved:
            stale = 0
            if holdout_seeds:
                holdout_summary, _ = _evaluate_params(
                    dataset,
                    params=params,
                    generator_template=generator_template,
                    seeds=holdout_seeds,
                    cache=cache,
                    forecast_mode=forecast_mode,
                )
                summary["holdout_mean_score"] = holdout_summary["train_mean_score"]
            best_summary = dict(summary)
            _save_json(
                BEST_PARAMS_PATH,
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "params_hash": best_summary["params_hash"],
                    "params": best_summary["params"],
                    "canonical_score": best_summary["canonical_score"],
                    "canonical_max_score": best_summary["canonical_max_score"],
                    "train_mean_score": best_summary["train_mean_score"],
                    "holdout_mean_score": best_summary.get("holdout_mean_score"),
                    "secondary_known_orders_mode": best_summary.get("secondary_known_orders_mode"),
                    "secondary_train_mean_score": best_summary.get("secondary_train_mean_score"),
                    "secondary_train_min_score": best_summary.get("secondary_train_min_score"),
                    "secondary_canonical_score": best_summary.get("secondary_canonical_score"),
                    "forecast_mode": forecast_mode,
                    "known_orders_mode": known_orders_mode,
                    "train_seeds": train_seeds,
                    "holdout_seeds": holdout_seeds,
                },
            )
            _save_json(
                BEST_REPORT_PATH,
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "best": best_summary,
                    "forecast_mode": forecast_mode,
                    "known_orders_mode": known_orders_mode,
                    "secondary_known_orders_mode": secondary_mode or None,
                    "train_seeds": train_seeds,
                    "holdout_seeds": holdout_seeds,
                    "attempts": attempts,
                },
            )
            _update_dashboard(
                best=best_summary,
                max_info=canonical_max,
                reproduction_cmd=reproduction_cmd,
                last_trials=history + [summary],
                forecast_mode=forecast_mode,
            )
            _update_tasks(
                status_line=f"improved to canonical={best_summary['canonical_score']} at attempt={attempts}",
                next_steps=[
                    "Continue tuning until stop condition",
                    "Watch stale counter",
                    "Validate with --mode single and saved params",
                ],
            )
            marker = "NEW_BEST"
        else:
            stale += 1
            marker = "-"

        history.append(dict(summary))
        top_configs.append(dict(summary))
        top_configs.sort(key=_score_key, reverse=True)
        top_configs = top_configs[:10]
        _save_json(TOP_CONFIGS_PATH, {"top_configs": top_configs})

        _append_trial_row(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "seed": ",".join(str(s) for s in train_seeds),
                "params_hash": summary["params_hash"],
                "score": round(summary["train_mean_score"], 4),
                "orders_completed": round(summary["train_mean_orders"], 4),
                "items_delivered": round(summary["train_mean_items"], 4),
                "steps_used": round(summary["train_mean_rounds"], 4),
                "decision_ms_p95": round(summary["train_p95_ms"], 4),
                "notes": (
                    f"{forecast_mode} attempt={attempts} canonical={summary['canonical_score']} "
                    f"secondary={summary.get('secondary_canonical_score')} "
                    f"blocked={summary['train_mean_blocked']:.2f} {marker}"
                ),
            }
        )

        if attempts % 10 == 0 or improved:
            print(
                f"[sim] attempt={attempts} mean_score={summary['train_mean_score']:.3f} "
                f"canonical={summary['canonical_score']} "
                f"secondary={summary.get('secondary_canonical_score')} marker={marker}"
            )

        if target_score > 0 and best_summary["canonical_score"] >= target_score:
            print(f"[sim] target reached: canonical={best_summary['canonical_score']} >= {target_score}")
            break
        if best_summary["canonical_score"] >= canonical_max.max_score:
            print(f"[sim] reached exact max score: {canonical_max.max_score}")
            break
        if stale >= max_stale:
            print(f"[sim] stop: no improvement for {stale} attempts")
            break

    _update_dashboard(
        best=best_summary,
        max_info=canonical_max,
        reproduction_cmd=reproduction_cmd,
        last_trials=history,
        forecast_mode=forecast_mode,
    )
    _update_tasks(
        status_line=(
            f"autotune finished: best_canonical={best_summary['canonical_score']} "
            f"attempts={attempts} stale={stale}"
        ),
        next_steps=[
            "Validate best params with --mode single",
            "Optionally run live validation with saved config",
            "Archive best_params if starting a new experiment batch",
        ],
    )

    return {
        "best": best_summary,
        "history": history,
        "top_configs": top_configs,
        "max_info": canonical_max,
        "attempts": attempts,
        "stale": stale,
        "train_seeds": train_seeds,
        "holdout_seeds": holdout_seeds,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline Medium simulator/autotune")
    parser.add_argument("--artifact-root", type=str, default=".seed_artifacts/nmiai/medium")
    parser.add_argument(
        "--mine-recent-window",
        type=int,
        default=RECENT_RUN_WINDOW,
        help="Number of most recent run_* directories to mine",
    )
    parser.add_argument(
        "--mine-top-score-runs",
        type=int,
        default=TOP_SCORE_RUNS,
        help="Additionally include top-scoring historical runs (0 disables)",
    )
    parser.add_argument(
        "--dataset-snapshot",
        type=str,
        default="",
        help="Load MediumDataset from a frozen snapshot JSON instead of mining artifacts",
    )
    parser.add_argument(
        "--write-dataset-snapshot",
        type=str,
        default="",
        help="Write the mined/loaded dataset snapshot JSON to this path",
    )
    parser.add_argument("--mode", type=str, choices=["single", "baseline", "tune"], default="tune")
    parser.add_argument("--seed", type=int, default=7002, help="Canonical deterministic generator seed")
    parser.add_argument(
        "--known-orders-mode",
        type=str,
        default="weighted",
        choices=["weighted", "latest"],
        help="How to synthesize known-order indexes from mined traces",
    )
    parser.add_argument(
        "--secondary-known-orders-mode",
        type=str,
        default="",
        help="Optional secondary known-order mode (weighted/latest) evaluated for each candidate",
    )
    parser.add_argument("--show-max", action="store_true", help="Print exact max score for canonical seed")
    parser.add_argument("--max-attempts", type=int, default=500)
    parser.add_argument("--max-stale", type=int, default=50)
    parser.add_argument("--target-score", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--train-seeds", type=str, default="")
    parser.add_argument("--holdout-seeds", type=str, default="")
    parser.add_argument("--train-count", type=int, default=10)
    parser.add_argument("--holdout-count", type=int, default=5)
    parser.add_argument("--params-file", type=str, default="")
    parser.add_argument(
        "--forecast-mode",
        type=str,
        default="oracle",
        choices=["oracle", "live", "mined"],
        help="oracle=full-order forecast, live=active+preview only, mined=historical index forecast",
    )
    parser.add_argument(
        "--engine-mode",
        type=str,
        default="decision_engine",
        choices=["decision_engine", "experimental_dispatch_engine"],
        help="Policy engine for simulation runs",
    )
    parser.add_argument(
        "--item-type-shuffle-prob",
        type=float,
        default=0.0,
        help="Probability to reshuffle each shelf item type from live-observed distributions per simulation seed",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def _load_params_file(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    params = payload.get("params")
    if isinstance(params, dict):
        subset = {k: params[k] for k in TUNABLE_KEYS if k in params}
        if subset:
            return subset
    config = payload.get("config")
    if isinstance(config, dict):
        mapped = dict(config)
        if "lookahead_k" in mapped and "lookahead_orders" not in mapped:
            mapped["lookahead_orders"] = mapped["lookahead_k"]
        if "dropoff_threshold" in mapped and "dropoff_completion_threshold" not in mapped:
            mapped["dropoff_completion_threshold"] = mapped["dropoff_threshold"]
        if "zone_penalty" in mapped and "zone_penalty_weight" not in mapped:
            mapped["zone_penalty_weight"] = mapped["zone_penalty"]
        if "seed" in mapped and "tie_break_seed" not in mapped:
            mapped["tie_break_seed"] = mapped["seed"]
        subset = {k: mapped[k] for k in TUNABLE_KEYS if k in mapped}
        if subset:
            return subset
    if isinstance(payload, dict):
        subset = {k: payload[k] for k in TUNABLE_KEYS if k in payload}
        if subset:
            return subset
    raise RuntimeError(f"Could not parse params from {path}")


def main() -> None:
    args = parse_args()
    secondary_mode = args.secondary_known_orders_mode.strip().lower()
    if secondary_mode and secondary_mode not in {"weighted", "latest"}:
        raise RuntimeError(f"Invalid --secondary-known-orders-mode: {args.secondary_known_orders_mode}")
    dataset_source = f"mine:{args.artifact_root}"
    if args.dataset_snapshot:
        dataset = load_medium_dataset_snapshot(args.dataset_snapshot)
        dataset_source = f"snapshot:{args.dataset_snapshot}"
    else:
        dataset = mine_medium_dataset(
            args.artifact_root,
            recent_run_window=args.mine_recent_window,
            top_score_runs=args.mine_top_score_runs,
        )
    if args.write_dataset_snapshot:
        saved_path = save_medium_dataset_snapshot(
            dataset,
            args.write_dataset_snapshot,
            source=dataset_source,
        )
        print(f"[sim] dataset snapshot written: {saved_path}")
    print(
        f"[sim] dataset: items={len(dataset.items)} bots={len(dataset.bot_starts)} "
        f"observed_orders={len(dataset.observed_orders_exact)} templates={len(dataset.order_templates)} "
        f"total_orders={dataset.total_orders} source={dataset_source}"
    )

    generator = default_generator_from_dataset(
        dataset,
        seed=args.seed,
        known_orders_mode=args.known_orders_mode,
        item_type_shuffle_prob=args.item_type_shuffle_prob,
    )
    canonical_orders = synthesize_orders(dataset, generator)
    max_info = max_score_for_orders(canonical_orders)
    if args.show_max:
        print(
            f"[sim] max_info: total_orders={max_info.total_orders} "
            f"total_items_needed={max_info.total_items_needed} max_score={max_info.max_score} exact={max_info.exact}"
        )

    if args.mode == "single":
        if args.params_file:
            params = _load_params_file(args.params_file)
            decision = params_to_decision(params)
        else:
            decision = baseline_decision()
        policy = PolicyCandidate(decision=decision, generator=generator)
        result = simulate_policy(
            dataset,
            policy,
            forecast_mode=args.forecast_mode,
            engine_mode=str(getattr(args, "engine_mode", "decision_engine")),
            debug=True,
        )
        print(
            f"[sim] single result: score={result.score} items={result.items_delivered} "
            f"orders={result.orders_completed} p95_ms={result.p95_decision_ms:.3f}"
        )
        return

    if args.mode == "baseline":
        baseline = run_baseline(
            dataset,
            canonical_seed=args.seed,
            known_orders_mode=args.known_orders_mode,
            item_type_shuffle_prob=args.item_type_shuffle_prob,
            forecast_mode=args.forecast_mode,
            debug=args.debug,
        )
        print(
            f"[sim] baseline: score={baseline.score} items={baseline.items_delivered} "
            f"orders={baseline.orders_completed} avg_ms={baseline.avg_decision_ms:.3f} "
            f"p95_ms={baseline.p95_decision_ms:.3f}"
        )
        return

    baseline = run_baseline(
        dataset,
        canonical_seed=args.seed,
        known_orders_mode=args.known_orders_mode,
        item_type_shuffle_prob=args.item_type_shuffle_prob,
        forecast_mode=args.forecast_mode,
        debug=False,
    )
    print(
        f"[sim] baseline: canonical_score={baseline.score} items={baseline.items_delivered} "
        f"orders={baseline.orders_completed}"
    )

    train_seeds = _seed_list(args.train_seeds, default_start=args.seed, count=args.train_count)
    if args.seed not in train_seeds:
        train_seeds = [args.seed] + [s for s in train_seeds if s != args.seed]
    holdout_seeds = _seed_list(args.holdout_seeds, default_start=args.seed + 100, count=args.holdout_count)

    summary = autotune_medium(
        dataset,
        canonical_seed=args.seed,
        known_orders_mode=args.known_orders_mode,
        secondary_known_orders_mode=secondary_mode,
        item_type_shuffle_prob=args.item_type_shuffle_prob,
        train_seeds=train_seeds,
        holdout_seeds=holdout_seeds,
        max_attempts=args.max_attempts,
        max_stale=args.max_stale,
        random_seed=args.random_seed,
        target_score=args.target_score,
        forecast_mode=args.forecast_mode,
    )

    _write_final_report(
        baseline=baseline,
        best=summary["best"],
        max_info=summary["max_info"],
        train_seeds=summary["train_seeds"],
        holdout_seeds=summary["holdout_seeds"],
        forecast_mode=args.forecast_mode,
    )

    print("[sim] final summary")
    print(f"best_canonical_score={summary['best']['canonical_score']}")
    print(f"best_train_mean_score={summary['best']['train_mean_score']:.3f}")
    print(f"max_score_exact={summary['max_info'].max_score}")
    print(f"best_params={BEST_PARAMS_PATH}")
    print(f"best_report={BEST_REPORT_PATH}")
    print(f"top_configs={TOP_CONFIGS_PATH}")


if __name__ == "__main__":
    main()
