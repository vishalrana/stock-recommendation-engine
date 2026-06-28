"""
validate_ranking.py
Runs after nightly signal generation.
For every signals_history row where outcome='open' and scan_date is >= 10 trading days ago,
fetch price history and determine whether price hit T1, T2, T3, stop, or expired.
Updates signals_history with outcome, outcome_return_pct, outcome_date, outcome_holding_days.
"""

import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import yfinance as yf
from datetime import date, timedelta
from jobs.supabase_client import supabase

EVALUATION_TRADING_DAYS = 10  # evaluate after 10 trading days
EXPIRY_TRADING_DAYS = 20      # mark expired after 20 trading days with no hit

def get_trading_days_ago(n: int) -> date:
    """Return the calendar date that is approximately n trading days in the past."""
    # Use pandas business day offset as approximation
    return (pd.Timestamp.today() - pd.offsets.BDay(n)).date()

def evaluate_signal(row: dict) -> dict | None:
    """
    Fetch price history for a signal and determine its outcome.
    Returns updated fields dict or None if price data unavailable.
    """
    ticker = row['ticker']
    scan_date = pd.Timestamp(row['scan_date']).date()
    entry_price = float(row['entry_price'])
    stop_loss = float(row['stop_loss'])
    target_1 = float(row['target_1_price']) if row.get('target_1_price') else None
    target_2 = float(row['target_2_price']) if row.get('target_2_price') else None
    target_3 = float(row['target_3_price']) if row.get('target_3_price') else None

    if not target_1:
        return None

    # Fetch daily OHLCV from scan_date + 1 forward
    start = scan_date + timedelta(days=1)
    end = date.today()

    try:
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty or len(df) < 1:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = df.columns.str.title()
    except Exception:
        return None

    # Walk day by day to find first event (stop or target hit)
    for i, (dt, row_data) in enumerate(df.iterrows()):
        
        day_high = float(row_data['High'])
        day_low = float(row_data['Low'])
        day_close = float(row_data['Close'])
        holding_days = i + 1

        # Check stop first (stop takes priority on same day)
        if day_low <= stop_loss:
            # Approximate exit at stop (could be worse due to gap, use stop as estimate)
            exit_price = stop_loss
            return_pct = round((exit_price - entry_price) / entry_price * 100, 4)
            return {
                'outcome': 'stopped',
                'outcome_return_pct': return_pct,
                'outcome_date': dt.date(),
                'outcome_holding_days': holding_days
            }

        # Check targets (highest first — if T3 hit same day as T1, credit T3)
        if target_3 and day_high >= target_3:
            return_pct = round((target_3 - entry_price) / entry_price * 100, 4)
            return {
                'outcome': 'hit_t3',
                'outcome_return_pct': return_pct,
                'outcome_date': dt.date(),
                'outcome_holding_days': holding_days
            }
        if target_2 and day_high >= target_2:
            return_pct = round((target_2 - entry_price) / entry_price * 100, 4)
            return {
                'outcome': 'hit_t2',
                'outcome_return_pct': return_pct,
                'outcome_date': dt.date(),
                'outcome_holding_days': holding_days
            }
        if day_high >= target_1:
            return_pct = round((target_1 - entry_price) / entry_price * 100, 4)
            return {
                'outcome': 'hit_t1',
                'outcome_return_pct': return_pct,
                'outcome_date': dt.date(),
                'outcome_holding_days': holding_days
            }

        # Check expiry
        if holding_days >= EXPIRY_TRADING_DAYS:
            return_pct = round((day_close - entry_price) / entry_price * 100, 4)
            return {
                'outcome': 'expired',
                'outcome_return_pct': return_pct,
                'outcome_date': dt.date(),
                'outcome_holding_days': holding_days
            }

    return None  # Still open, not enough bars yet


def run_validation():
    cutoff_date = get_trading_days_ago(EVALUATION_TRADING_DAYS)

    # Fetch all open signals old enough to evaluate
    response = supabase.table('signals_history') \
        .select('id, ticker, scan_date, entry_price, stop_loss, target_1_price, target_2_price, target_3_price') \
        .eq('outcome', 'open') \
        .lte('scan_date', str(cutoff_date)) \
        .execute()

    rows = response.data
    if not rows:
        print("No open signals ready for evaluation.")
        return

    print(f"Evaluating {len(rows)} open signals...")
    updated = 0
    skipped = 0

    for row in rows:
        result = evaluate_signal(row)
        if result:
            supabase.table('signals_history') \
                .update(result) \
                .eq('id', row['id']) \
                .execute()
            print(f"  {row['ticker']} ({row['scan_date']}): {result['outcome']} | {result['outcome_return_pct']}% in {result['outcome_holding_days']} days")
            updated += 1
        else:
            skipped += 1

    print(f"Validation complete. Updated: {updated}, Skipped (insufficient data): {skipped}")


if __name__ == '__main__':
    run_validation()
