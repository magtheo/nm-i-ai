"""Core Observer class."""
from dataclasses import dataclass, field
from typing import Any, Callable
from contextlib import contextmanager
from collections import defaultdict
import time

from .metrics import Counter, Gauge, Timer


@dataclass
class PhaseRecord:
    """Record of a single phase execution."""
    name: str
    duration_ms: float
    timestamp: float


@dataclass  
class Session:
    """A collection of metrics for a single observation session."""
    id: str
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    phases: list[PhaseRecord] = field(default_factory=list)
    counters: dict[str, Counter] = field(default_factory=dict)
    gauges: dict[str, Gauge] = field(default_factory=dict)
    timers: dict[str, Timer] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms(),
            "phases": [{"name": p.name, "duration_ms": p.duration_ms, "timestamp": p.timestamp} for p in self.phases],
            "counters": {k: v.snapshot() for k, v in self.counters.items()},
            "gauges": {k: v.snapshot() for k, v in self.gauges.items()},
            "timers": {k: v.snapshot() for k, v in self.timers.items()},
            "metadata": self.metadata
        }


class Observer:
    """
    Main observer class for tracking performance metrics.
    
    Usage:
        obs = Observer()
        
        with obs.session("game_1"):
            with obs.phase("update"):
                update()
            obs.counter("items").increment()
        
        analysis = obs.analyze()
    """
    
    def __init__(self, enabled: bool = True, output_handler: Callable | None = None):
        self.enabled = enabled
        self.output_handler = output_handler
        self._sessions: list[Session] = []
        self._current_session: Session | None = None
        self._counters: dict[str, Counter] = defaultdict(lambda: Counter(""))
        self._gauges: dict[str, Gauge] = defaultdict(lambda: Gauge(""))
        self._timers: dict[str, Timer] = defaultdict(lambda: Timer(""))
        self._phase_stack: list[str] = []
        self._phase_timers: dict[str, float] = {}
    
    def session(self, name: str, **metadata) -> "SessionContext":
        """Start a new observation session."""
        return SessionContext(self, name, metadata)
    
    def _start_session(self, name: str, metadata: dict) -> None:
        if not self.enabled:
            return
        self._current_session = Session(id=name, metadata=metadata)
    
    def _end_session(self) -> Session | None:
        if not self.enabled or not self._current_session:
            return None
        self._current_session.end_time = time.time()
        self._sessions.append(self._current_session)
        session = self._current_session
        self._current_session = None
        if self.output_handler:
            self.output_handler(session)
        return session
    
    @contextmanager
    def phase(self, name: str):
        """Context manager for timing a phase."""
        if not self.enabled:
            yield
            return
        
        start = time.perf_counter()
        self._phase_stack.append(name)
        try:
            yield
        finally:
            duration = (time.perf_counter() - start) * 1000
            self._phase_stack.pop()
            
            record = PhaseRecord(name=name, duration_ms=duration, timestamp=time.time())
            
            if self._current_session:
                self._current_session.phases.append(record)
            
            if name not in self._timers:
                self._timers[name] = Timer(name)
            self._timers[name]._durations.append(duration)
    
    def counter(self, name: str) -> Counter:
        """Get or create a counter."""
        if name not in self._counters:
            self._counters[name] = Counter(name)
        return self._counters[name]
    
    def gauge(self, name: str) -> Gauge:
        """Get or create a gauge."""
        if name not in self._gauges:
            self._gauges[name] = Gauge(name)
        return self._gauges[name]
    
    def timer(self, name: str) -> Timer:
        """Get or create a timer."""
        if name not in self._timers:
            self._timers[name] = Timer(name)
        return self._timers[name]
    
    def record(self, key: str, value: Any) -> None:
        """Record a custom value in current session metadata."""
        if self._current_session:
            self._current_session.metadata[key] = value
    
    def get_sessions(self) -> list[Session]:
        """Get all recorded sessions."""
        return self._sessions.copy()
    
    def get_phases(self) -> dict[str, Timer]:
        """Get all phase timers."""
        return dict(self._timers)
    
    def analyze(self) -> "Analysis":
        """Analyze collected metrics."""
        from .analysis import Analysis
        return Analysis(self)
    
    def reset(self) -> None:
        """Reset all metrics."""
        self._sessions.clear()
        self._counters.clear()
        self._gauges.clear()
        self._timers.clear()
        self._phase_stack.clear()
    
    def to_dict(self) -> dict:
        """Export all data as dict."""
        return {
            "sessions": [s.to_dict() for s in self._sessions],
            "counters": {k: v.snapshot() for k, v in self._counters.items()},
            "gauges": {k: v.snapshot() for k, v in self._gauges.items()},
            "timers": {k: v.snapshot() for k, v in self._timers.items()}
        }


class SessionContext:
    """Context manager for a session."""
    
    def __init__(self, observer: Observer, name: str, metadata: dict):
        self.observer = observer
        self.name = name
        self.metadata = metadata
    
    def __enter__(self):
        self.observer._start_session(self.name, self.metadata)
        return self
    
    def __exit__(self, *args):
        self.observer._end_session()
        return False
