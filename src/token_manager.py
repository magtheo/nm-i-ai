"""Token manager for automatic game token fetching."""

import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from config import AUTH_TOKEN_PATH, GAME_REQUEST_API_URL

logger = logging.getLogger(__name__)

VALID_DIFFICULTIES = ["easy", "medium", "hard", "expert", "nightmare"]


class TokenManager:
    """Manages JWT authentication and game token fetching."""
    
    def __init__(self, auth_token_path: Path | None = None):
        self.auth_token_path = auth_token_path or AUTH_TOKEN_PATH
    
    def load_auth_token(self) -> str:
        """Load JWT auth token from file.
        
        Raises:
            FileNotFoundError: If auth token file doesn't exist
            ValueError: If token is empty
        """
        if not self.auth_token_path.exists():
            raise FileNotFoundError(
                f"Auth token file not found: {self.auth_token_path}\n"
                "Run 'python scripts/save_token.py' first to save your JWT."
            )
        
        token = self.auth_token_path.read_text().strip()
        if not token:
            raise ValueError(f"Auth token file is empty: {self.auth_token_path}")
        
        return token
    
    async def fetch_game_token(self, difficulty: str = "medium") -> str:
        """Fetch a game token from the API.
        
        Args:
            difficulty: Game difficulty (easy, medium, hard, expert, nightmare)
            
        Returns:
            Game token for WebSocket connection
            
        Raises:
            ValueError: If difficulty is invalid
            httpx.HTTPStatusError: If API request fails
        """
        if difficulty not in VALID_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty '{difficulty}'. "
                f"Must be one of: {', '.join(VALID_DIFFICULTIES)}"
            )
        
        auth_token = self.load_auth_token()
        
        logger.info(f"Requesting game token for difficulty: {difficulty}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GAME_REQUEST_API_URL,
                json={"difficulty": difficulty},
                cookies={"access_token": auth_token},
                timeout=30.0,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "token" not in data:
                raise ValueError(
                    f"API response missing 'token' field. Got keys: {list(data.keys())}"
                )
            
            game_token = data["token"]
            logger.info("Successfully fetched game token")
            return game_token
