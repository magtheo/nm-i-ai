"""WebSocket connection handling."""

import asyncio
import json
from typing import Callable

import websockets
from websockets.client import WebSocketClientProtocol

from tools.logging_config import get_logger, LogCategory
from challenges.grocery_bot.shared.config import WS_URL_TEMPLATE, GAME_TOKEN

logger = get_logger(LogCategory.CONNECTION)


class GameConnection:
    """Manages WebSocket connection to game server."""
    
    def __init__(self, token: str | None = None):
        self.token = token or GAME_TOKEN
        self.ws: WebSocketClientProtocol | None = None
        self.on_game_state: Callable | None = None
        self.on_game_over: Callable | None = None
    
    @property
    def ws_url(self) -> str:
        """Get WebSocket URL with token."""
        return WS_URL_TEMPLATE.format(token=self.token)
    
    async def connect(self) -> None:
        """Establish WebSocket connection."""
        logger.info(f"Connecting to {self.ws_url}")
        self.ws = await websockets.connect(self.ws_url)
        logger.info("Connected to game server")
    
    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self.ws:
            await self.ws.close()
            self.ws = None
            logger.info("Disconnected from game server")
    
    async def receive_message(self) -> dict:
        """Receive and parse a message from the server."""
        if not self.ws:
            raise RuntimeError("Not connected to server")
        
        raw_message = await self.ws.recv()
        message = json.loads(raw_message)
        
        msg_type = message.get("type", "unknown")
        logger.debug(f"Received message type: {msg_type}")
        
        if msg_type == "game_over":
            logger.info(f"Game over message received: {message}")
        elif msg_type != "unknown":
            round_num = message.get("round", "?")
            score = message.get("score", 0)
            bots = message.get("bots", [])
            orders = message.get("orders", [])
            logger.debug(f"  Round {round_num} | Score: {score} | Bots: {len(bots)} | Orders: {len(orders)}")
        
        return message
    
    async def send_actions(self, actions: list[dict]) -> None:
        """Send actions to the server."""
        if not self.ws:
            raise RuntimeError("Not connected to server")
        
        message = json.dumps({"actions": actions})
        await self.ws.send(message)
        logger.debug(f"Sent {len(actions)} actions")
    
    async def play_game(self, bot) -> dict:
        """Play a complete game.
        
        Args:
            bot: GroceryBot instance to make decisions
            
        Returns:
            Final game over data
        """
        await self.connect()
        
        try:
            while True:
                message = await self.receive_message()
                
                if message.get("type") == "game_over":
                    logger.info(f"Game over! Score: {message.get('score', 0)}")
                    return message
                
                # Process game state and get actions
                actions = bot.process_round(message)
                await self.send_actions(actions)
                
        finally:
            await self.disconnect()
