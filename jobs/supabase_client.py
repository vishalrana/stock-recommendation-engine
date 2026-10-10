"""
Supabase Client
===============
Provides a configured Supabase client for the jobs package.

Usage:
    from jobs.supabase_client import get_client
    client = get_client()
    client.table("scan_log").insert({...}).execute()

Environment variables required:
    SUPABASE_URL         - Project URL from Supabase dashboard
    SUPABASE_SERVICE_KEY - service_role key (secret, bypasses RLS)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import pandas as pd
from supabase import create_client, Client


# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)


class SupabaseConfigError(RuntimeError):
    """Supabase credentials are not configured (e.g. CI without secrets)."""


def get_client() -> Client:
    """
    Create and return a Supabase client using the service_role key.

    Raises:
        SupabaseConfigError: If required environment variables are missing. (Raising instead of
        sys.exit lets importers and tests run without credentials; entry points that need the
        database still fail with a non-zero exit.)
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not url:
        raise SupabaseConfigError(
            "SUPABASE_URL is not set. Copy .env.example to .env and fill in your Supabase project URL."
        )

    if not key:
        raise SupabaseConfigError(
            "SUPABASE_SERVICE_KEY is not set. Copy .env.example to .env and fill in your service_role key "
            "(Supabase Dashboard > Settings > API > service_role)."
        )

    return create_client(url, key)

# Convenience instance for direct import
try:
    supabase = get_client()
except Exception:
    supabase = None


def update_signals_status(ticker, status, exit_price, sell_signal, sell_signal_reason=None, removal_reason=None, removal_note=None, signal_id=None, exit_date=None, last_price=None):
    from datetime import datetime
    if not supabase:
        return None
    today = datetime.now().date().isoformat()
    now_ts = datetime.now().isoformat()
    update_data = {
        'status': status,
        'exit_price': exit_price,
        'sell_price': exit_price,
        'sell_signal': True,
        'sell_signal_reason': sell_signal_reason or (sell_signal if isinstance(sell_signal, str) else None),
        'exit_date': exit_date or today,
        'price': last_price if last_price is not None else exit_price,
    }
    if removal_reason:
        update_data['removal_reason'] = removal_reason
    if removal_note:
        update_data['removal_note'] = removal_note
    if status == 'manually_removed':
        update_data['removed_at'] = now_ts

    if signal_id:
        return supabase.table('signals').update(update_data).eq('id', signal_id).execute()
    else:
        import logging
        logging.getLogger(__name__).error(
            f"[MUTATION REFUSED] Refusing unsafe mutation for ticker={ticker} status={status}. Exact signal_id is required."
        )
        return None


def update_signals_price(ticker, current_price, signal_id=None):
    if not supabase:
        return None
    if not signal_id:
        import logging
        logging.getLogger(__name__).error(
            f"[PRICE UPDATE REFUSED] Refusing unsafe price update for ticker={ticker}. Exact signal_id is required."
        )
        return None
    q = supabase.table('signals').update({
        'price': current_price
    })
    return q.eq('id', signal_id).execute()


def update_portfolio_realized_pnl(pnl_dollars):
    """[DEPRECATED] Decommissioned. Portfolio state tracking is not part of recommendation engine."""
    import warnings
    warnings.warn("update_portfolio_realized_pnl is deprecated and decommissioned", DeprecationWarning, stacklevel=2)
    return


def execute_position_exit(signal_id, exit_price, outcome, reason, split_fraction=1, live_price=None):
    """[DEPRECATED] Decommissioned. Automated position exits are not part of recommendation engine."""
    import warnings
    warnings.warn("execute_position_exit is deprecated and decommissioned", DeprecationWarning, stacklevel=2)
    return None


