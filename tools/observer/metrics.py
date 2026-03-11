"""Metric types for the observer module."""
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class MetricValue:
    """A single metric measurement."""
    value: Any
    timestamp: float = field(default_factory=time.time)


class Counter:
    """A counter that only goes up (requests, items processed, etc.)"""
    
    def __init__(self, name: str):
        self.name = name
        self._value = 0
        self._history: list[int] = []
    
    def increment(self, amount: int = 1) -> None:
        self._value += amount
    
    def decrement(self, amount: int = 1) -> None:
        self._value -= amount
    
    @property
    def value(self) -> int:
        return self._value
    
    def snapshot(self) -> dict:
        return {"type": "counter", "name": self.name, "value": self._value}
    
    def reset(self) -> None:
        self._history.append(self._value)
        self._value = 0


class Gauge:
    """A gauge that can go up or down (current connections, queue size, etc.)"""
    
    def __init__(self, name: str):
        self.name = name
        self._value = 0
        self._history: list[tuple[float, int]] = []
    
    def set(self, value: int | float) -> None:
        self._value = value
        self._history.append((time.time(), value))
    
    def increment(self, amount: int = 1) -> None:
        self._value += amount
    
    def decrement(self, amount: int = 1) -> None:
        self._value -= amount
    
    @property
    def value(self) -> int | float:
        return self._value
    
    def snapshot(self) -> dict:
        return {"type": "gauge", "name": self.name, "value": self._value}
    
    @property
    def avg(self) -> float:
        if not self._history:
            return 0.0
        return sum(v for _, v in self._history) / len(self._history)


class Timer:
    """A timer for measuring durations."""
    
    def __init__(self, name: str):
        self.name = name
        self._durations: list[float] = []
        self._start_time: float | None = None
    
    def start(self) -> None:
        self._start_time = time.perf_counter()
    
    def stop(self) -> float:
        if self._start_time is None:
            return 0.0
        duration = (time.perf_counter() - self._start_time) * 1000
        self._durations.append(duration)
        self._start_time = None
        return duration
    
    @property
    def total(self) -> float:
        return sum(self._durations)
    
    @property
    def avg(self) -> float:
        if not self._durations:
            return 0.0
        return self.total / len(self._durations)
    
    @property
    def count(self) -> int:
        return len(self._durations)
    
    @property
    def min(self) -> float:
        return min(self._durations) if self._durations else 0.0
    
    @property
    def max(self) -> float:
        return max(self._durations) if self._durations else 0.0
    
    def snapshot(self) -> dict:
        return {
            "type": "timer",
            "name": self.name,
            "total_ms": self.total,
            "avg_ms": self.avg,
            "count": self.count,
            "min_ms": self.min,
            "max_ms": self.max
        }
