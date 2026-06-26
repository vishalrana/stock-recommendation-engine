import os
import sys
import json
import pandas as pd

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from jobs.supabase_client import get_client

def main():
    supabase = get_client()

    print("=" * 80)
    print("  VERIFICATION OF NIGHTLY SIGNAL SCAN & DATABASE METRICS")
    print("=" * 80)

    # 1. Find the scan date to validate from the active signals table
    target_res = supabase.table("signals").select("scan_date").limit(1).execute()
    if not target_res.data:
        print("ERROR: No signals found in signals table.")
        sys.exit(1)
    scan_date = target_res.data[0]["scan_date"]

    print("\nFetching latest scan log for date: {}...".format(scan_date))
    log_res = supabase.table("scan_log").select("*").eq("scan_date", scan_date).execute()
    if not log_res.data:
        print("ERROR: No scan log found for date {}.".format(scan_date))
        sys.exit(1)
    
    log = log_res.data[0]
    
    print("\n=== LATEST SCAN LOG ENTRY ===")
    print(f"Scan Date           : {scan_date}")
    print(f"Regime              : {log.get('regime', 'N/A').upper()}")
    print(f"Tickers Scanned     : {log.get('tickers_scanned')}")
    print(f"Signals Qualified   : {log.get('signals_qualified')}")
    print(f"Signals Recommended : {log.get('signals_recommended')}")
    print(f"RSI Breadth Pct     : {log.get('rsi_breadth_pct')}%")
    print(f"Failed Gates Breakdown:")
    print(f"  failed_trend_gate  : {log.get('failed_trend_gate', 0)}")
    print(f"  failed_rsi_gate    : {log.get('failed_rsi_gate', 0)}")
    print(f"  failed_adx_gate    : {log.get('failed_adx_gate', 0)}")
    print(f"  failed_macd_gate   : {log.get('failed_macd_gate', 0)}")
    print(f"  failed_volume_gate : {log.get('failed_volume_gate', 0)}")
    print(f"  failed_rr_gate     : {log.get('failed_rr_gate', 0)}")
    print(f"  failed_maxrisk_gate: {log.get('failed_maxrisk_gate', 0)}")
    print(f"  failed_trades_gate : {log.get('failed_trades_gate', 0)}")
    if log.get("error_message"):
        print(f"Error Message/Warning: {log.get('error_message')}")
    print("-" * 80)

    # 2. Fetch signals for this scan_date from the recommendations view (joins ticker_metrics)
    print(f"\nFetching signals from recommendations view for date {scan_date}...")
    view_res = supabase.table("recommendations").select("*").eq("scan_date", scan_date).execute()
    signals = view_res.data or []
    
    print(f"\n=== GENERATED SIGNALS FOR {scan_date} ===")
    if signals:
        df_sig = pd.DataFrame(signals)
        cols_to_print = [
            "ticker", "company_name", "composite_score", "tier_label", "is_fallback",
            "current_rsi", "adx_value", "volume_ratio", "risk_reward", "past_win_rate", "expectancy_pct"
        ]
        # Clean columns if missing
        for col in cols_to_print:
            if col not in df_sig.columns:
                df_sig[col] = None
        
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        print(df_sig[cols_to_print].to_string(index=False))
    else:
        print("No recommendations found in view.")
    print("-" * 80)

    # 3. Save output to scan_result_20260626.json
    json_path = os.path.join(PROJECT_ROOT, "scan_result_20260626.json")
    print(f"\nSaving signals output to {json_path}...")
    with open(json_path, "w") as f:
        json.dump(signals, f, indent=2, default=str)
    print("Saved successfully.")

    # 4. Compare gate failures against 2026-06-25 scan
    print("\n=== GATE FAILURE COMPARISON ANALYSIS ===")
    prev_res = supabase.table("scan_log").select("*").eq("scan_date", "2026-06-25").execute()
    if prev_res.data:
        prev = prev_res.data[0]
        print(f"Comparison of gate failures (Current {scan_date} vs Previous 2026-06-25):")
        print(f"  Scanned            : {log.get('tickers_scanned')} vs {prev.get('tickers_scanned')}")
        print(f"  failed_trend_gate  : {log.get('failed_trend_gate', 0)} vs {prev.get('failed_trend_gate', 0)}")
        print(f"  failed_rsi_gate    : {log.get('failed_rsi_gate', 0)} vs {prev.get('failed_rsi_gate', 0)}")
        print(f"  failed_adx_gate    : {log.get('failed_adx_gate', 0)} vs {prev.get('failed_adx_gate', 0)}")
        print(f"  failed_macd_gate   : {log.get('failed_macd_gate', 0)} vs {prev.get('failed_macd_gate', 0)}")
        print(f"  failed_volume_gate : {log.get('failed_volume_gate', 0)} vs {prev.get('failed_volume_gate', 0)}")
        print(f"  failed_rr_gate     : {log.get('failed_rr_gate', 0)} vs {prev.get('failed_rr_gate', 0)}")
        print(f"  failed_maxrisk_gate: {log.get('failed_maxrisk_gate', 0)} vs {prev.get('failed_maxrisk_gate', 0)}")
        print(f"  failed_trades_gate : {log.get('failed_trades_gate', 0)} vs {prev.get('failed_trades_gate', 0)}")
        
        # Checking trades gate drop
        cur_trades_failed = log.get('failed_trades_gate', 0)
        prev_trades_failed = prev.get('failed_trades_gate', 0)
        print("\nTrades Gate Status:")
        if cur_trades_failed == 0:
            print("  SUCCESS: failed_trades_gate dropped to 0! All scanned tickers were successfully found in ticker_metrics.")
        else:
            print(f"  INFO: failed_trades_gate is {cur_trades_failed} (compared to {prev_trades_failed} in previous scan).")
    else:
        print("Previous scan log for 2026-06-25 not found for comparison.")

    # 5. Check qualified signals count
    signals_qualified = log.get("signals_qualified", 0)
    print("\nQualified Signals Status:")
    if signals_qualified >= 3:
        print(f"  SUCCESS: Signals qualified is {signals_qualified} (>= 3). The pool is healthy.")
    else:
        print(f"  WARNING: Still generating fewer than 3 signals (Qualified: {signals_qualified}). Investigate remaining gate bottlenecks.")

    # 6. Check auto-relax logic
    signals_recommended = log.get("signals_recommended", 0)
    print("\nAuto-Relax Logic Check:")
    if signals_qualified >= 3:
        if signals_recommended >= 3:
            print("  SUCCESS: Recommended count is >= 3. Auto-relax logic functioned correctly.")
        else:
            print("  ERROR: Auto-relax failed! signals_qualified >= 3 but signals_recommended < 3.")
    else:
        print("  N/A: Qualified signals count is < 3, so auto-relax couldn't group up to 3 recommendations.")

    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
