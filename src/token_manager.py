"""Token manager for automatic game token fetching."""

import logging

import httpx

from config import BROWSER_STATE_PATH, LOGIN_URL, GAME_REQUEST_API_URL, MAPS_API_URL

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
                "Playwright not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )
        
        self.browser_state_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Opening browser to fetch fresh JWT...")
        
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(self.browser_state_path),
                headless=False,
            )
            page = await context.new_page()
            
            logger.info(f"Navigating to {LOGIN_URL}...")
            await page.goto(LOGIN_URL)
            
            import time
            start_time = time.time()
            access_token = None
            
            while time.time() - start_time < LOGIN_TIMEOUT_SECONDS:
                await page.wait_for_timeout(1000)
                cookies = await context.cookies()
                access_token = next(
                    (c["value"] for c in cookies if c["name"] == "access_token"),
                    None
                )
                if access_token:
                    logger.info("Successfully extracted JWT from browser")
                    break
            
            await context.close()
            
            if not access_token:
                raise TimeoutError(
                    f"Login timeout: No token detected after {LOGIN_TIMEOUT_SECONDS} seconds."
                )
            
            return access_token
    
    async def fetch_maps(self) -> list:
        """Fetch available maps from the API.
        
        Returns:
            List of maps, each with id, difficulty, and label fields
            
        Raises:
            httpx.HTTPStatusError: If API request fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(MAPS_API_URL, timeout=30.0)
            response.raise_for_status()
            return response.json()
    
    async def fetch_game_token(self, difficulty: str = "medium") -> str:
        """Fetch a game token from the API.
        
        Opens browser to get fresh JWT, then uses it to request game token.
        
        Args:
            difficulty: Game difficulty (easy, medium, hard, expert, nightmare)
            
        Returns:
            Game token for WebSocket connection
            
        Raises:
            ValueError: If difficulty is invalid
            ImportError: If playwright not installed
            TimeoutError: If login doesn't complete within timeout
            httpx.HTTPStatusError: If API request fails
        """
        if difficulty not in VALID_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty '{difficulty}'. "
                f"Must be one of: {', '.join(VALID_DIFFICULTIES)}"
            )
        
        jwt_token = await self.fetch_fresh_jwt()
        
        logger.info(f"Fetching available maps...")
        maps = await self.fetch_maps()
        
        map_id = next(
            (m["id"] for m in maps if m["difficulty"] == difficulty),
            None
        )
        if not map_id:
            raise ValueError(
                f"No map found for difficulty '{difficulty}'. "
                f"Available difficulties: {', '.join(m['difficulty'] for m in maps)}"
            )
        
        logger.info(f"Requesting game token for difficulty: {difficulty}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GAME_REQUEST_API_URL,
                json={"difficulty": difficulty, "map_id": map_id},
                cookies={"access_token": jwt_token},
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
