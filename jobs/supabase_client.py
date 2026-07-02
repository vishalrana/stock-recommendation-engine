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


def update_signals_status(ticker, status, exit_price, sell_signal, sell_signal_reason=None):
    from datetime import datetime
    if not supabase:
        return
    today = datetime.now().date().isoformat()
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
    supabase.table('signals').update(update_data).eq('ticker', ticker).eq('status', 'open').execute()


def update_signals_price(ticker, current_price):
    if not supabase:
        return
    supabase.table('signals').update({
        'price': current_price
    }).eq('ticker', ticker).eq('status', 'open').execute()


def update_portfolio_realized_pnl(pnl_dollars):
    if not supabase:
        return
    try:
        from datetime import datetime
        res = supabase.table("portfolio_state").select("*").order("created_at", desc=True).limit(1).execute()
        portfolio_value = 10000.0
        peak_value = 10000.0
        if res.data:
            state = res.data[0]
            portfolio_value = float(state.get("portfolio_value") or 10000.0)
            peak_value = float(state.get("peak_value") or 10000.0)
            
        new_portfolio_value = portfolio_value + pnl_dollars
        new_peak_value = max(peak_value, new_portfolio_value)
        new_dd = ((new_peak_value - new_portfolio_value) / new_peak_value) * 100.0 if new_peak_value > 0 else 0.0
        
        today_str = datetime.now().date().isoformat()
        supabase.table("portfolio_state").insert({
            "date": today_str,
            "portfolio_value": new_portfolio_value,
            "peak_value": new_peak_value,
            "current_drawdown_pct": new_dd
        }).execute()
        
        print(f"[PORTFOLIO UPDATE] Realized PNL: ${pnl_dollars:.2f}, New Value: ${new_portfolio_value:.2f}")
    except Exception as e:
        print(f"[PORTFOLIO UPDATE] Failed to update portfolio state realized PNL: {e}")


def update_history_outcome(ticker, status, exit_price, sell_signal, allocated_dollars=None, max_shares=None):
    if not supabase:
        return
    from datetime import datetime
    outcome_map = {
        'stop_loss': 'stopped',
        'take_profit_1': 'hit_t1',
        'take_profit_2': 'hit_t2',
        'take_profit_3': 'hit_t3',
    }
    outcome = outcome_map.get(status, status)
    
    res = supabase.table('signals_history').select('*').eq('ticker', ticker).eq('outcome', 'open').execute()
    if res.data:
        record = res.data[0]
        entry_price = float(record.get('entry_price') or 0)
        scan_date_str = record.get('scan_date')
        
        return_pct = None
        if entry_price > 0 and exit_price is not None:
            return_pct = ((exit_price - entry_price) / entry_price) * 100
            
        holding_days = None
        if scan_date_str:
            try:
                scan_date = datetime.strptime(scan_date_str, '%Y-%m-%d').date()
                holding_days = (datetime.now().date() - scan_date).days
            except Exception:
                pass
                
        update_data = {
            'outcome': outcome,
            'exit_price': exit_price,
            'outcome_date': datetime.now().date().isoformat(),
            'outcome_return_pct': return_pct,
            'outcome_holding_days': holding_days
        }
        if allocated_dollars is not None:
            update_data['allocated_dollars'] = allocated_dollars
        if max_shares is not None:
            update_data['max_shares'] = max_shares

        supabase.table('signals_history').update(update_data).eq('ticker', ticker).eq('outcome', 'open').execute()


def get_latest_price(ticker):
    ticker = ticker.upper()
    try:
        from src.data.cache_manager import get_cache_manager
        cm = get_cache_manager()
        # Check preloaded history cache
        if ticker in cm._history_cache and not cm._history_cache[ticker].empty:
            df = cm._history_cache[ticker]
            close_col = "CLOSE" if "CLOSE" in df.columns else "Close"
            return float(df[close_col].iloc[-1])
        # Fallback to reading disk/download
        import datetime
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=10)
        df = cm.get_ticker_history(ticker, start_date.isoformat(), end_date.isoformat())
        if df is not None and not df.empty:
            close_col = "CLOSE" if "CLOSE" in df.columns else "Close"
            return float(df[close_col].iloc[-1])
        # Direct yfinance fallback
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        fast_info = ticker_obj.fast_info
        if fast_info and 'lastPrice' in fast_info:
            return float(fast_info['lastPrice'])
        history = ticker_obj.history(period="1d")
        if not history.empty:
            return float(history['Close'].iloc[-1])
    except Exception as e:
        print(f"Error fetching latest price for {ticker}: {e}")
    return None

