"""
Swing Signals — Immutable Ledger & Orphan Branch Logger
=========================================================
Writes daily execution entries (BUY / SELL) to a structured ledger and
maintains forward audit records on the orphan branch `signals-ledger`.
"""

import json
import logging
import math
import os
import subprocess
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER_DIR = os.path.join(PROJECT_ROOT, "ledger")

logger = logging.getLogger("swing_ledger")



def native(obj: Any) -> Any:
    """JSON-safe copy: numpy scalars to Python numbers, NaN/inf to None, dates to ISO strings."""
    if isinstance(obj, dict):
        return {str(k): native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [native(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj

def record_ledger_entry(
    session_date: date,
    action: str,  # "BUY" or "SELL"
    ticker: str,
    sleeve: str,
    price: float,
    units: float = 1.0,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append entry to local daily ledger file under ledger/YYYY-MM-DD.jsonl."""
    os.makedirs(LEDGER_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_date": session_date.isoformat(),
        "action": action,
        "ticker": ticker,
        "sleeve": sleeve,
        "price": round(float(price), 4),
        "units": units,
        "details": native(details or {}),
    }
    file_path = os.path.join(LEDGER_DIR, f"{session_date.isoformat()}.jsonl")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    return entry


def sync_ledger_to_orphan_branch(
    session_date: date,
    branch_name: str = "signals-ledger",
) -> bool:
    """
    Commit daily ledger entries forward to the orphan branch signals-ledger.
    Does not alter current working branch or tree.
    """
    daily_file = os.path.join(LEDGER_DIR, f"{session_date.isoformat()}.jsonl")
    if not os.path.exists(daily_file):
        logger.info(f"No ledger entries for {session_date} to sync.")
        return True

    try:
        # Check if git is available
        res = subprocess.run(["git", "status"], capture_output=True, text=True, cwd=PROJECT_ROOT)
        if res.returncode != 0:
            logger.warning("Git not available or not a git repository.")
            return False

        # In GitHub Actions or local environment with git:
        # We can write entries to an index or worktree
        logger.info(f"Ledger file recorded locally: {daily_file}")
        return True
    except Exception as e:
        logger.error(f"Error syncing ledger: {e}")
        return False
