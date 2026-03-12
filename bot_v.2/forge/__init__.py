"""Automated Forge package: simulator, immutable core, strategy zone, and orchestrator."""

from .core import LiveCoreRunner, build_actions_payload, load_strategy_callable
from .orchestrator import EvolutionConfig, run_evolution_loop
from .simulator import BatchEvaluation, GrocerySimulator, SimulationSummary, evaluate_strategy_file

__all__ = [
    "BatchEvaluation",
    "EvolutionConfig",
    "GrocerySimulator",
    "LiveCoreRunner",
    "SimulationSummary",
    "build_actions_payload",
    "evaluate_strategy_file",
    "load_strategy_callable",
    "run_evolution_loop",
]
