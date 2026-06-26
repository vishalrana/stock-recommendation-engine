#!/usr/bin/env python
"""
Diagnose Scan Issues — Strategy 1.3 Rev B
==========================================
Diagnostic script to inspect scan_log and signals_history to analyze signal count issues.
"""
import os
import sys
import pandas as pd

# Set up sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from jobs.supabase_client import get_client

def run_diagnostics():
    supabase = get_client()

    print("=" * 80)
    print("  STRATEGY 1.3 REV B — SCAN DIAGNOSTICS & TELEMETRY REPORT")
    print("=" * 80)

    # 1. Fetch scan logs
    print("\nFetching scan logs from database...")
    scan_log_res = supabase.table("scan_log").select("*").order("scan_date", desc=True).execute()
    scan_logs = scan_log_res.data or []
    print(f"Loaded {len(scan_logs)} entries from scan_log.")

    # 2. Print scan log summaries
    print("\n=== SCAN LOG HISTORY ===")
    for row in scan_logs:
        date = row.get("scan_date")
        regime = row.get("regime", "N/A")
        scanned = row.get("tickers_scanned", 0)
        qualified = row.get("signals_qualified", 0)
        recommended = row.get("signals_recommended", 0)
        rsi_breadth = row.get("rsi_breadth_pct")
        rsi_breadth_str = f"{rsi_breadth}%" if rsi_breadth is not None else "N/A"

        # failed gates
        f_rsi = row.get("failed_rsi_gate", 0)
        f_adx = row.get("failed_adx_gate", 0)
        f_macd = row.get("failed_macd_gate", 0)
        f_trend = row.get("failed_trend_gate", 0)
        f_volume = row.get("failed_volume_gate", 0)
        f_rr = row.get("failed_rr_gate", 0)
        f_maxrisk = row.get("failed_maxrisk_gate", 0)
        f_trades = row.get("failed_trades_gate", 0)

        print(f"Date: {date} | Regime: {str(regime).upper():<8} | Scanned: {scanned:<4} | "
              f"Qualified: {qualified:<3} | Recommended: {recommended:<3} | RSI Breadth: {rsi_breadth_str:<6}")
        print(f"  Failed Gates: Trend={f_trend:<3} RSI={f_rsi:<3} ADX={f_adx:<3} MACD={f_macd:<3} Volume={f_volume:<3} RR={f_rr:<3} MaxRisk={f_maxrisk:<3} Trades={f_trades:<3}")
        if row.get("error_message"):
            print(f"  Note/Error: {row.get('error_message')}")
        print("-" * 80)

    # 3. Fetch signals history for grouping
    print("\nFetching history from signals_history...")
    history_res = supabase.table("signals_history").select("scan_date, composite_score, score, tier_label").execute()
    history_data = history_res.data or []
    print(f"Loaded {len(history_data)} entries from signals_history.")

    # Grouping logic
    unrated_counts = {}
    rated_counts = {}

    for row in history_data:
        date = row.get("scan_date")
        score = row.get("composite_score")
        if score is None:
            score = row.get("score")
        score_val = float(score) if score is not None else 0.0
        tier = row.get("tier_label")

        if score_val == 0.0 and tier == "Unrated":
            unrated_counts[date] = unrated_counts.get(date, 0) + 1
        elif score_val > 0.0:
            rated_counts[date] = rated_counts.get(date, 0) + 1

    print("\n=== UNRATED HISTORY SIGNALS (score = 0 & tier = 'Unrated') ===")
    if unrated_counts:
        for date in sorted(unrated_counts.keys(), reverse=True):
            print(f"Scan Date: {date} | Count: {unrated_counts[date]}")
    else:
        print("No unrated signals (score=0, tier='Unrated') found in history.")

    print("\n=== RATED HISTORY SIGNALS (score > 0) ===")
    if rated_counts:
        for date in sorted(rated_counts.keys(), reverse=True):
            print(f"Scan Date: {date} | Count: {rated_counts[date]}")
    else:
        print("No rated signals (score > 0) found in history.")

    # 4. Check ticker_metrics row count
    print("\nChecking ticker_metrics row count...")
    metrics_res = supabase.table("ticker_metrics").select("ticker").execute()
    metrics_count = len(metrics_res.data) if metrics_res.data else 0
    print(f"ticker_metrics row count: {metrics_count}")
    if metrics_count < 400:
        print(f"WARNING: Backtest metrics are not fully seeded. Count is {metrics_count} (Universe is ~514).")
    else:
        print("SUCCESS: Backtest metrics are fully seeded.")

    # 5. Detail report for the 3 most recent scan dates
    unique_dates = sorted(list(set(row.get("scan_date") for row in history_data)), reverse=True)
    recent_dates = unique_dates[:3]

    print(f"\n=== DETAILED REPORT FOR MOST RECENT 3 SCAN DATES: {recent_dates} ===")
    if recent_dates:
        # Fetch actual signals for these dates from signals_history and signals
        hist_signals_res = supabase.table("signals_history").select("*").in_("scan_date", recent_dates).execute()
        hist_signals = hist_signals_res.data or []
        
        # Also check if target_date has signals in the active table
        active_signals_res = supabase.table("signals").select("*").in_("scan_date", recent_dates).execute()
        active_signals = active_signals_res.data or []
        
        # Merge them (avoid duplicates by scan_date, ticker)
        all_recent = {}
        for s in hist_signals + active_signals:
            key = (s["scan_date"], s["ticker"].upper())
            all_recent[key] = s
            
        for (date, ticker), s in sorted(all_recent.items(), key=lambda x: (x[0][0], x[0][1]), reverse=True):
            score = s.get("composite_score")
            if score is None:
                score = s.get("score")
            score_val = float(score) if score is not None else 0.0
            tier = s.get("tier_label")
            is_fb = s.get("is_fallback", False)
            rsi = s.get("current_rsi")
            adx = s.get("adx_value")
            vol = s.get("volume_ratio")
            rr = s.get("risk_reward")

            print(f"Date: {date} | Ticker: {ticker:<6} | Score: {score_val:.2f} | Tier: {tier:<11} | Fallback: {is_fb} | "
                  f"RSI={rsi} | ADX={adx} | Volume={vol}x | R/R={rr}x")
    else:
        print("No historical signals found to inspect details.")

    # 6. Gate failure breakdown with percentages for the most recent scan
    if scan_logs:
        latest_scan = scan_logs[0]
        date = latest_scan.get("scan_date")
        scanned = latest_scan.get("tickers_scanned", 0)
        
        # failed gates
        gates = [
            ("Trend Alignment Gate", latest_scan.get("failed_trend_gate", 0)),
            ("RSI Pullback-Recovery Gate", latest_scan.get("failed_rsi_gate", 0)),
            ("ADX Trend Strength Gate", latest_scan.get("failed_adx_gate", 0)),
            ("MACD Momentum Gate", latest_scan.get("failed_macd_gate", 0)),
            ("Volume Confirmation Gate", latest_scan.get("failed_volume_gate", 0)),
            ("Risk/Reward Ratio Gate", latest_scan.get("failed_rr_gate", 0)),
            ("Max Risk % Gate", latest_scan.get("failed_maxrisk_gate", 0)),
            ("Trades Floor Gate", latest_scan.get("failed_trades_gate", 0)),
        ]

        print(f"\n=== GATE FAILURE BREAKDOWN FOR SCAN DATE {date} (Scanned: {scanned}) ===")
        if scanned > 0:
            for gate_name, count in gates:
                pct = (count / scanned) * 100.0
                print(f"  {gate_name:<30} : {count:<4} failed ({pct:.1f}% of total universe)")
        else:
            print("No tickers scanned in the latest run.")
    else:
        print("\nNo scan logs available for breakdown.")

if __name__ == "__main__":
    run_diagnostics()
