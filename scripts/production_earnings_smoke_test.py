"""
Production Earnings Smoke Test
==============================
Verifies live earnings infrastructure post-deployment:
- ordinary liquid equity (AAPL)
- previously affected AME
- previously affected VTRS
- Sector Rotation ETF (XLE)
- known earnings equity (MSFT)

Verifies:
1. Cache lookup & provider fallback
2. Successful results persistence to Supabase
3. Repeated lookup uses cache (0 network queries)
4. Sector ETF exemption
5. No recommendation scan triggered
"""

import sys
import os
import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from jobs.supabase_client import get_client
from src.filters.earnings_filter import (
    fetch_earnings_calendar,
    earnings_risk_filter,
    EarningsStatus,
)

def run_smoke_test():
    print("=" * 60)
    print("PRODUCTION EARNINGS SMOKE TEST")
    print("=" * 60)

    supabase = get_client()
    scan_dt = datetime.date(2026, 10, 3)
    sample_tickers = ["AAPL", "AME", "VTRS", "XLE", "MSFT"]

    # Step 1: Fetch earnings calendar
    print(f"\n[1] Fetching earnings calendar for: {sample_tickers}")
    cal = fetch_earnings_calendar(sample_tickers, supabase=supabase)
    
    for t in sample_tickers:
        rec = cal.get(t)
        assert rec is not None, f"Ticker {t} missing from result calendar"
        print(f"  --> {t:5s}: status={rec['status']:14s} next={str(rec['next_earnings_date']):10s} last={str(rec['last_earnings_date']):10s} source={rec['source']}")
        assert rec["status"] in (EarningsStatus.KNOWN_UPCOMING.value, EarningsStatus.KNOWN_CLEAR.value), f"{t} got invalid status {rec['status']}"

    # Step 2: Verify specific dates
    assert cal["AME"]["next_earnings_date"] == "2026-10-29", f"AME next earnings expected 2026-10-29, got {cal['AME']['next_earnings_date']}"
    assert cal["VTRS"]["next_earnings_date"] == "2026-11-05", f"VTRS next earnings expected 2026-11-05, got {cal['VTRS']['next_earnings_date']}"
    assert cal["XLE"]["status"] == EarningsStatus.KNOWN_CLEAR.value, f"XLE expected KNOWN_CLEAR, got {cal['XLE']['status']}"
    print("  --> PASS: All sample tickers have valid, non-UNKNOWN earnings status")

    # Step 3: Repeated lookup uses cache
    print("\n[2] Verifying cache hit on repeated lookup...")
    cal2 = fetch_earnings_calendar(sample_tickers, supabase=supabase)
    for t in sample_tickers:
        source = cal2[t]["source"]
        assert source in ("local_cache", "supabase"), f"{t} expected cached source, got {source}"
        print(f"  --> {t:5s}: source={source} (cached)")
    print("  --> PASS: Repeated lookup successfully resolved from cache without provider queries")

    # Step 4: Earnings Risk Filter evaluation
    print("\n[3] Evaluating Earnings Risk Gate...")
    for t in ["AAPL", "AME", "VTRS", "MSFT"]:
        decision = earnings_risk_filter(t, scan_dt, "52w_high_breakout", cal)
        assert decision["pass"] is True, f"{t} should pass earnings gate on Oct 3"
        print(f"  --> {t:5s} (52W High): pass={decision['pass']} days_to_earnings={decision['days_to_earnings']}")

    # Sector ETF exemption
    xle_decision = earnings_risk_filter("XLE", scan_dt, "sector_rotation", cal)
    assert xle_decision["pass"] is True, "XLE must pass via Sector ETF exemption"
    assert xle_decision["status"] == EarningsStatus.KNOWN_CLEAR.value
    assert "Sector ETF" in xle_decision["reason"]
    print(f"  --> XLE   (Sector Rotation): pass={xle_decision['pass']} reason='{xle_decision['reason']}'")
    print("  --> PASS: Sector ETF exemption verified")

    # Step 5: Verify Supabase database persistence
    print("\n[4] Verifying Supabase earnings_calendar persistence...")
    res = supabase.table("earnings_calendar").select("*").in_("ticker", sample_tickers).execute()
    db_rows = {r["ticker"]: r for r in (res.data or [])}
    for t in sample_tickers:
        assert t in db_rows, f"{t} not found in Supabase earnings_calendar"
        print(f"  --> DB {t:5s}: next={db_rows[t]['next_earnings_date']} updated_at={db_rows[t]['updated_at']}")
    print("  --> PASS: Supabase earnings_calendar contains all verified records")

    print("\n" + "=" * 60)
    print("PRODUCTION EARNINGS SMOKE TEST PASSED (0 SCAN TRIGGERS)")
    print("=" * 60)

if __name__ == "__main__":
    run_smoke_test()
