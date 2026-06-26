#!/usr/bin/env python
"""
Validate Recommendations — Strategy 1.3 Rev B
==============================================
Standalone script to validate database recommendations and ranking logic.
"""
import os
import sys
import argparse
import pandas as pd
import numpy as np

# Set up sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from jobs.supabase_client import get_client
from src.ranker import SignalRanker

def main():
    parser = argparse.ArgumentParser(description="Validate Recommendations & DB Views")
    parser.add_argument("--date", type=str, help="Specific scan date to validate (defaults to most recent)")
    args = parser.parse_args()

    supabase = get_client()
    
    # 1. Find the scan date to validate
    target_date = args.date
    if not target_date:
        res_date = supabase.table("signals").select("scan_date").order("scan_date", desc=True).limit(1).execute()
        if not res_date.data:
            print("ERROR: No signals found in signals table.")
            sys.exit(1)
        target_date = res_date.data[0]["scan_date"]

    print("=" * 80)
    print(f"VALIDATING STRATEGY 1.3 REV B RECOMMENDATIONS FOR SCAN DATE: {target_date}")
    print("=" * 80)

    # 2. Fetch signals for target_date
    signals_res = supabase.table("signals").select("*").eq("scan_date", target_date).execute()
    signals = signals_res.data or []
    if not signals:
        print(f"ERROR: No signals found in database for scan date {target_date}.")
        sys.exit(1)

    print(f"Fetched {len(signals)} signals from 'signals' table.")

    # 3. Fetch ticker_metrics for these tickers
    tickers = [s["ticker"].upper() for s in signals]
    metrics_res = supabase.table("ticker_metrics").select("*").in_("ticker", tickers).execute()
    metrics_map = {m["ticker"].upper(): m for m in (metrics_res.data or [])}
    print(f"Fetched {len(metrics_map)} matching rows from 'ticker_metrics'.")

    # 4. Fetch the scan_log row to get metadata (regime, signals_qualified)
    log_res = supabase.table("scan_log").select("*").eq("scan_date", target_date).execute()
    log_data = log_res.data[0] if log_res.data else {}
    regime = log_data.get("regime", "bull")
    signals_qualified = log_data.get("signals_qualified", len(signals))
    
    print(f"Scan Metadata: Regime={regime.upper()} | Signals Qualified={signals_qualified}")

    # Build local pool DataFrame from signals and join metrics
    pool_df = pd.DataFrame(signals)
    
    # Download DMA_50 on the fly for the target date to run composite scoring
    import yfinance as yf
    dma_50_map = {}
    print("\nFetching DMA_50 on the fly for tickers via yfinance...")
    for ticker in tickers:
        try:
            df_yf = yf.download(ticker, period="2y", progress=False)
            if not df_yf.empty:
                if isinstance(df_yf.columns, pd.MultiIndex):
                    df_yf.columns = df_yf.columns.get_level_values(0)
                df_yf.columns = df_yf.columns.str.upper()
                df_yf["DMA_50"] = df_yf["CLOSE"].rolling(window=50).mean()
                target_dt = pd.to_datetime(target_date)
                available_dates = df_yf.index[df_yf.index <= target_dt]
                if len(available_dates) > 0:
                    row_date = available_dates[-1]
                    dma_50_val = float(df_yf.loc[row_date, "DMA_50"].iloc[0]) if isinstance(df_yf.loc[row_date, "DMA_50"], pd.Series) else float(df_yf.loc[row_date, "DMA_50"])
                    dma_50_map[ticker] = dma_50_val
                    print(f"  {ticker}: DMA_50 on {row_date.date()} is {dma_50_val:.2f}")
        except Exception as e:
            print(f"  [WARNING] Failed to fetch yfinance data for {ticker}: {e}")

    pool_df["dma_50"] = pool_df["ticker"].apply(lambda t: dma_50_map.get(t.upper(), float(pool_df.loc[pool_df["ticker"]==t, "price"].iloc[0])))
    pool_df["win_rate"] = pool_df["ticker"].apply(lambda t: float(metrics_map.get(t.upper(), {}).get("win_rate", 0.0)))
    pool_df["expectancy_pct"] = pool_df["ticker"].apply(lambda t: float(metrics_map.get(t.upper(), {}).get("expectancy_pct", 0.0)))
    pool_df["total_trades"] = pool_df["ticker"].apply(lambda t: int(metrics_map.get(t.upper(), {}).get("total_trades", 0)))

    # Re-compute composite scores using codebase's SignalRanker
    print("\nRe-computing composite scores and tiers using SignalRanker...")
    ranker = SignalRanker()
    computed_df = ranker.composite_rank(pool_df, regime, top_n=len(pool_df))
    computed_map = {row["ticker"].upper(): row for _, row in computed_df.iterrows()}

    # Warnings for pool mismatch
    pool_mismatch = signals_qualified > len(signals)
    if pool_mismatch:
        print(f"\n[WARNING] Note: The original candidate pool had {signals_qualified} signals, but only {len(signals)} recommendations are stored.")
        print("Percentile-normalized scores (momentum, win rate) and expectancy Z-scores may differ from stored values.")

    mismatches = []
    validated_count = 0

    print("\n=== SIGNAL VALIDATION REPORT ===")
    for s in signals:
        ticker = s["ticker"].upper()
        stored_score = float(s["composite_score"] if s["composite_score"] is not None else s["score"])
        stored_tier = s["tier_label"]
        stored_fallback = bool(s.get("is_fallback", False))

        comp = computed_map.get(ticker)
        if comp is None:
            mismatches.append({
                "ticker": ticker,
                "rule": "Missing in re-computed signals",
                "details": f"Ticker {ticker} not found in computed DataFrame."
            })
            continue

        computed_score = float(comp["composite_score"])
        computed_tier = comp["tier_label"]
        computed_fallback = bool(comp.get("is_fallback", False))

        # Check score with absolute tolerance of 0.05
        score_diff = abs(stored_score - computed_score)
        score_ok = score_diff < 0.05

        # Check fallback cap: if fallback signal, tier is capped at Watch
        fallback_ok = True
        if stored_fallback:
            if stored_tier != "Watch":
                fallback_ok = False
                mismatches.append({
                    "ticker": ticker,
                    "rule": "Fallback Tier Cap Violation",
                    "details": f"Stored tier is '{stored_tier}', but fallback signals must be capped at 'Watch'."
                })
        
        # Check absolute floor: negative expectancy AND win_rate < 30% caps score at 45.0
        exp = float(comp["expectancy_pct"])
        wr = float(comp["win_rate"])
        floor_ok = True
        if exp < 0.0 and wr < 30.0:
            if stored_score > 45.0:
                floor_ok = False
                mismatches.append({
                    "ticker": ticker,
                    "rule": "Absolute Floor Violation",
                    "details": f"Expectancy={exp}%, WinRate={wr}%, but stored score={stored_score} (must be <= 45.0)."
                })

        # Check tier rules:
        # Tier 1 (Strong Buy): score >= 70, expectancy_pct > 0, win_rate >= 35%, total_trades >= 10
        # Tier 2 (Buy): score >= 58, expectancy_pct >= 0, win_rate >= 30%, total_trades >= 10
        # Tier 3 (Watch): score >= 45, expectancy_pct >= -1
        # Tier 4 (Speculative): score < 45 or doesn't meet Watch
        # Note: fallback signals are capped at Watch, so if is_fallback is True, computed_tier might be Buy or Strong Buy based on scores but stored_tier is capped at Watch.
        tier_ok = (stored_tier == computed_tier) or (stored_fallback and stored_tier == "Watch")
        
        if not pool_mismatch and not score_ok:
            mismatches.append({
                "ticker": ticker,
                "rule": "Score Mismatch",
                "details": f"Stored Score={stored_score}, Computed Score={computed_score} (Diff={score_diff:.4f})."
            })
        
        if not tier_ok:
            mismatches.append({
                "ticker": ticker,
                "rule": "Tier Mismatch",
                "details": f"Stored Tier='{stored_tier}', Computed Tier='{computed_tier}' (Fallback={stored_fallback})."
            })

        status_str = "OK" if (score_ok or pool_mismatch) and tier_ok and fallback_ok and floor_ok else "MISMATCH"
        if status_str == "OK":
            validated_count += 1

        print(f"Ticker: {ticker:<6} | Stored: Score={stored_score:.1f}, Tier={stored_tier:<11}, Fallback={stored_fallback} | "
              f"Computed: Score={computed_score:.1f}, Tier={computed_tier:<11}, Fallback={computed_fallback} | Status: {status_str}")
        dma_50_val = float(comp.get("dma_50", s["price"]))
        print(f"  Indicators: Price={s['price']:.2f}, 50DMA={dma_50_val:.2f}, RSI={s['current_rsi']:.1f}, "
              f"VolRatio={s['volume_ratio']:.2f}x, ADX={s['adx_value']:.1f}, MACDHist={s['macd_histogram']:.4f}, is_fallback={s['is_fallback']}")

    # 5. Validate recommendations view joins
    print("\n=== RECOMMENDATIONS VIEW VALIDATION ===")
    view_res = supabase.table("recommendations").select("*").eq("scan_date", target_date).execute()
    view_data = view_res.data or []
    print(f"Fetched {len(view_data)} rows from 'recommendations' view.")

    view_map = {v["ticker"].upper(): v for v in view_data}
    view_errors = 0

    for s in signals:
        ticker = s["ticker"].upper()
        v_row = view_map.get(ticker)
        if v_row is None:
            print(f"  [ERROR] Ticker {ticker} is in 'signals' table but missing from 'recommendations' view!")
            view_errors += 1
            mismatches.append({
                "ticker": ticker,
                "rule": "Missing in View",
                "details": "Ticker exists in signals table but is missing from recommendations view."
            })
            continue

        # Check joins
        m_row = metrics_map.get(ticker, {})
        v_wr = float(v_row.get("past_win_rate", 0.0))
        v_exp = float(v_row.get("expectancy_pct", 0.0))
        m_wr = float(m_row.get("win_rate", 0.0))
        m_exp = float(m_row.get("expectancy_pct", 0.0))

        if abs(v_wr - m_wr) > 0.01 or abs(v_exp - m_exp) > 0.01:
            print(f"  [ERROR] Metric mismatch for {ticker} in view! "
                  f"View: WinRate={v_wr}%, Exp={v_exp}%. Metrics: WinRate={m_wr}%, Exp={m_exp}%.")
            view_errors += 1
            mismatches.append({
                "ticker": ticker,
                "rule": "View Join Metric Mismatch",
                "details": f"View: WR={v_wr}%, Exp={v_exp}%. Metrics Table: WR={m_wr}%, Exp={m_exp}%."
            })
        
        # Check null values
        if v_row.get("past_win_rate") is None and m_row.get("win_rate") is not None:
            print(f"  [ERROR] Null past_win_rate in view for {ticker} but data exists in ticker_metrics.")
            view_errors += 1
            mismatches.append({
                "ticker": ticker,
                "rule": "Null View Field",
                "details": "past_win_rate is Null in recommendations view but exists in ticker_metrics."
            })

    if view_errors == 0:
        print("  All view rows joined correctly with ticker_metrics.")

    # Detailed mismatches output
    if mismatches:
        print("\n=== MISMATCH DETAILS ===")
        for m in mismatches:
            print(f"Ticker: {m['ticker']} | Rule: {m['rule']} | Details: {m['details']}")
    else:
        print("\nNo mismatches or discrepancies found!")

    # Summary line
    total_recs = len(signals)
    z_mismatches = len(mismatches)
    print(f"\n{validated_count} out of {total_recs} recommendations validated correctly. {z_mismatches} mismatches found.")

if __name__ == "__main__":
    main()
