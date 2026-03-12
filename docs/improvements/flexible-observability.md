# Plan: Flexible Observability System

## Problem Statement

The current observability system has a well-designed, generic core (`core.py`, `metrics.py`, `output.py`) but the analysis module (`analysis.py`) contains ~150 lines of hardcoded mappings specific to the `grocery_bot` challenge. This means:

- New challenges require modifying the observer module
- Bottleneck detection with source/log guidance is not reusable
- The system violates separation of concerns

## Goal

Make the observability system **challenge-agnostic** while preserving the rich analysis capabilities (bottleneck detection, source file guidance, AI context generation).

---

## Architecture Changes

### Current State

```
tools/observer/
├── core.py          ✅ Generic
├── metrics.py       ✅ Generic
├── output.py        ✅ Generic
├── presets.py       ✅ Generic (but unused)
├── analysis.py      ❌ Hardcoded PHASE_MAPPINGS, SUBPHASE_MAPPINGS
└── __init__.py

challenges/grocery_bot/
└── (no observer config)
```

### Target State

```
tools/observer/
├── core.py          ✅ Generic (unchanged)
├── metrics.py       ✅ Generic (unchanged)
├── output.py        ✅ Generic (unchanged)
├── presets.py       ✅ Generic (enhanced)
├── analysis.py      ✅ Generic - accepts injected mappings
├── registry.py      🆕 Phase mapping registry
└── __init__.py

challenges/grocery_bot/
└── observer_config.py   🆕 Challenge-specific mappings

challenges/<new_challenge>/
└── observer_config.py   🆕 Each challenge defines its own
```

---

## Implementation Plan

### Phase 1: Create Mapping Registry

**File**: `tools/observer/registry.py`

Create a registry class that holds phase mappings and allows runtime registration:

```python
@dataclass
class PhaseMapping:
    log_file: str
    source_file: str
    entry_point: str
    description: str

@dataclass
class SubPhaseMapping:
    function_name: str
    line_hint: str
    log_marker: str
    description: str

class PhaseRegistry:
    """Registry for phase mappings - challenge-agnostic."""
    
    def __init__(self):
        self._phases: dict[str, PhaseMapping] = {}
        self._sub_phases: dict[str, SubPhaseMapping] = {}
    
    def register_phase(self, name: str, mapping: PhaseMapping) -> None:
        """Register a phase mapping."""
        self._phases[name] = mapping
    
    def register_sub_phase(self, name: str, mapping: SubPhaseMapping) -> None:
        """Register a sub-phase mapping."""
        self._sub_phases[name] = mapping
    
    def get_phase(self, name: str) -> PhaseMapping | None:
        return self._phases.get(name)
    
    def get_sub_phase(self, name: str) -> SubPhaseMapping | None:
        return self._sub_phases.get(name)
    
    def all_phases(self) -> dict[str, PhaseMapping]:
        return self._phases.copy()
    
    def all_sub_phases(self) -> dict[str, SubPhaseMapping]:
        return self._sub_phases.copy()
    
    @classmethod
    def from_dict(cls, config: dict) -> "PhaseRegistry":
        """Create registry from configuration dict."""
        registry = cls()
        for name, mapping in config.get("phases", {}).items():
            registry.register_phase(name, PhaseMapping(**mapping))
        for name, mapping in config.get("sub_phases", {}).items():
            registry.register_sub_phase(name, SubPhaseMapping(**mapping))
        return registry
```

**Estimated effort**: 30 minutes

---

### Phase 2: Refactor Analysis Module

**File**: `tools/observer/analysis.py`

Modify `Analysis` class to accept a `PhaseRegistry` instead of using globals:

```python
class Analysis:
    """Analysis of observer data."""
    
    def __init__(self, observer, registry: PhaseRegistry | None = None):
        self.observer = observer
        self.registry = registry or PhaseRegistry()  # Empty registry if none provided
        self._bottlenecks: list[Bottleneck] | None = None
    
    def _get_phase_mapping(self, phase: str) -> PhaseMapping | None:
        return self.registry.get_phase(phase)
    
    def _get_sub_phase_info(self, sub_phase: str) -> SubPhaseMapping | None:
        return self.registry.get_sub_phase(sub_phase)
    
    # ... rest of methods use self.registry instead of global dicts
```

**Changes required**:
1. Remove `PHASE_MAPPINGS` and `SUBPHASE_MAPPINGS` globals
2. Add `registry` parameter to `__init__`
3. Replace direct dict access with registry methods
4. Update `Bottleneck.__post_init__` to accept registry or remove auto-population

**Estimated effort**: 1 hour

---

### Phase 3: Create Challenge-Specific Configs

**File**: `challenges/grocery_bot/observer_config.py`

Extract the hardcoded mappings into a challenge-specific config:

