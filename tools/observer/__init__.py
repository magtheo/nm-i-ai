"""
Observer - A simple but powerful observation tool for Python.

Usage:
    from observer import Observer
    
    obs = Observer()
    
    # Time phases
    with obs.phase("database"):
        result = db.query()
    
    # Track counters
    obs.counter("requests").increment()
    
    # Track gauges
    obs.gauge("connections").set(5)
    
    # Get analysis
    analysis = obs.analyze()
    print(analysis.summary())
"""

from .core import Observer, Session
from .metrics import Counter, Gauge, Timer
from .output import ConsoleOutput, JSONOutput
from .analysis import Analysis, Bottleneck

__version__ = "1.0.0"
__all__ = [
    "Observer", "Session",
    "Counter", "Gauge", "Timer",
    "ConsoleOutput", "JSONOutput",
    "Analysis", "Bottleneck"
]
