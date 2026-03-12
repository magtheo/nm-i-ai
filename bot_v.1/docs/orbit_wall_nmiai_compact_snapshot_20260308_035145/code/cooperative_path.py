"""Compatibility fallback for optional cooperative path planning helpers.

The current workspace does not include the original cooperative path planner
implementation. DecisionEngine already has a guarded WHCA call site, so expose
placeholder functions that fail only if that optional path is actually used.
"""
from __future__ import annotations


def _missing_helper(name: str) -> RuntimeError:
    return RuntimeError(f"{name} is unavailable in this workspace")


def largest_conflict_component(*args, **kwargs):
    raise _missing_helper("largest_conflict_component")


def plan_windowed_next_steps(*args, **kwargs):
    raise _missing_helper("plan_windowed_next_steps")