```python
"""Observer configuration for grocery_bot challenge."""
from tools.observer.registry import PhaseRegistry, PhaseMapping, SubPhaseMapping

def create_registry() -> PhaseRegistry:
    """Create and populate the phase registry for grocery_bot."""
    registry = PhaseRegistry()
    
    # Main phases
    registry.register_phase("parsing", PhaseMapping(
        log_file="logs/main.log",
        source_file="challenges/grocery_bot/shared/state.py",
        entry_point="GameState.from_dict()",
        description="State deserialization from server data"
    ))
    
    registry.register_phase("pathfinding", PhaseMapping(
        log_file="logs/pathfinding.log",
        source_file="challenges/grocery_bot/theo/pathfinding.py",
        entry_point="Pathfinder class",
        description="Navigation, BFS, obstacle handling"
    ))
    
    # ... (move all existing mappings here)
    
    return registry

# Convenience export
REGISTRY = create_registry()
```

**Estimated effort**: 30 minutes

---

### Phase 4: Update Observer Integration

**File**: `tools/observer/core.py`

Add optional registry parameter to `Observer.analyze()`:

```python
def analyze(self, registry: PhaseRegistry | None = None) -> "Analysis":
    """Analyze collected metrics."""
    from .analysis import Analysis
    return Analysis(self, registry=registry)
```

**Estimated effort**: 15 minutes

---

### Phase 5: Update Challenge Code

Update the grocery_bot code to pass its registry when analyzing:

```python
# In grocery_bot main code
from challenges.grocery_bot.observer_config import REGISTRY

# When analyzing
analysis = observer.analyze(registry=REGISTRY)
analysis.print_report()
```

**Estimated effort**: 15 minutes

---

### Phase 6: Update Presets (Optional Enhancement)

**File**: `tools/observer/presets.py`

Make presets actually useful by allowing registry injection:

```python
def game_loop_observer(
    registry: PhaseRegistry | None = None,
    console_interval: int = 10,
    json_output: bool = True,
    output_dir: str = "observer_logs"
) -> Observer:
    """Create an observer configured for game loops."""
    obs = Observer()
    obs._registry = registry  # Store for later use in analyze()
    return obs
```

**Estimated effort**: 30 minutes

---

## Migration Checklist

- [ ] Create `tools/observer/registry.py`
- [ ] Refactor `tools/observer/analysis.py` to use registry
- [ ] Create `challenges/grocery_bot/observer_config.py`
- [ ] Update `tools/observer/core.py` analyze method
- [ ] Update grocery_bot to use new config
- [ ] Add tests for registry functionality
- [ ] Update `tools/observer/__init__.py` exports
- [ ] Document the new API in docstrings

---

## Usage Examples

### For Existing Challenge (grocery_bot)

```python
from tools.observer import Observer
from challenges.grocery_bot.observer_config import REGISTRY

obs = Observer()
# ... use observer ...

analysis = obs.analyze(registry=REGISTRY)
analysis.print_report()
```

### For New Challenge

```python
# challenges/my_challenge/observer_config.py
from tools.observer.registry import PhaseRegistry, PhaseMapping

def create_registry() -> PhaseRegistry:
    registry = PhaseRegistry()
    registry.register_phase("my_phase", PhaseMapping(
        log_file="logs/my_challenge.log",
        source_file="challenges/my_challenge/main.py",
        entry_point="process()",
        description="Main processing phase"
    ))
    return registry

REGISTRY = create_registry()
```

```python
# challenges/my_challenge/main.py
from tools.observer import Observer
from .observer_config import REGISTRY

obs = Observer()
with obs.session("tick"):
    with obs.phase("my_phase"):
        process()

analysis = obs.analyze(registry=REGISTRY)
```

### Without Any Registry (Basic Usage)

```python
# Still works - just no source/log guidance
obs = Observer()
with obs.phase("anything"):
    do_work()

analysis = obs.analyze()  # No registry = basic timing stats only
analysis.print_report()   # Still shows bottlenecks, just no file hints
```

---

## Benefits

1. **New challenges work immediately** - Core observer needs no changes
2. **Rich analysis when configured** - Just add a config file per challenge
3. **Backward compatible** - Existing code continues to work
4. **Clean separation** - Challenge logic stays in challenge directory
5. **Testable** - Registry can be mocked/faked easily

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Breaking existing grocery_bot usage | Keep old globals temporarily with deprecation warning |
| Performance impact | Registry is in-memory dict lookup - negligible |
| Complexity increase | Actually reduces complexity by removing globals |

---

## Timeline

| Phase | Effort | Priority |
|-------|--------|----------|
| Phase 1: Registry | 30 min | High |
| Phase 2: Analysis refactor | 1 hour | High |
| Phase 3: Challenge config | 30 min | High |
| Phase 4: Core update | 15 min | Medium |
| Phase 5: Challenge update | 15 min | Medium |
| Phase 6: Presets | 30 min | Low |

**Total estimated effort**: ~3 hours
