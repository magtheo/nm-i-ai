"""Token manager for automatic game token fetching."""

import logging
from pathlib import Path

import httpx

from challenges.grocery_bot.shared.config import BROWSER_STATE_PATH, LOGIN_URL, GAME_REQUEST_API_URL, MAPS_API_URL

logger = logging.getLogger(__name__)

VALID_DIFFICULTIES = ["easy", "medium", "hard", "expert", "nightmare"]
LOGIN_TIMEOUT_SECONDS = 300


class TokenManager:
    """Manages JWT authentication and game token fetching."""
    
    def __init__(self, browser_state_path = None):
        self.browser_state_path = browser_state_path or BROWSER_STATE_PATH
    
    async def fetch_fresh_jwt(self) -> str:
        """Fetch fresh JWT by opening browser and extracting access_token cookie.
        
        Uses persistent browser state so user only needs to login once.
        
        Returns:
            JWT access token value
            
        Raises:
            ImportError: If playwright not installed
            TimeoutError: If login doesn't complete within timeout
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed. Install with: pip install playwright && playwright install"
            )
        
        logger.info(f"Opening browser for login at {LOGIN_URL}")
        logger.info("Please log in with your credentials...")
        
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.browser_state_path),
                headless=False
            )
            logger.info(f"Using browser state from {self.browser_state_path}")
            
            page = await context.new_page()
            await page.goto(LOGIN_URL)
            
            import asyncio
            
            for _ in range(LOGIN_TIMEOUT_SECONDS):
                cookies = await context.cookies()
                access_token = next(
                    (c for c in cookies if c["name"] == "access_token"),
                    None
                )
                if access_token:
                    await context.close()
                    logger.info("Login successful!")
                    return access_token["value"]
                
                await asyncio.sleep(1)
            
            await context.close()
            raise TimeoutError("Login timed out after 5 minutes")
    
    async def fetch_game_token(self, difficulty: str = "medium") -> str:
        """Fetch a game token for the specified difficulty.
        
        Args:
            difficulty: Game difficulty (easy, medium, hard, expert, nightmare)
            
        Returns:
            Game token string
            
        Raises:
            ValueError: If difficulty is invalid or map not found
            RuntimeError: If token fetch fails
        """
        if difficulty not in VALID_DIFFICULTIES:
            raise ValueError(f"Invalid difficulty: {difficulty}. Must be one of {VALID_DIFFICULTIES}")
        
        maps = await self.get_available_maps()
        map_info = next((m for m in maps if m.get("difficulty") == difficulty), None)
        if not map_info:
            raise ValueError(f"No map found for difficulty: {difficulty}")
        
        map_id = map_info["id"]
        
        jwt = await self.fetch_fresh_jwt()
        
        logger.debug(f"Map ID for {difficulty}: {map_id}")
        logger.debug(f"JWT length: {len(jwt)}")
        
        async with httpx.AsyncClient() as client:
            logger.debug(f"POST {GAME_REQUEST_API_URL}")
            logger.debug(f"Request body: map_id={map_id}")
            
            response = await client.post(
                GAME_REQUEST_API_URL,
                json={"map_id": map_id},
                cookies={"access_token": jwt}
            )
            
            logger.debug(f"Response status: {response.status_code}")
            logger.debug(f"Response body: {response.text}")
            
            if response.status_code == 403:
                raise RuntimeError("JWT expired or invalid. Try logging in again.")
            
            response.raise_for_status()
            data = response.json()
            
            token = data.get("token")
            if not token:
                raise RuntimeError(f"No token in response: {data}")
            
            logger.info(f"Got game token for {difficulty} difficulty (mapId: {map_id})")
            return token
    
    async def get_available_maps(self) -> list[dict]:
        """Get list of available maps.
        
        Returns:
            List of map info dictionaries
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(MAPS_API_URL)
            response.raise_for_status()
            return response.json()
