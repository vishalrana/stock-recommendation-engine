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
from supabase import create_client, Client


# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)


def get_client() -> Client:
    """
    Create and return a Supabase client using the service_role key.

    Raises:
        SystemExit: If required environment variables are missing.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not url:
        print("ERROR: SUPABASE_URL is not set.")
        print("Copy .env.example to .env and fill in your Supabase project URL.")
        sys.exit(1)

    if not key:
        print("ERROR: SUPABASE_SERVICE_KEY is not set.")
        print("Copy .env.example to .env and fill in your service_role key.")
        print("Find it at: Supabase Dashboard > Settings > API > service_role")
        sys.exit(1)

    return create_client(url, key)

# Convenience instance for direct import
try:
    supabase = get_client()
except Exception:
    supabase = None


def update_signals_status(ticker, status, exit_price, sell_signal, sell_signal_reason=None, removal_reason=None, removal_note=None, signal_id=None):
    from datetime import datetime
    if not supabase:
        return
    today = datetime.now().date().isoformat()
    now_ts = datetime.now().isoformat()
    update_data = {
        'status': status,
        'exit_price': exit_price,
        'sell_price': exit_price,
        'sell_signal': True,
        'sell_signal_reason': sell_signal_reason or sell_signal,
        'sell_signal_date': today,
        'exit_date': today,
        'price': exit_price,
    }
    if removal_reason:
        update_data['removal_reason'] = removal_reason
    if removal_note:
        update_data['removal_note'] = removal_note
    if status == 'manually_removed':
        update_data['removed_at'] = now_ts

    def _execute_update(data):
        if signal_id:
            return supabase.table('signals').update(data).eq('id', signal_id).execute()
        elif status == 'manually_removed':
            import logging
            logging.getLogger(__name__).error(f"[MANUAL REMOVAL] Refusing unsafe ticker-only update for {ticker}. Exact signal_id is required.")
            return None
        else:
            return supabase.table('signals').update(data).eq('ticker', ticker).in_('status', ['open', 'pending']).execute()

    try:
        _execute_update(update_data)
    except Exception as e:
        new_cols = ['removal_reason', 'removal_note', 'removed_at']
        if any(col in str(e) for col in new_cols):
            for col in new_cols:
                update_data.pop(col, None)
            _execute_update(update_data)
        else:
            raise e


def update_signals_price(ticker, current_price, signal_id=None):
    if not supabase:
        return
    q = supabase.table('signals').update({
        'price': current_price
    })
    if signal_id:
        q.eq('id', signal_id).execute()
    else:
        q.eq('ticker', ticker).in_('status', ['open', 'pending']).execute()


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


def update_history_outcome(ticker, status, exit_price, sell_signal=True, allocated_dollars=None, max_shares=None, removal_reason=None, removal_note=None, history_id=None, signal_id=None, scan_date=None, sell_signal_reason=None):
    if not supabase:
        return
    from datetime import datetime
    outcome_map = {
        'stop_loss': 'stopped',
        'take_profit_1': 'hit_t1',
        'take_profit_2': 'hit_t2',
        'take_profit_3': 'hit_t3',
        'invalidated': 'invalidated',
        'manually_removed': 'manually_removed',
    }
    outcome = outcome_map.get(status, status)
    
    # Exact record lookup priority:
    # 1. history_id (exact primary key in signals_history)
    # 2. signal_id (exact foreign key if present)
    # 3. (ticker, scan_date) (unique constraint in signals_history)
    # 4. fallback: ticker + outcome='open' (only if no instance identifier available)
    record = None
    target_filter_type = 'fallback'
    target_filter_val = None

    if history_id is not None:
        try:
            h_res = supabase.table('signals_history').select('*').eq('id', history_id).execute()
            if h_res.data:
                record = h_res.data[0]
                target_filter_type = 'history_id'
                target_filter_val = history_id
        except Exception:
            pass

    if record is None and signal_id is not None:
        try:
            s_res = supabase.table('signals_history').select('*').eq('signal_id', signal_id).execute()
            if s_res.data:
                record = s_res.data[0]
                target_filter_type = 'signal_id'
                target_filter_val = signal_id
        except Exception:
            pass
        if record is None and scan_date is None:
            try:
                sig_lookup = supabase.table('signals').select('scan_date, ticker').eq('id', signal_id).execute()
                if sig_lookup.data:
                    scan_date = sig_lookup.data[0].get('scan_date')
                    if not ticker:
                        ticker = sig_lookup.data[0].get('ticker')
            except Exception:
                pass

    if record is None and scan_date is not None and ticker:
        try:
            d_res = supabase.table('signals_history').select('*').eq('ticker', ticker).eq('scan_date', scan_date).execute()
            if d_res.data:
                record = d_res.data[0]
                target_filter_type = 'scan_date'
                target_filter_val = scan_date
        except Exception:
            pass

    if record is None and ticker:
        if status == 'manually_removed':
            import logging
            logging.getLogger(__name__).error(f"[MANUAL REMOVAL] Refusing unsafe ticker-only fallback for {ticker}. Exact instance identifier (history_id, signal_id, or scan_date) is required.")
            return None
        try:
            res = supabase.table('signals_history').select('*').eq('ticker', ticker).eq('outcome', 'open').execute()
            if res.data:
                record = res.data[0]
                target_filter_type = 'fallback'
        except Exception:
            pass

    if record:
        entry_price = float(record.get('entry_price') or 0)
        scan_date_str = record.get('scan_date')
        
        return_pct = None
        if entry_price > 0 and exit_price is not None:
            return_pct = round(((exit_price - entry_price) / entry_price) * 100, 2)
            
        holding_days = None
        if scan_date_str:
            try:
                scan_dt = datetime.strptime(str(scan_date_str)[:10], '%Y-%m-%d').date()
                holding_days = (datetime.now().date() - scan_dt).days
            except Exception:
                pass
                
        update_data = {
            'outcome': outcome,
            'exit_price': exit_price,
            'outcome_date': datetime.now().date().isoformat(),
            'outcome_return_pct': return_pct,
            'outcome_holding_days': holding_days
        }
        if sell_signal_reason:
            update_data['sell_signal_reason'] = sell_signal_reason
        if allocated_dollars is not None:
            update_data['allocated_dollars'] = allocated_dollars
        if max_shares is not None:
            update_data['max_shares'] = max_shares
        if removal_reason:
            update_data['removal_reason'] = removal_reason
        if removal_note:
            update_data['removal_note'] = removal_note
        if status == 'manually_removed':
            update_data['removed_at'] = datetime.now().isoformat()

        def _execute_history_update(data):
            rec_id = record.get('id')
            if rec_id is not None:
                return supabase.table('signals_history').update(data).eq('id', rec_id).execute()
            elif target_filter_type == 'scan_date':
                return supabase.table('signals_history').update(data).eq('ticker', ticker).eq('scan_date', target_filter_val).execute()
            elif target_filter_type == 'signal_id':
                return supabase.table('signals_history').update(data).eq('signal_id', target_filter_val).execute()
            elif target_filter_type == 'fallback':
                if status == 'manually_removed':
                    return None
                return supabase.table('signals_history').update(data).eq('ticker', ticker).eq('outcome', 'open').execute()
            else:
                return None

        try:
            _execute_history_update(update_data)
        except Exception as e:
            new_cols = ['removal_reason', 'removal_note', 'removed_at', 'signal_id']
            if any(col in str(e) for col in new_cols):
                for col in new_cols:
                    update_data.pop(col, None)
                _execute_history_update(update_data)
            else:
                raise e


def get_latest_price(ticker):
    bar = get_latest_bar(ticker)
    if bar and "close" in bar:
        return bar["close"]
    return None


def get_latest_bar(ticker):
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
            
            c = float(df[close_col].iloc[-1])
            h = float(df[high_col].iloc[-1]) if high_col in df.columns else c
            l = float(df[low_col].iloc[-1]) if low_col in df.columns else c
            
            atr = c * 0.02
            if len(df) >= 14 and high_col in df.columns and low_col in df.columns:
                tr = (df[high_col] - df[low_col]).tail(14).mean()
                if tr > 0:
                    atr = float(tr)
            return {"close": c, "high": h, "low": l, "atr": atr}

        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        history = ticker_obj.history(period="14d")
        if not history.empty:
            c = float(history['Close'].iloc[-1])
            h = float(history['High'].iloc[-1])
            l = float(history['Low'].iloc[-1])
            tr = (history['High'] - history['Low']).mean()
            atr = float(tr) if tr > 0 else c * 0.02
            return {"close": c, "high": h, "low": l, "atr": atr}
    except Exception as e:
        print(f"Error fetching latest bar for {ticker}: {e}")
        
    return None

