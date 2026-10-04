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
    target_1 = float(row['target_1']) if row.get('target_1') else None
    target_2 = float(row['target_2']) if row.get('target_2') else None
    target_3 = float(row['target_3']) if row.get('target_3') else None

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
        df = df.dropna(subset=['High', 'Low', 'Close'])
        if df.empty or len(df) < 1:
            return None
    except Exception:
        return None

    from src.outcome.outcome_calculator import evaluate_signal_outcome, SAME_DAY_AMBIGUITY_POLICY

    res = evaluate_signal_outcome(
        df=df,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
        max_holding_days=EXPIRY_TRADING_DAYS,
        ambiguity_policy=SAME_DAY_AMBIGUITY_POLICY,
    )
    if res:
        return {
            'outcome': res['outcome'],
            'outcome_return_pct': res['outcome_return_pct'],
            'outcome_date': res['outcome_date'],
            'outcome_holding_days': res['outcome_holding_days'],
        }
    return None



def run_validation():
    cutoff_date = get_trading_days_ago(EVALUATION_TRADING_DAYS)

    # Fetch all open signals old enough to evaluate
    response = supabase.table('signals_history') \
        .select('id, ticker, scan_date, entry_price, stop_loss, target_1, target_2, target_3') \
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
            if hasattr(result.get('outcome_date'), 'isoformat'):
                result['outcome_date'] = result['outcome_date'].isoformat()
            elif isinstance(result.get('outcome_date'), date):
                result['outcome_date'] = str(result['outcome_date'])
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
