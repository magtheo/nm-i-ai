# Repository Structure

This document outlines the team repository structure for NM i AI 2026 competition.

## Team Members

- Theo
- Mykyta
- [Member 3 - placeholder]

---

## Directory Structure

```
nm-i-ai/
├── tools/                      # Shared package (importable)
│   ├── __init__.py
│   ├── config.py               # General config (log format, auth paths)
│   ├── connection.py           # WebSocket base class
│   ├── logging_config.py       # Multi-file logging system
│   ├── token_manager.py        # Auth/token fetching
│   └── observer/               # Metrics & analysis
│       ├── __init__.py
│       ├── core.py
│       ├── analysis.py
│       ├── metrics.py
│       ├── output.py
│       └── presets.py
│
├── scripts/                    # Standalone utility scripts
│   ├── fetch_token.py          # Browser-based token fetching
│   ├── analyze_logs.py         # Log analysis utilities
│   └── run_benchmark.py        # Performance benchmarking
│
├── challenges/
│   └── grocery-bot/            # Challenge: Grocery Bot
│       ├── README.md           # Challenge rules & documentation
│       ├── docs/               # Additional challenge docs
│       ├── shared/             # Challenge-specific shared code
│       │   ├── __init__.py
│       │   ├── config.py       # Game rules, weights, timeouts
│       │   ├── state.py        # Game state parsing
│       │   ├── types.py        # Data classes (Bot, Item, Order)
│       │   ├── utils.py        # Helper functions
│       │   └── tests/          # Tests for shared code
│       │       ├── __init__.py
│       │       ├── test_state.py
│       │       └── test_types.py
│       │
│       ├── theo/               # Theo's implementation
│       │   ├── __init__.py
│       │   ├── bot.py          # Main bot orchestrator
│       │   ├── pathfinding.py  # Pathfinding algorithm
│       │   ├── tasks.py        # Task assignment logic
│       │   ├── actions.py      # Action generation
│       │   ├── collision.py    # Collision avoidance
│       │   ├── tests/          # Theo's tests
│       │   │   ├── __init__.py
│       │   │   ├── test_pathfinding.py
│       │   │   ├── test_tasks.py
│       │   │   └── test_actions.py
│       │   └── README.md       # Theo's approach/notes
│       │
│       ├── mykyta/             # Mykyta's implementation
│       │   ├── __init__.py
│       │   ├── bot.py
│       │   ├── tests/
│       │   │   └── ...
│       │   └── README.md
│       │
│       └── member3/            # Third member (placeholder)
│           └── .gitkeep
│
├── logs/                       # Runtime logs (gitignored)
├── docs/                       # General documentation
├── main.py                     # Single entry point
├── requirements.txt            # Python dependencies
├── README.md                   # Project overview
├── REPO_STRUCTURE.md           # This file
└── .gitignore
```

---

## Branch Strategy

### Branches

| Branch | Purpose | Protected |
|--------|---------|-----------|
| `main` | Stable shared code only | Yes |
| `theo/main` | Theo's working branch | No |
| `mykyta/main` | Mykyta's working branch | No |
| `member3/main` | Third member's working branch | No |

### Workflow

1. **Daily work**: Commit to your personal branch (`<name>/main`)
2. **Shared code updates**: Create PR from your branch to `main`
3. **Sync shared changes**: Merge `main` into your personal branch

```
theo/main ──────┬──────────────────────► main
                │                           │
                │  (PR for shared tools)    │
                │                           │
                └───────────────────────────┘
                     (merge back to sync)
```

### Rules

- Never force-push to `main`
- Always PR to `main` for shared code changes
- Personal directories (`theo/`, `mykyta/`) are owned by that member
- Coordinate before modifying `shared/` or `tools/`

---

## Entry Point

Single entry point via `main.py`:

```bash
# Run with specific bot
python main.py --challenge grocery-bot --bot theo --token <TOKEN>

# Shorter (defaults to grocery-bot if only one challenge)
python main.py --bot theo --token <TOKEN>

# With options
python main.py --bot theo --token <TOKEN> --verbose --observe

# Auto-token mode
python main.py --bot theo --auto-token --difficulty hard
```