def update_history_outcome(ticker, status, exit_price, sell_signal=True, allocated_dollars=None, max_shares=None, removal_reason=None, removal_note=None, history_id=None, signal_id=None, scan_date=None, sell_signal_reason=None, return_pct=None, outcome_date=None, holding_days=None, entry_fill_price=None):
    """
    Record a terminal outcome on the exact signals_history instance.

    When the caller replayed the bar path (return_pct / outcome_date / holding_days given),
    those values are authoritative. Otherwise the return is reconstructed from the outcome
    label with the static scale-out model (used for manual removals).
    """
    if not supabase:
        return None
    from datetime import datetime
    import logging
    logger = logging.getLogger(__name__)

    outcome_map = {
        'stop_loss': 'stopped',
        'take_profit_1': 'hit_t1',
        'take_profit_2': 'hit_t2',
        'take_profit_3': 'hit_t3',
        'invalidated': 'invalidated',
        'manually_removed': 'manually_removed',
    }
    outcome = outcome_map.get(status, status)
    
    # Exact record lookup:
    # 1. history_id (exact primary key in signals_history)
    # 2. signal_id (exact recommendation instance identifier)
    record = None

    if history_id is not None:
        try:
            h_res = supabase.table('signals_history').select('*').eq('id', history_id).execute()
            if h_res.data:
                record = h_res.data[0]
        except Exception as e:
            logger.warning(f"Error querying signals_history by history_id={history_id}: {e}")

    if record is None and signal_id is not None:
        try:
            s_res = supabase.table('signals_history').select('*').eq('signal_id', signal_id).execute()
            if s_res.data:
                record = s_res.data[0]
        except Exception as e:
            logger.warning(f"Error querying signals_history by signal_id={signal_id}: {e}")

    if record is None:
        logger.error(
            f"[HISTORY OUTCOME] Refusing unsafe update for {ticker}: exact instance identifier "
            f"(history_id={history_id} or signal_id={signal_id}) is required."
        )
        return None

    entry_price = float(entry_fill_price or record.get('entry_fill_price') or record.get('entry_price') or 0)
    scan_date_str = record.get('scan_date')

    if return_pct is None and entry_price > 0:
        t1 = float(record.get('target_1') or 0) if record.get('target_1') else None
        t2 = float(record.get('target_2') or 0) if record.get('target_2') else None
        t3 = float(record.get('target_3') or 0) if record.get('target_3') else None
        sl = float(record.get('stop_loss') or 0) if record.get('stop_loss') else entry_price * 0.93

        from src.outcome.outcome_calculator import calculate_static_scale_out_return
        return_pct = calculate_static_scale_out_return(
            entry_price=entry_price,
            stop_loss=sl,
            target_1=t1,
            target_2=t2,
            target_3=t3,
            outcome=outcome,
            exit_price=exit_price,
        )
        
    if holding_days is None and scan_date_str:
        # Fallback only (manual removals): trading days elapsed since the signal date.
        try:
            import numpy as np
            scan_dt = datetime.strptime(str(scan_date_str)[:10], '%Y-%m-%d').date()
            holding_days = max(0, int(np.busday_count(scan_dt, datetime.now().date())))
        except Exception:
            pass

    update_data = {
        'outcome': outcome,
        'exit_price': exit_price,
        'outcome_date': outcome_date or datetime.now().date().isoformat(),
        'outcome_return_pct': return_pct,
        'outcome_holding_days': holding_days
    }
    if entry_fill_price is not None:
        update_data['entry_fill_price'] = entry_fill_price
    if sell_signal_reason:
        update_data['sell_signal_reason'] = sell_signal_reason
    if removal_reason:
        update_data['removal_reason'] = removal_reason
    if removal_note:
        update_data['removal_note'] = removal_note
    if status == 'manually_removed':
        update_data['removed_at'] = datetime.now().isoformat()

    rec_id = record.get('id')
    try:
        return supabase.table('signals_history').update(update_data).eq('id', rec_id).execute()
    except Exception as e:
        if 'entry_fill_price' in update_data and ('42703' in str(e) or 'entry_fill_price' in str(e)):
            # Column not migrated yet: keep the outcome, drop only the new optional field.
            update_data.pop('entry_fill_price')
            return supabase.table('signals_history').update(update_data).eq('id', rec_id).execute()
        raise


def get_latest_price(ticker):
    bar = get_latest_bar(ticker)
    if bar and "close" in bar:
        return bar["close"]
    return None


def _bar_to_dict(df, close_col, high_col, low_col, open_col):
    c = float(df[close_col].iloc[-1])
    h = float(df[high_col].iloc[-1]) if high_col in df.columns else c
    l = float(df[low_col].iloc[-1]) if low_col in df.columns else c
    o = float(df[open_col].iloc[-1]) if open_col in df.columns else None
    idx = df.index[-1]
    bar_date = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
    atr = None
    if len(df) >= 14 and high_col in df.columns and low_col in df.columns:
        from src.indicators import calculate_atr
        atr_series = calculate_atr(df[high_col], df[low_col], df[close_col], 14)
        if not atr_series.empty and not pd.isna(atr_series.iloc[-1]):
            atr = float(atr_series.iloc[-1])
    bar = {"close": c, "high": h, "low": l, "atr": atr, "date": bar_date}
    if o is not None:
        bar["open"] = o
    return bar


def get_latest_bar(ticker):
    """Latest daily bar with its date (and open when available)."""
    ticker = ticker.upper()
    try:
        from src.data.cache_manager import get_cache_manager
        cm = get_cache_manager()
        df = None
        if ticker in cm._history_cache and not cm._history_cache[ticker].empty:
            df = cm._history_cache[ticker]
        else:
            import datetime
            end_date = datetime.date.today()
            start_date = end_date - datetime.timedelta(days=30)
            df = cm.get_ticker_history(ticker, start_date.isoformat(), end_date.isoformat())

        if df is not None and not df.empty:
            close_col = "CLOSE" if "CLOSE" in df.columns else "Close"
            high_col = "HIGH" if "HIGH" in df.columns else "High"
            low_col = "LOW" if "LOW" in df.columns else "Low"
            open_col = "OPEN" if "OPEN" in df.columns else "Open"
            return _bar_to_dict(df, close_col, high_col, low_col, open_col)

        import yfinance as yf
        history = yf.Ticker(ticker).history(period="30d")
        if not history.empty:
            return _bar_to_dict(history, "Close", "High", "Low", "Open")
    except Exception as e:
        print(f"Error fetching latest bar for {ticker}: {e}")

    return None


def get_bars_after(ticker, after_date, through_date=None):
    """
    All daily OHLC bars strictly after `after_date` (the signal date) up to and including
    `through_date`, oldest first, with uppercase OPEN/HIGH/LOW/CLOSE columns.
    Returns None when price history is unavailable, an empty DataFrame when no bar
    after the signal date exists yet.
    """
    import datetime
    ticker = ticker.upper()
    try:
        after_dt = datetime.date.fromisoformat(str(after_date)[:10])
        end_dt = datetime.date.fromisoformat(str(through_date)[:10]) if through_date else datetime.date.today()
        from src.data.cache_manager import get_cache_manager
        cm = get_cache_manager()
        df = cm.get_ticker_history(ticker, (after_dt + datetime.timedelta(days=1)).isoformat(), end_dt.isoformat())
        if df is None:
            return None
        df = df.copy()
        df.columns = [str(c).upper() for c in df.columns]
        if not {"HIGH", "LOW", "CLOSE"}.issubset(df.columns):
            return None
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        dates = df.index.date
        return df[(dates > after_dt) & (dates <= end_dt)]
    except Exception as e:
        print(f"Error fetching bars for {ticker}: {e}")
        return None

