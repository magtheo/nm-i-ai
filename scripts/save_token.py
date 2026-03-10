#!/usr/bin/env python3
"""One-time setup script to save JWT auth token via browser automation.

Usage:
    python scripts/save_token.py

Opens a browser window for you to login, then automatically extracts
the JWT from cookies and saves it locally.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AUTH_TOKEN_PATH

GAME_URL = "https://game.ainm.no"
TIMEOUT_SECONDS = 300


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: Playwright not installed.")
        print("Install with: pip install playwright && playwright install chromium")
        return 1
    
    print("=" * 60)
    print("NM Game - JWT Token Setup")
    print("=" * 60)
    print()
    print("A browser window will open.")
    print("1. Login to your account")
    print("2. Wait for the game page to fully load")
    print("3. The script will auto-detect and save your token")
    print()
    print("Press Ctrl+C to cancel at any time.")
    print("-" * 60)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print(f"\nOpening {GAME_URL}...")
        page.goto(GAME_URL)
        
        print("\nWaiting for you to login...")
        print("(Token will be extracted automatically when logged in)")
        
        try:
            start_time = time.time()
            access_token = None
            
            while time.time() - start_time < TIMEOUT_SECONDS:
                page.wait_for_timeout(1000)
                cookies = context.cookies()
                access_token = next(
                    (c["value"] for c in cookies if c["name"] == "access_token"),
                    None
                )
                if access_token:
                    break
            
            if not access_token:
                print(f"\nTimeout: No token detected after {TIMEOUT_SECONDS} seconds.")
                browser.close()
                return 1
            
            AUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            AUTH_TOKEN_PATH.write_text(access_token)
            AUTH_TOKEN_PATH.chmod(0o600)
            
            print()
            print("-" * 60)
            print(f"Token saved to: {AUTH_TOKEN_PATH}")
            print("File permissions set to 600 (owner read/write only)")
            print()
            print("You can now run the bot with:")
            print("  python main.py --auto-token --difficulty medium")
            
            browser.close()
            return 0
            
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            browser.close()
            return 1


if __name__ == "__main__":
    sys.exit(main())
