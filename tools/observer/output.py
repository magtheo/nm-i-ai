"""Output handlers for observer data."""
import json
from pathlib import Path
from datetime import datetime
from typing import Any


class ConsoleOutput:
    """Print observer data to console."""
    
    def __init__(self, interval: int = 10, verbose: bool = False):
        self.interval = interval
        self.verbose = verbose
        self._count = 0
    
    def __call__(self, session) -> None:
        self._count += 1
        if self._count % self.interval != 0:
            return
        
        duration = session.duration_ms()
        phases_str = " | ".join(
            f"{p.name}: {p.duration_ms:.1f}ms" 
            for p in session.phases[-5:]
        )
        
        print(f"[{session.id}] {duration:.1f}ms total | {phases_str}")
        
        if self.verbose:
            for name, counter in session.counters.items():
                print(f"  {name}: {counter.value}")
            for name, gauge in session.gauges.items():
                print(f"  {name}: {gauge.value}")


class JSONOutput:
    """Save observer data to JSON file."""
    
    def __init__(self, output_dir: str = "observer_logs", filename: str | None = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.filename = filename
        self._sessions: list = []
    
    def __call__(self, session) -> None:
        self._sessions.append(session)
    
    def save(self, analysis: dict | None = None) -> Path:
        """Save all sessions to JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.filename or f"observer_{timestamp}.json"
        filepath = self.output_dir / filename
        
        data = {
            "sessions": [s.to_dict() for s in self._sessions],
            "analysis": analysis
        }
        
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        return filepath
    
    def get_sessions(self) -> list:
        return self._sessions
