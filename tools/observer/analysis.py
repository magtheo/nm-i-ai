"""Analysis engine for detecting bottlenecks and generating insights."""

from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict
from tools.logging_config import _current_log_dir


@dataclass
class PhaseMapping:
    """Maps a phase to its log file and source code location."""

    log_file: str
    source_file: str
    entry_point: str
    description: str


@dataclass
class SubPhaseMapping:
    """Maps a sub-phase to its specific function and search terms."""

    function_name: str
    line_hint: str  # What to search for in the source file
    log_marker: str  # What to search for in the log file
    description: str


# Configuration mapping phases to their log files and source code
PHASE_MAPPINGS: dict[str, PhaseMapping] = {
    "parsing": PhaseMapping(
        log_file="main.log",
        source_file="challenges/grocery_bot/shared/state.py",
        entry_point="GameState.from_dict()",
        description="State deserialization from server data",
    ),
    "pathfinding": PhaseMapping(
        log_file="pathfinding.log",
        source_file="challenges/grocery_bot/theo/pathfinding.py",
        entry_point="Pathfinder class",
        description="Navigation, BFS, obstacle handling",
    ),
    "tasks": PhaseMapping(
        log_file="tasks.log",
        source_file="challenges/grocery_bot/theo/tasks.py",
        entry_point="TaskAssigner.assign_tasks()",
        description="Task assignment and prioritization",
    ),
    "actions": PhaseMapping(
        log_file="actions.log",
        source_file="challenges/grocery_bot/theo/actions.py",
        entry_point="ActionGenerator.generate_actions()",
        description="Action generation (move/pick_up/drop_off)",
    ),
    "collision": PhaseMapping(
        log_file="collision.log",
        source_file="challenges/grocery_bot/theo/collision.py",
        entry_point="CollisionAvoider.resolve_conflicts()",
        description="Collision detection and resolution",
    ),
}

# Sub-phase mappings with specific function names and search hints
SUBPHASE_MAPPINGS: dict[str, SubPhaseMapping] = {
    # Tasks sub-phases
    "tasks:spatial_indices": SubPhaseMapping(
        function_name="_rebuild_spatial_indices",
        line_hint="def _rebuild_spatial_indices",
        log_marker="[PHASE:tasks:spatial_indices]",
        description="Rebuild spatial indices for item lookup",
    ),
    "tasks:congestion": SubPhaseMapping(
        function_name="update_congestion",
        line_hint="def update_congestion",
        log_marker="[PHASE:tasks:congestion]",
        description="Update congestion map for pathfinding",
    ),
    "tasks:needed_items": SubPhaseMapping(
        function_name="_get_needed_items",
        line_hint="def _get_needed_items",
        log_marker="[PHASE:tasks:needed_items]",
        description="Calculate remaining items needed for orders",
    ),
    "tasks:generate_candidates": SubPhaseMapping(
        function_name="_generate_all_candidates",
        line_hint="def _generate_all_candidates",
        log_marker="[PHASE:tasks:generate_candidates]",
        description="Generate candidate tasks for each bot",
    ),
    "tasks:global_assignment": SubPhaseMapping(
        function_name="_global_assignment",
        line_hint="def _global_assignment",
        log_marker="[PHASE:tasks:global_assignment]",
        description="Greedy optimization to assign tasks",
    ),
    # Actions sub-phases
    "actions:drop_off": SubPhaseMapping(
        function_name="_drop_off_action",
        line_hint="def _drop_off_action",
        log_marker="[PHASE:actions:drop_off]",
        description="Create drop_off action",
    ),
    "actions:pick_up": SubPhaseMapping(
        function_name="_pick_up_action",
        line_hint="def _pick_up_action",
        log_marker="[PHASE:actions:pick_up]",
        description="Create pick_up action",
    ),
    "actions:move": SubPhaseMapping(
        function_name="_move_toward_action",
        line_hint="def _move_toward_action",
        log_marker="[PHASE:actions:move]",
        description="Create movement action with pathfinding",
    ),
    "actions:adjacent": SubPhaseMapping(
        function_name="_get_adjacent_position",
        line_hint="def _get_adjacent_position",
        log_marker="[PHASE:actions:adjacent]",
        description="Find best adjacent tile for reaching items",
    ),
    # Pathfinding sub-phases
    "pathfinding:set_map": SubPhaseMapping(
        function_name="set_map",
        line_hint="def set_map",
        log_marker="[PHASE:pathfinding:set_map]",
        description="Initialize map configuration",
    ),
    "pathfinding:set_obstacles": SubPhaseMapping(
        function_name="set_obstacles",
        line_hint="def set_obstacles",
        log_marker="[PHASE:pathfinding:set_obstacles]",
        description="Set shelf positions",
    ),
    "pathfinding:update_congestion": SubPhaseMapping(
        function_name="update_congestion",
        line_hint="def update_congestion",
        log_marker="[PHASE:pathfinding:update_congestion]",
        description="Build congestion map from bot positions",
    ),
    "pathfinding:bfs": SubPhaseMapping(
        function_name="bfs_distance",
        line_hint="def bfs_distance",
        log_marker="[PHASE:pathfinding:bfs]",
        description="BFS shortest path calculation",
    ),
    "pathfinding:batch_bfs": SubPhaseMapping(
        function_name="get_distances_to_positions",
        line_hint="def get_distances_to_positions",
        log_marker="[PHASE:pathfinding:batch_bfs]",
        description="Batched BFS for multiple goals",
    ),
    # Collision sub-phases
    "collision:priorities": SubPhaseMapping(
        function_name="_calculate_priorities",
        line_hint="def _calculate_priorities",
        log_marker="[PHASE:collision:priorities]",
        description="Assign priority scores to bots",
    ),
    "collision:path_projection": SubPhaseMapping(
        function_name="_get_planned_path",
        line_hint="def _get_planned_path",
        log_marker="[PHASE:collision:path_projection]",
        description="Project path over multiple steps",
    ),
    "collision:conflict_check": SubPhaseMapping(
        function_name="_check_path_conflict",
        line_hint="def _check_path_conflict",
        log_marker="[PHASE:collision:conflict_check]",
        description="Detect collisions in planned paths",
    ),
    "collision:alternative": SubPhaseMapping(
        function_name="_find_alternative_action",
        line_hint="def _find_alternative_action",
        log_marker="[PHASE:collision:alternative]",
        description="Find alternative action when conflict detected",
    ),
}