### CLI Arguments

| Argument          | Description                                             |
| -------------------| ---------------------------------------------------------|
| `--challenge`     | Challenge name (default: grocery-bot)                   |
| `--bot`           | Bot implementation to use (theo, mykyta, member3)       |
| `--token`         | Game token                                              |
| `--auto-token`    | Fetch token automatically via browser                   |
| `--difficulty`    | Game difficulty (easy, medium, hard, expert, nightmare) |
| `--verbose`, `-v` | Enable verbose logging                                  |
| `--observe`, `-o` | Enable observation metrics                              |

---

## Import Paths

### From shared code

```python
# General tools
from tools.connection import GameConnection
from tools.logging_config import get_logger, LogCategory
from tools.token_manager import TokenManager

# Challenge-specific shared
from challenges.grocery_bot.shared.state import GameState
from challenges.grocery_bot.shared.types import Bot, Item, Order
from challenges.grocery_bot.shared.config import MAX_INVENTORY_SIZE
```

### From member's bot

```python
# In challenges/grocery_bot/theo/bot.py
from challenges.grocery_bot.shared.state import GameState
from challenges.grocery_bot.shared.types import Bot as GameBot
```

---

## Testing

### Run tests for specific member

```bash
# Theo's tests
pytest challenges/grocery-bot/theo/tests/

# Mykyta's tests
pytest challenges/grocery-bot/mykyta/tests/

# Shared code tests
pytest challenges/grocery-bot/shared/tests/
```

### Run all tests

```bash
pytest challenges/grocery-bot/
```

---

## Adding a New Challenge

1. Create directory: `challenges/<challenge-name>/`
2. Add `README.md` with challenge rules
3. Create `shared/` directory with challenge-specific types and parsing
4. Create member directories: `theo/`, `mykyta/`, `member3/`
5. Update `main.py` to support the new challenge

---

## Migration Plan

From current structure to new structure:

### Phase 1: Create directories
- [ ] Create `tools/` directory
- [ ] Create `challenges/grocery-bot/` structure
- [ ] Create member directories

### Phase 2: Move shared code
- [ ] Move `src/connection.py` → `tools/connection.py`
- [ ] Move `src/logging_config.py` → `tools/logging_config.py`
- [ ] Move `src/token_manager.py` → `tools/token_manager.py`
- [ ] Move `src/observer/` → `tools/observer/`
- [ ] Move `src/state.py` → `challenges/grocery-bot/shared/state.py`
- [ ] Move `src/utils.py` → `challenges/grocery-bot/shared/utils.py`

### Phase 3: Move member code (Theo)
- [ ] Move `src/bot.py` → `challenges/grocery-bot/theo/bot.py`
- [ ] Move `src/pathfinding.py` → `challenges/grocery-bot/theo/pathfinding.py`
- [ ] Move `src/tasks.py` → `challenges/grocery-bot/theo/tasks.py`
- [ ] Move `src/actions.py` → `challenges/grocery-bot/theo/actions.py`
- [ ] Move `src/collision.py` → `challenges/grocery-bot/theo/collision.py`
- [ ] Move `tests/` → `challenges/grocery-bot/theo/tests/`

### Phase 4: Update imports
- [ ] Update all import paths in moved files
- [ ] Update `main.py` entry point
- [ ] Update `config.py` split

### Phase 5: Create branches
- [ ] Create `theo/main` branch
- [ ] Create `mykyta/main` branch (placeholder)
- [ ] Create `member3/main` branch (placeholder)

### Phase 6: Cleanup
- [ ] Remove old `src/` directory
- [ ] Update `README.md`
- [ ] Update `.gitignore`

---

## Notes

- `logs/` and `observer_logs/` are gitignored
- Each member owns their directory completely
- Coordinate on `shared/` changes via PR/communication
- Keep `tools/` minimal and well-documented
