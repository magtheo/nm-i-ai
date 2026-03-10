# Auto-Token Feature

Automatically fetch game tokens using a saved JWT, eliminating the need to manually copy tokens from the browser for each game.

## Quick Start

```bash
# 1. Install browser (one-time)
playwright install chromium

# 2. Login and save JWT (repeat when token expires)
python scripts/save_token.py

# 3. Run the bot
python main.py --auto-token --difficulty medium
```

## How It Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Setup (run once or when JWT expires)           │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   scripts/save_token.py                                                  │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐     ┌─────────────┐     ┌────────────────────────┐   │
│   │   Chromium   │────▶│  You login  │────▶│  Extract access_token  │   │
│   │   browser    │     │  manually   │     │  cookie (JWT)          │   │
│   └──────────────┘     └─────────────┘     └────────────────────────┘   │
│                                                     │                    │
│                                                     ▼                    │
│                                          ~/.config/nm-game/auth_token   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                           Run Bot (each game)                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   python main.py --auto-token --difficulty medium                        │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────────────┐     ┌─────────────────────────────────────┐  │
│   │ ~/.config/nm-game/   │────▶│  POST api.ainm.no/games/request     │  │
│   │ auth_token (JWT)     │     │  Cookie: access_token=<JWT>         │  │
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
│                                │  Connect to game WebSocket          │  │
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

### Step 1: Login and Save JWT

```bash
python scripts/save_token.py
```

This will:
1. Open a Chromium browser window
2. Navigate to https://game.ainm.no
3. Wait for you to login manually
4. Automatically detect and save your JWT when logged in
5. Close the browser

The JWT is saved to: `~/.config/nm-game/auth_token`

### Step 2: Run the Bot

```bash
# Default difficulty (medium)
python main.py --auto-token

# Specify difficulty
python main.py --auto-token --difficulty hard

# Short form
python main.py -a -d expert
```

## CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--auto-token` | `-a` | Fetch token automatically using saved JWT |
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
scripts/save_token.py   # Browser automation to extract JWT
config.py               # API URL and auth token path constants
main.py                 # CLI with --auto-token flag
```

### TokenManager Class

```python
from src.token_manager import TokenManager

manager = TokenManager()

# Load saved JWT
jwt = manager.load_auth_token()

# Fetch game token (async)
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

- JWT is stored in `~/.config/nm-game/auth_token` with `600` permissions
- The file is excluded from git via `.gitignore`
- Browser window is visible - you control the login
- Never commit your JWT to version control

## Troubleshooting

### "Playwright not installed"

```bash
pip install playwright
playwright install chromium
```

### "Auth token file not found"

Run the setup script:
```bash
python scripts/save_token.py
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

### JWT Expired

JWTs have an `exp` claim. When expired, re-run:
```bash
python scripts/save_token.py
```

## Token Lifecycle

```
┌─────────────────┐
│  Login via      │
│  browser        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  JWT saved      │────▶│  Fetch game     │
│  (valid ~hours) │     │  tokens as      │
└─────────────────┘     │  needed         │
         │              └─────────────────┘
         │
         ▼ (when expired)
┌─────────────────┐
│  Re-run         │
│  save_token.py  │
└─────────────────┘
```

The JWT typically lasts several hours. You can fetch multiple game tokens with a single JWT. When it expires, re-run `scripts/save_token.py`.