@dataclass
class Bottleneck:
    """Represents a detected bottleneck."""

    phase: str
    avg_time_ms: float
    max_time_ms: float
    percentage: float
    count: int
    log_file: str = ""
    source_file: str = ""
    entry_point: str = ""
    sub_phases: list[dict] = field(default_factory=list)

    def __post_init__(self):
        """Populate log/source info from PHASE_MAPPINGS if not provided."""
        parent = self.phase.split(":")[0] if ":" in self.phase else self.phase

        if parent in PHASE_MAPPINGS:
            mapping = PHASE_MAPPINGS[parent]
            if not self.log_file:
                self.log_file = f"{_current_log_dir}/{mapping.log_file}"
            if not self.source_file:
                self.source_file = mapping.source_file
            if not self.entry_point:
                self.entry_point = mapping.entry_point


class Analysis:
    """Analysis of observer data."""

    def __init__(self, observer):
        self.observer = observer
        self._bottlenecks: list[Bottleneck] | None = None

    def _get_parent_phase(self, phase: str) -> str:
        """Get parent phase name (e.g., 'tasks:generate' -> 'tasks')."""
        return phase.split(":")[0] if ":" in phase else phase

    def _get_sub_phase_info(self, sub_phase: str) -> SubPhaseMapping | None:
        """Get sub-phase mapping info."""
        return SUBPHASE_MAPPINGS.get(sub_phase)

    def _group_phases_by_parent(self, timers: dict) -> dict[str, dict]:
        """Group phases into parent phases and their sub-phases."""
        result = {}

        for name, timer in timers.items():
            parent = self._get_parent_phase(name)

            if parent not in result:
                result[parent] = {"total": 0, "count": 0, "sub_phases": {}}

            if ":" in name:
                sub_name = name.split(":")[1]
                result[parent]["sub_phases"][sub_name] = {
                    "name": name,
                    "total_ms": timer.total,
                    "avg_ms": timer.avg,
                    "count": timer.count,
                }
            else:
                result[parent]["total"] = timer.total
                result[parent]["count"] = timer.count
                result[parent]["avg_ms"] = timer.avg

        return result

    def bottlenecks(self, threshold_percent: float = 30.0) -> list[Bottleneck]:
        """Detect bottlenecks - phases taking more than threshold% of total time."""
        if self._bottlenecks is not None:
            return self._bottlenecks

        timers = self.observer.get_phases()
        if not timers:
            return []

        grouped = self._group_phases_by_parent(timers)
        parent_phases = {k: v for k, v in timers.items() if ":" not in k}
        total_time = (
            sum(t.total for t in parent_phases.values())
            if parent_phases
            else sum(t.total for t in timers.values())
        )

        if total_time == 0:
            return []

        bottlenecks = []

        for name, timer in parent_phases.items():
            percentage = (timer.total / total_time) * 100
            if percentage >= threshold_percent:
                # Get sub-phase breakdown
                sub_phases = []
                if name in grouped and grouped[name]["sub_phases"]:
                    for sub_name, sub_info in grouped[name]["sub_phases"].items():
                        sub_phase_key = f"{name}:{sub_name}"
                        sub_mapping = SUBPHASE_MAPPINGS.get(sub_phase_key)
                        sub_phases.append(
                            {
                                "name": sub_name,
                                "total_ms": sub_info["total_ms"],
                                "avg_ms": sub_info["avg_ms"],
                                "count": sub_info["count"],
                                "function": sub_mapping.function_name
                                if sub_mapping
                                else sub_name,
                                "line_hint": sub_mapping.line_hint
                                if sub_mapping
                                else f"def {sub_name}",
                                "log_marker": sub_mapping.log_marker
                                if sub_mapping
                                else f"[PHASE:{name}:{sub_name}]",
                            }
                        )
                    sub_phases.sort(key=lambda x: x["total_ms"], reverse=True)

                bottlenecks.append(
                    Bottleneck(
                        phase=name,
                        avg_time_ms=timer.avg,
                        max_time_ms=timer.max,
                        percentage=percentage,
                        count=timer.count,
                        sub_phases=sub_phases,
                    )
                )

        bottlenecks.sort(key=lambda b: b.percentage, reverse=True)
        self._bottlenecks = bottlenecks
        return bottlenecks

    def ai_context(self) -> dict[str, Any]:
        """Generate AI-friendly context for debugging."""
        bottlenecks = self.bottlenecks()
        timers = self.observer.get_phases()
        grouped = self._group_phases_by_parent(timers)

        all_phases = {}
        for name, timer in timers.items():
            parent = self._get_parent_phase(name)
            mapping = PHASE_MAPPINGS.get(parent)

            if ":" in name:
                sub_mapping = SUBPHASE_MAPPINGS.get(name)
                all_phases[name] = {
                    "total_ms": timer.total,
                    "avg_ms": timer.avg,
                    "count": timer.count,
                    "log_file": f"{_current_log_dir}/{mapping.log_file}"
                    if mapping
                    else f"{_current_log_dir}/main.log",
                    "source_file": mapping.source_file if mapping else "unknown",
                    "function": sub_mapping.function_name
                    if sub_mapping
                    else name.split(":")[1],
                    "line_hint": sub_mapping.line_hint
                    if sub_mapping
                    else f"def {name.split(':')[1]}",
                    "log_marker": sub_mapping.log_marker
                    if sub_mapping
                    else f"[PHASE:{name}]",
                    "is_sub_phase": True,
                    "parent": parent,
                }
            else:
                all_phases[name] = {
                    "total_ms": timer.total,
                    "avg_ms": timer.avg,
                    "count": timer.count,
                    "log_file": f"{_current_log_dir}/{mapping.log_file}"
                    if mapping
                    else f"{_current_log_dir}/main.log",
                    "source_file": mapping.source_file if mapping else "unknown",
                    "entry_point": mapping.entry_point if mapping else "unknown",
                    "is_sub_phase": False,
                }

        priority = None
        if bottlenecks:
            b = bottlenecks[0]
            slowest_sub = b.sub_phases[0] if b.sub_phases else None

            priority = {
                "phase": b.phase,
                "percentage": b.percentage,
                "avg_ms": b.avg_time_ms,
                "log_file": b.log_file,
                "source_file": b.source_file,
                "entry_point": b.entry_point,
                "slowest_sub_phase": slowest_sub,
                "sub_phase_breakdown": b.sub_phases,
            }

        return {
            "priority_bottleneck": priority,
            "all_bottlenecks": [
                {
                    "phase": b.phase,
                    "percentage": b.percentage,
                    "log_file": b.log_file,
                    "source_file": b.source_file,
                    "slowest_sub_phase": b.sub_phases[0] if b.sub_phases else None,
                }
                for b in bottlenecks
            ],
            "phase_mapping": all_phases,
        }

    def print_ai_context(self) -> None:
        """Print AI-friendly context section."""
        context = self.ai_context()
        priority = context["priority_bottleneck"]

        print("\n" + "=" * 60)
        print("AI CONTEXT (debugging guidance)")
        print("=" * 60)

        if priority:
            phase = priority["phase"]
            print(
                f"\n>>> PRIORITY BOTTLENECK: '{phase}' ({priority['percentage']:.0f}% of time)"
            )

            if priority.get("slowest_sub_phase"):
                sub = priority["slowest_sub_phase"]
                print(
                    f"\n>>> SLOWEST SUB-PHASE: '{sub['name']}' ({sub['total_ms']:.1f}ms total)"
                )

                print("\nTO INVESTIGATE:")
                print(f"  Source: {priority['source_file']}")
                print(f'    grep: "{sub["line_hint"]}"')
                print(f"\n  Logs: {priority['log_file']}")
                print(f'    grep: "{sub["log_marker"]}"')

            # Show all sub-phases breakdown
            if (
                priority.get("sub_phase_breakdown")
                and len(priority["sub_phase_breakdown"]) > 0
            ):
                print(f"\nSUB-PHASE TIMING for '{phase}':")
                for sub in priority["sub_phase_breakdown"]:
                    marker = (
                        " <-- SLOWEST" if sub == priority["slowest_sub_phase"] else ""
                    )
                    print(f"  {sub['name']:20} {sub['total_ms']:6.1f}ms{marker}")
        else:
            print("\nNo significant bottlenecks detected.")

        print("\n" + "=" * 60)

    def summary(self) -> dict[str, Any]:
        """Generate a summary of all metrics."""
        timers = self.observer.get_phases()
        sessions = self.observer.get_sessions()

        total_time = sum(t.total for t in timers.values())

        return {
            "total_sessions": len(sessions),
            "total_time_ms": total_time,
            "phases": {
                name: {
                    "total_ms": timer.total,
                    "avg_ms": timer.avg,
                    "min_ms": timer.min,
                    "max_ms": timer.max,
                    "count": timer.count,
                }
                for name, timer in timers.items()
            },
            "bottlenecks": [
                {
                    "phase": b.phase,
                    "avg_ms": b.avg_time_ms,
                    "percentage": b.percentage,
                    "log_file": b.log_file,
                    "source_file": b.source_file,
                    "sub_phases": b.sub_phases,
                }
                for b in self.bottlenecks()
            ],
            "ai_context": self.ai_context(),
        }

    def print_report(self, include_ai_context: bool = True) -> None:
        """Print a formatted analysis report."""
        summary = self.summary()
        bottlenecks = self.bottlenecks()

        print("\n" + "=" * 50)
        print("OBSERVER ANALYSIS REPORT")
        print("=" * 50)

        print(f"\nTotal sessions: {summary['total_sessions']}")
        print(f"Total time: {summary['total_time_ms']:.1f}ms")

        if summary["phases"]:
            print("\nPhase breakdown:")
            parent_phases = {k: v for k, v in summary["phases"].items() if ":" not in k}
            for name, stats in sorted(
                parent_phases.items(), key=lambda x: x[1]["total_ms"], reverse=True
            ):
                print(
                    f"  {name}: {stats['total_ms']:.1f}ms total, {stats['avg_ms']:.1f}ms avg ({stats['count']} calls)"
                )

        if bottlenecks:
            print("\nBottlenecks detected:")
            for b in bottlenecks:
                print(
                    f"  * {b.phase}: {b.avg_time_ms:.1f}ms avg ({b.percentage:.0f}% of time)"
                )
                if b.sub_phases:
                    for sub in b.sub_phases[:3]:
                        print(f"       - {sub['name']}: {sub['total_ms']:.1f}ms")
        else:
            print("\nNo significant bottlenecks detected")

        print("\n" + "=" * 50)

        if include_ai_context:
            self.print_ai_context()

    def to_dict(self) -> dict[str, Any]:
        """Export analysis as dict."""
        return self.summary()
