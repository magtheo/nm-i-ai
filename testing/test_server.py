#!/usr/bin/env python3
"""Test script to run bot against game server."""

import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot import GroceryBot
from src.connection import GameConnection


async def test_difficulty(token: str, difficulty: str = "easy"):
    """Test bot against a specific difficulty.
    
    Args:
        token: Game token from the website
        difficulty: Difficulty level (easy, medium, hard, expert, nightmare)
    
    Returns:
        Dict with test results or None on error
    """
    print(f"\n{'='*50}")
    print(f"Testing {difficulty.upper()} difficulty")
    print(f"{'='*50}")
    
    bot = GroceryBot()
    connection = GameConnection(token)
    
    try:
        result = await connection.play_game(bot)
        
        score = result.get("score", 0)
        items = result.get("items", 0)
        orders = result.get("orders", 0)
        
        print(f"\nResults:")
        print(f"  Score:   {score}")
        print(f"  Items:   {items}")
        print(f"  Orders:  {orders}")
        print(f"\n✅ Test completed successfully")
        
        return {
            "difficulty": difficulty,
            "score": score,
            "items": items,
            "orders": orders,
            "success": True
        }
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            "difficulty": difficulty,
            "error": str(e),
            "success": False
        }


async def test_all_difficulties(token: str):
    """Test bot against all difficulty levels."""
    difficulties = ["easy", "medium", "hard", "expert", "nightmare"]
    results = []
    
    for diff in difficulties:
        print(f"\nNote: You need to get a new token for each difficulty from app.ainm.no")
        result = await test_difficulty(token, diff)
        results.append(result)
        
        # Ask before continuing
        if diff != difficulties[-1]:
            response = input("\nContinue to next difficulty? (y/n): ")
            if response.lower() != 'y':
                break
    
    # Summary
    print(f"\n{'='*50}")
    print("SUMMARY")
    print(f"{'='*50}")
    
    for r in results:
        if r.get("success"):
            print(f"{r['difficulty']:12} - Score: {r['score']:4}, Orders: {r['orders']}")
        else:
            print(f"{r['difficulty']:12} - FAILED: {r.get('error', 'Unknown error')}")
    
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python testing/test_server.py <token>")
        print("\nGet a token from: https://app.ainm.no/challenge")
        print("Click 'Play' on a difficulty to get a token")
        sys.exit(1)
    
    token = sys.argv[1]
    
    # Run test
    asyncio.run(test_difficulty(token))


if __name__ == "__main__":
    main()
