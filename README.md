# Grocery Bot - NM i AI 2026

A competitive AI bot for the Grocery Bot Challenge warm-up competition. The bot controls worker bots in a simulated grocery store to pick up items and deliver them to complete orders.

## Prerequisites

- Python 3.10+
- pip (Python package manager)

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd nm-i-ai
   ```

2. Create and activate virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. (Optional) For auto-token feature:
   ```bash
   pip install playwright && playwright install chromium
   ```

## Getting a Game Token

### Manual Method
1. Go to https://app.ainm.no/challenge
2. Select a difficulty level (Easy, Medium, Hard, Expert, or Nightmare)
3. Click "Play" to generate a token
4. Copy the token from the URL (the part after `token=`)

### Automatic Method
Use the `--auto-token` flag which opens a browser, logs you in, and fetches the token automatically:
```bash
./run.sh play -b theo -d easy -a
```

## Running the Bot

### Interactive Mode (Recommended)
```bash
./run.sh
```
Presents a menu to select challenge, bot, difficulty, and token mode.

### Direct Mode
```bash
./run.sh play -b theo -d easy -a      # Auto-token, easy difficulty
./run.sh play -b mykyta -d hard -t TOKEN  # Manual token, hard difficulty
```

### History Commands
```bash
./run.sh --history    # Show command history and re-run selected
./run.sh --last       # Re-run the last command
```

### Command Options
| Flag | Description |
|------|-------------|
| `-b, --bot` | Bot implementation: `theo`, `mykyta`, `member3` |
| `-d, --difficulty` | Difficulty: `easy`, `medium`, `hard`, `expert`, `nightmare` |
| `-t, --token` | Game token (manual mode) |
| `-a, --auto-token` | Fetch token automatically (opens browser) |
| `-v, --verbose` | Enable verbose logging |
| `-o, --observe` | Enable observation metrics |

## Running Tests

### Bot-specific Tests
```bash
./run.sh test -b theo      # Test theo's implementation
./run.sh test -b mykyta    # Test mykyta's implementation
```

### Direct pytest Commands
```bash
pytest challenges/grocery_bot/theo/tests/      # Theo's tests
pytest challenges/grocery_bot/shared/tests/    # Shared tests
pytest challenges/grocery_bot/theo/tests/ -v   # Verbose output
```

## Logging System

The bot uses a multi-file logging system to separate overview logs from detailed debug information.

### Log Files

All logs are written to the `logs/` directory:

| File | Purpose | Level |
|------|---------|-------|
| `main.log` | Overview: rounds, scores, errors, warnings | INFO+ |
| `bot.log` | Bot operations and decision making | DEBUG+ |
| `pathfinding.log` | Pathfinding and navigation details | DEBUG+ |
| `tasks.log` | Task assignment details | DEBUG+ |
| `actions.log` | Action generation details | DEBUG+ |
| `connection.log` | WebSocket connection details | DEBUG+ |
| `collision.log` | Collision avoidance details | DEBUG+ |

### Usage

- **Check `main.log`** for an overview of what happened during a game
- **Check specific logs** (e.g., `pathfinding.log`) when debugging specific issues
- Use `-v` flag to also see DEBUG messages in the console

### Log Format

```
2026-03-10 22:27:30 | INFO     | Round 10/300 | Score: 15
```

Each game session starts with a separator line for easy identification.

## Project Structure

```
nm-i-ai/
├── run.sh                    # Main entry script (interactive/direct mode)
├── main.py                   # Python entry point
├── config.py                 # Configuration settings
├── requirements.txt          # Python dependencies
│
├── challenges/
│   └── grocery_bot/          # Grocery Bot challenge
│       ├── shared/           # Shared code between bots
│       │   ├── config.py     # Shared configuration
│       │   ├── state.py      # Game state parsing
│       │   ├── utils.py      # Helper functions
│       │   └── tests/        # Shared tests
│       ├── theo/             # Theo's bot implementation
│       │   ├── bot.py        # Main bot orchestrator
│       │   ├── pathfinding.py
│       │   ├── tasks.py
│       │   ├── actions.py
│       │   ├── collision.py
│       │   └── tests/
│       ├── mykyta/           # Mykyta's bot implementation
│       └── member3/          # Member3's bot implementation
│
├── tools/                    # Shared utilities
│   ├── connection.py         # WebSocket connection handling
│   ├── logging_config.py     # Multi-file logging configuration
│   ├── observer/             # Observation metrics
│   └── token_manager.py      # Auto-token fetching
│
├── testing/
│   ├── test_performance.py   # Performance benchmarks
│   ├── mock_states/          # Mock game states
│   ├── results/              # Test results
│   └── bugs/                 # Bug tracking
│
└── logs/                     # Log files (gitignored)
```

## Game Rules

- Control bots to navigate a grid-based grocery store
- Pick up items from shelves and deliver to drop-off zones
- Complete orders to earn points (+1 per item, +5 per completed order)
- 2 second timeout per round
- 300 rounds max (500 for Nightmare difficulty)

## Difficulty Levels

| Difficulty | Bots | Max Rounds |
|------------|------|------------|
| Easy       | 1    | 300        |
| Medium     | 3    | 300        |
| Hard       | 5    | 300        |
| Expert     | 10   | 300        |
| Nightmare  | 20   | 500        |

## Troubleshooting

### "ModuleNotFoundError: No module named 'websockets'"

Install dependencies:
```bash
pip install -r requirements.txt
```

### "HTTP 403 Forbidden"

The token has expired or already been used. Get a fresh token from https://app.ainm.no/challenge

### "command not found: python"

Try using `python3` instead, or ensure your virtual environment is activated:
```bash
source .venv/bin/activate
```

### "Virtual environment not found"

Create and activate the virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Tests Failing

Ensure all dependencies are installed and you're in the project root directory.
