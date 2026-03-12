from __future__ import annotations

import builtins
import subprocess
import sys
from pathlib import Path

from bot.cooperative_path import largest_conflict_component, plan_windowed_next_steps
from bot.grid import Grid
from bot.models import GridInfo
from bot.pathfinding import find_pickup_position


ROOT = Path(__file__).resolve().parents[1]


def _grid() -> Grid:
    return Grid(GridInfo(width=6, height=4, walls=[]))


def _is_one_step(current: tuple[int, int], target: tuple[int, int]) -> bool:
    return abs(int(current[0]) - int(target[0])) + abs(int(current[1]) - int(target[1])) <= 1


def test_largest_conflict_component_detects_head_on_pair() -> None:
    grid = _grid()
    plans = [
        (0, (1, 1), (2, 1)),
        (1, (2, 1), (1, 1)),
        (2, (4, 1), (4, 2)),
    ]
    goals = {0: (4, 1), 1: (0, 1), 2: (4, 3)}

    component = largest_conflict_component(
        grid=grid,
        plans=plans,
        goals_by_bot=goals,
        blocked=set(),
        window=4,
    )

    assert 0 in component
    assert 1 in component
    assert len(component) >= 2


def test_plan_windowed_next_steps_avoids_direct_swap() -> None:
    grid = _grid()
    plans = [
        (0, (1, 1), (2, 1)),
        (1, (2, 1), (1, 1)),
    ]
    goals = {0: (4, 1), 1: (0, 1)}

    next_steps = plan_windowed_next_steps(
        grid=grid,
        plans=plans,
        goals_by_bot=goals,
        occupied=set(),
        blocked=set(),
        window=4,
        deliverer_ids=set(),
    )

    assert set(next_steps) == {0, 1}
    assert not (next_steps[0] == (2, 1) and next_steps[1] == (1, 1))
    assert next_steps[0] != next_steps[1]
    assert _is_one_step((1, 1), next_steps[0])
    assert _is_one_step((2, 1), next_steps[1])
    assert grid.is_walkable(next_steps[0][0], next_steps[0][1])
    assert grid.is_walkable(next_steps[1][0], next_steps[1][1])


def test_find_pickup_position_does_not_use_external_modules_import(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if str(name).startswith("modules.bot.models"):
            raise AssertionError("unexpected external import")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    grid = Grid(GridInfo(width=3, height=3, walls=[[1, 1]]))

    pickup = find_pickup_position(grid, (1, 1))
    assert pickup in {(1, 0), (1, 2), (0, 1), (2, 1)}


def _run_module_help(module_name: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", module_name, "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_review_run_supports_module_execution() -> None:
    _run_module_help("scripts.review_run")


def test_compare_runs_supports_module_execution() -> None:
    _run_module_help("scripts.compare_runs")
