#!/usr/bin/env python3
"""Standalone script to pre-login via browser.

Usage:
    python scripts/login.py

Opens a browser window for you to login. The browser session is persisted
so subsequent runs of `python main.py --auto-token` won't require re-login.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BROWSER_STATE_PATH, LOGIN_URL

TIMEOUT_SECONDS = 300


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: Playwright not installed.")
        print("Install with: pip install playwright && playwright install chromium")
        return 1
    
    print("=" * 60)
    print("NM Game - Browser Login")
    print("=" * 60)
    print()
    print("A browser window will open.")
    print("Login to your account if not already logged in.")
    print("The browser session will be saved for future use.")
    print()
    print("Press Ctrl+C to cancel at any time.")
    print("-" * 60)
    
    BROWSER_STATE_PATH.mkdir(parents=True, exist_ok=True)
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(BROWSER_STATE_PATH),
            headless=False,
        )
        page = context.new_page()
        
        print(f"\nOpening {LOGIN_URL}...")
        page.goto(LOGIN_URL)
        
        print("\nWaiting for login...")
        
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
                context.close()
                return 1
            
            print()
            print("-" * 60)
            print("Login successful!")
            print(f"Browser state saved to: {BROWSER_STATE_PATH}")
            print()
            print("You can now run the bot with:")
            print("  python main.py --auto-token --difficulty medium")
            
            context.close()
            return 0
            
        except KeyboardInterrupt:
            print("\nCancelled by user.")
            context.close()
            return 1


if __name__ == "__main__":
    sys.exit(main())
