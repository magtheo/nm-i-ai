"""Configuration settings for the Grocery Bot."""

import os

# WebSocket URL template - replace YOUR_TOKEN with actual token
WS_URL_TEMPLATE = "wss://game.ainm.no/ws?token={token}"

# Game settings
MAX_INVENTORY_SIZE = 3
ROUND_TIMEOUT_SECONDS = 2.0
MAX_ROUNDS = 300
MAX_ROUNDS_NIGHTMARE = 500

# Token from environment or empty
GAME_TOKEN = os.environ.get("NM_GAME_TOKEN", "")

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Pathfinding
CACHE_DISTANCE_TABLES = True

# Task assignment weights
WEIGHT_ACTIVE_ITEM = 1.0
WEIGHT_ORDER_COMPLETION = 5.0
WEIGHT_PREVIEW_ITEM = 0.5
WEIGHT_POSITIONING = 0.1

# Collision avoidance
COLLISION_LOOKAHEAD_STEPS = 3
