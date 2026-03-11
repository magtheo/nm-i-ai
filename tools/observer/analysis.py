"""Analysis engine for detecting bottlenecks and generating insights."""
from dataclasses import dataclass
from typing import Any


@dataclass
class Bottleneck:
    """Represents a detected bottleneck."""
    phase: str
    avg_time_ms: float
    max_time_ms: float
    percentage: float
    count: int
    suggestion: str


class Analysis:
    """Analysis of observer data."""
    
    def __init__(self, observer):
        self.observer = observer
        self._bottlenecks: list[Bottleneck] | None = None
    
    def bottlenecks(self, threshold_percent: float = 30.0) -> list[Bottleneck]:
        """Detect bottlenecks - phases taking more than threshold% of total time."""
        if self._bottlenecks is not None:
            return self._bottlenecks
        
        timers = self.observer.get_phases()
        if not timers:
            return []
        
        total_time = sum(t.total for t in timers.values())
        if total_time == 0:
            return []
        
        bottlenecks = []
        suggestions = {
            "database": "Consider adding indexes, connection pooling, or query caching",
            "network": "Consider batching requests, adding retries, or using async I/O",
            "render": "Consider caching rendered output or optimizing templates",
            "parse": "Consider using faster parsers or caching parsed data",
            "compute": "Consider algorithm optimization or parallelization",
            "io": "Consider async I/O or batching operations",
            "collision": "Consider reducing collision detection complexity or spatial partitioning",
            "pathfinding": "Consider path caching or heuristic optimization",
            "tasks": "Consider task batching or priority queue optimization",
        }
        
        for name, timer in timers.items():
            percentage = (timer.total / total_time) * 100
            if percentage >= threshold_percent:
                suggestion = f"This phase is taking {percentage:.0f}% of total time"
                for key, sug in suggestions.items():
                    if key in name.lower():
                        suggestion = sug
                        break
                
                bottlenecks.append(Bottleneck(
                    phase=name,
                    avg_time_ms=timer.avg,
                    max_time_ms=timer.max,
                    percentage=percentage,
                    count=timer.count,
                    suggestion=suggestion
                ))
        
        bottlenecks.sort(key=lambda b: b.percentage, reverse=True)
        self._bottlenecks = bottlenecks
        return bottlenecks
    
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
                    "count": timer.count
                }
                for name, timer in timers.items()
            },
            "bottlenecks": [
                {
                    "phase": b.phase,
                    "avg_ms": b.avg_time_ms,
                    "percentage": b.percentage,
                    "suggestion": b.suggestion
                }
                for b in self.bottlenecks()
            ]
        }
    
    def print_report(self) -> None:
        """Print a formatted analysis report."""
        summary = self.summary()
        bottlenecks = self.bottlenecks()
        
        print("\n" + "=" * 50)
        print("OBSERVER ANALYSIS REPORT")
        print("=" * 50)
        
        print(f"\nTotal sessions: {summary['total_sessions']}")
        print(f"Total time: {summary['total_time_ms']:.1f}ms")
        
        if summary['phases']:
            print("\nPhase breakdown:")
            for name, stats in sorted(summary['phases'].items(), key=lambda x: x[1]['total_ms'], reverse=True):
                print(f"  {name}: {stats['total_ms']:.1f}ms total, {stats['avg_ms']:.1f}ms avg ({stats['count']} calls)")
        
        if bottlenecks:
            print("\nBottlenecks detected:")
            for b in bottlenecks:
                print(f"  * {b.phase}: {b.avg_time_ms:.1f}ms avg ({b.percentage:.0f}% of time)")
                print(f"    -> {b.suggestion}")
        else:
            print("\nNo significant bottlenecks detected")
        
        print("\n" + "=" * 50)
    
    def to_dict(self) -> dict[str, Any]:
        """Export analysis as dict."""
        return self.summary()
