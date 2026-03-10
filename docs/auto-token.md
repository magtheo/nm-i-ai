# Auto-Token Feature

Automatically fetch game tokens using browser automation, eliminating the need to manually copy tokens from the browser for each game.

## Quick Start

```bash
# 1. Install browser (one-time)
playwright install chromium

# 2. (Optional) Pre-login to save browser session
python scripts/login.py

# 3. Run the bot
python main.py --auto-token --difficulty medium
```

## How It Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     Optional Pre-Login (run once)                        │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   python scripts/login.py                                                │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐     ┌─────────────┐     ┌────────────────────────┐   │
│   │   Chromium   │────▶│  You login  │────▶│  Browser state saved   │   │
│   │   browser    │     │  via magic  │     │  for future sessions   │   │
│   └──────────────┘     │    link     │     └────────────────────────┘   │
│                        └─────────────┘                    │              │
│                                                          ▼              │
│                                            ~/.config/nm-game/           │
│                                            browser_state/               │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                           Run Bot (each game)                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   python main.py --auto-token --difficulty medium                        │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐     ┌─────────────────────────────────────────────┐  │
│   │   Chromium   │────▶│  Load browser state (if previously saved)   │  │
│   │   browser    │     │  Navigate to app.ainm.no/challenge          │  │
│   └──────────────┘     └─────────────────────────────────────────────┘  │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  If logged in: JWT cookie auto-set (no interaction needed)       │  │
│   │  If not logged in: User logs in via magic link                   │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────────────┐     ┌─────────────────────────────────────┐  │
│   │ Extract access_token │────▶│  POST api.ainm.no/games/request     │  │
│   │ cookie (fresh JWT)   │     │  Cookie: access_token=<JWT>         │  │
│   └──────────────────────┘     │  Body: {"difficulty": "medium"}     │  │
│                                └─────────────────────────────────────┘  │
│                                             │                            │
│                                             ▼                            │
│                                ┌─────────────────────────────────────┐  │
│                                │  Response: {"token": "<game_token>"}│  │
│                                └─────────────────────────────────────┘  │
│                                             │                            │
│                                             ▼                            │
│                                ┌─────────────────────────────────────┐  │
│                                │  Browser closes, game starts        │  │
│                                │  wss://game.ainm.no/ws?token=...    │  │
│                                └─────────────────────────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

## Setup Instructions

### Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Install Chromium browser for Playwright (one-time)
playwright install chromium
```

### Step 1: (Optional) Pre-Login

```bash
python scripts/login.py
```

This will:
1. Open a Chromium browser window
2. Navigate to https://app.ainm.no/challenge
3. Wait for you to login via magic link
4. Save browser state for future sessions
5. Close the browser

Browser state is saved to: `~/.config/nm-game/browser_state/`

**Note:** This step is optional. If you skip it, the browser will open on your first `--auto-token` run and you'll login then.

### Step 2: Run the Bot

```bash
# Default difficulty (medium)
python main.py --auto-token

# Specify difficulty
python main.py --auto-token --difficulty hard

# Short form
python main.py -a -d expert
```

Each run will:
1. Open browser (reusing saved session if available)
2. Extract fresh JWT from cookies
3. Fetch game token from API
4. Close browser and start game

## CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--auto-token` | `-a` | Fetch token automatically via browser |
| `--difficulty` | `-d` | Game difficulty (used with `--auto-token`) |
| `--token` | `-t` | Manual token (existing method) |
| `--verbose` | `-v` | Enable debug logging |

### Difficulty Levels

- `easy`
- `medium` (default)
- `hard`
- `expert`
- `nightmare`

## Architecture

### Files

```
src/token_manager.py    # TokenManager class for fetching game tokens
scripts/login.py        # Browser automation to login and save session
config.py               # API URL and browser state path constants
main.py                 # CLI with --auto-token flag
```

### TokenManager Class

```python
from src.token_manager import TokenManager

manager = TokenManager()

# Fetch game token via browser automation (async)
game_token = await manager.fetch_game_token(difficulty="medium")
```

### API Details

**Endpoint:** `POST https://api.ainm.no/games/request`

**Request:**
```json
{
  "difficulty": "medium"
}
```

**Authentication:** Cookie `access_token=<JWT>`

**Response:**
```json
{
  "token": "<game_token>"
}
```

## Backward Compatibility

The existing token methods still work:

```bash
# Environment variable
export NM_GAME_TOKEN=your_token
python main.py

# CLI argument
python main.py --token your_token
```

## Security

- Browser state is stored in `~/.config/nm-game/browser_state/` with restricted permissions
- The directory is excluded from git via `.gitignore`
- Browser window is visible - you control the login
- JWT is one-time use and extracted fresh for each game session
- Never commit your browser state to version control

## Troubleshooting

### "Playwright not installed"

```bash
pip install playwright
playwright install chromium
```

### "Timeout: No token detected after 5 minutes"

The script waits up to 5 minutes for you to login. If you need more time, run the script again.

### "API response missing 'token' field"

The API response format may have changed. Check the response:
```bash
python main.py --auto-token --verbose
```

### "Invalid difficulty"

Valid difficulties are: `easy`, `medium`, `hard`, `expert`, `nightmare`

### Session Expired / Login Required

If your browser session expires, the browser will open and prompt for login again. Use magic link to re-authenticate.

## Token Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Each Game Session                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   python main.py --auto-token --difficulty medium                        │
│          │                                                               │
│          ▼                                                               │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│   │  Browser opens  │────▶│  Fresh JWT      │────▶│  Game token     │   │
│   │  (reuses state) │     │  extracted      │     │  fetched        │   │
│   └─────────────────┘     └─────────────────┘     └─────────────────┘   │
│          │                                               │               │
│          ▼                                               ▼               │
│   ┌─────────────────┐                           ┌─────────────────┐     │
│   │  Browser closes │                           │  Game starts    │     │
│   └─────────────────┘                           └─────────────────┘     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                      Session Persistence                                 │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   scripts/login.py saves browser state to:                               │
│   ~/.config/nm-game/browser_state/                                       │
│                                                                          │
│   This allows subsequent --auto-token runs to skip login.                │
│   When session expires, browser prompts for re-authentication.           │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

The browser session persists across runs. When it eventually expires, you'll be prompted to login again via magic link.
