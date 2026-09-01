"""
Clear Database Tables and Reset Portfolio State
===============================================
Clears:
- signals
- signals_history
- scan_log
- portfolio_state (resets to $10,000.00 base)
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load env from root
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)

from supabase import create_client

def clear_database():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        print("ERROR: Supabase credentials missing in environment.")
        sys.exit(1)

    supabase = create_client(url, key)
    print("=" * 60)
    print("CLEARING DATABASE TABLES")
    print("=" * 60)

    # 1. Clear signals table
    try:
        res = supabase.table("signals").delete().neq("ticker", "___NON_EXISTENT___").execute()
        count = len(res.data) if res.data else 0
        print(f"[OK] Cleared 'signals' table: {count} records deleted.")
    except Exception as e:
        print(f"[ERR] Error clearing 'signals': {e}")

    # 2. Clear signals_history table
    try:
        res = supabase.table("signals_history").delete().neq("ticker", "___NON_EXISTENT___").execute()
        count = len(res.data) if res.data else 0
        print(f"[OK] Cleared 'signals_history' table: {count} records deleted.")
    except Exception as e:
        print(f"[ERR] Error clearing 'signals_history': {e}")

    # 3. Clear scan_log table
    try:
        res = supabase.table("scan_log").delete().neq("scan_date", "1970-01-01").execute()
        count = len(res.data) if res.data else 0
        print(f"[OK] Cleared 'scan_log' table: {count} records deleted.")
    except Exception as e:
        print(f"[ERR] Error clearing 'scan_log': {e}")

    # 4. Reset portfolio_state table
    try:
        supabase.table("portfolio_state").delete().neq("date", "1970-01-01").execute()
        today = datetime.now().strftime("%Y-%m-%d")
        supabase.table("portfolio_state").insert({
            "date": today,
            "portfolio_value": 10000.0,
            "peak_value": 10000.0,
            "current_drawdown_pct": 0.0
        }).execute()
        print("[OK] Reset 'portfolio_state' table to $10,000.00 base value.")
    except Exception as e:
        print(f"[ERR] Error resetting 'portfolio_state': {e}")

    print("=" * 60)
    print("DATABASE CLEANUP COMPLETED")
    print("=" * 60)

if __name__ == "__main__":
    clear_database()
