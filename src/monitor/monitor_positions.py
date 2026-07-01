import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

# Ensure project root is on sys.path and load environment variables
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

# The default production deployment URL or local dev URL
DEFAULT_APP_URL = "https://stock-recommendation-engine-rouge.vercel.app"
APP_URL = os.environ.get("APP_URL", DEFAULT_APP_URL).rstrip("/")

def monitor_open_positions():
    sync_url = f"{APP_URL}/api/sync-market"
    print(f"[MONITOR] Invoking unified market sync at: {sync_url}")
    
    try:
        # Call the centralized Next.js endpoint to trigger standard checks
        resp = requests.post(sync_url, timeout=30)
        
        if resp.status_code == 200:
            data = resp.json()
            print("[MONITOR] Sync completed successfully!")
            summary = data.get("summary", {})
            print(f"  Processed Pending: {summary.get('processedPending', 0)}")
            print(f"    - Opened: {summary.get('openedPending', 0)}")
            print(f"    - Cancelled (Gap Up): {summary.get('cancelledGapUp', 0)}")
            print(f"    - Cancelled (Gap Down): {summary.get('cancelledGapDown', 0)}")
            print(f"  Processed Open: {summary.get('processedOpen', 0)}")
            print(f"    - Closed Exits: {summary.get('closedExits', 0)}")
            print(f"    - Splits Adjusted: {summary.get('splitsAdjusted', 0)}")
            print(f"    - Ratcheted Trailing Stops: {summary.get('ratchetedStops', 0)}")
            print(f"    - Updated Ticks: {summary.get('updatedPrices', 0)}")
            
            errors = summary.get("errors", [])
            if errors:
                print("  Warnings/Errors encountered:")
                for err in errors:
                    print(f"    - {err}")
        elif resp.status_code == 403:
            data = resp.json()
            print(f"[MONITOR WARNING] Market is closed: {data.get('reason', 'Outside hours')}")
        else:
            print(f"[MONITOR ERROR] Sync failed with status code {resp.status_code}: {resp.text}")
            sys.exit(1)
            
    except Exception as e:
        print(f"[MONITOR ERROR] Failed to connect to Next.js API sync route: {e}")
        sys.exit(1)

if __name__ == '__main__':
    monitor_open_positions()
