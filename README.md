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

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   
   Or if you're on a system with externally-managed Python:
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```

## Getting a Game Token

1. Go to https://app.ainm.no/challenge
2. Select a difficulty level (Easy, Medium, Hard, Expert, or Nightmare)
3. Click "Play" to generate a token
4. Copy the token from the URL (the part after `token=`)

## Running the Bot

### Basic Usage
```bash
python main.py --token <your_token>
```

### With Verbose Logging
```bash
python main.py --token <your_token> -v
```

### Using the Test Script
```bash
python testing/test_server.py <your_token>
```

## Running Tests

### Run All Unit Tests
```bash
pytest tests/
```

### Run Specific Test File
```bash
pytest tests/test_pathfinding.py
```

### Run with Verbose Output
```bash
pytest tests/ -v
```

### Run Performance Tests
```bash
python testing/test_performance.py
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
├── main.py              # Main entry point
├── config.py            # Configuration settings
├── requirements.txt     # Python dependencies
├── src/
│   ├── bot.py           # Main bot orchestrator
│   ├── connection.py    # WebSocket connection handling
│   ├── state.py         # Game state parsing
│   ├── pathfinding.py   # BFS pathfinding
│   ├── tasks.py         # Task assignment
│   ├── actions.py       # Action generation
│   ├── collision.py     # Collision avoidance
│   ├── logging_config.py # Multi-file logging configuration
│   └── utils.py         # Helper functions
├── tests/
│   ├── test_pathfinding.py
│   ├── test_state.py
│   └── test_actions.py
└── testing/
    ├── test_server.py       # Live server test script
    ├── test_performance.py  # Performance benchmarks
    ├── testing-plan.md      # Testing documentation
    └── mock_states/         # Mock game states
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

Try using `python3` instead:
```bash
python3 main.py --token <your_token>
```

### Tests Failing

Ensure all dependencies are installed and you're in the project root directory.
