import os
import sys
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Ensure project root is on sys.path and load environment variables
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

load_dotenv(_PROJECT_ROOT / ".env")

from jobs.supabase_client import get_client, update_history_outcome

TIINGO_API_KEY = os.environ.get('TIINGO_API_KEY')

def get_live_price(ticker):
    if not TIINGO_API_KEY:
        print("Warning: TIINGO_API_KEY environment variable is not set. Falling back to yfinance.")
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            history = t.history(period="1d")
            if not history.empty:
                return float(history['Close'].iloc[-1])
            fast_info = t.fast_info
            if fast_info and 'lastPrice' in fast_info:
                return float(fast_info['lastPrice'])
        except Exception as e:
            print(f"yfinance fallback failed for {ticker}: {e}")
        return None

    url = f"https://api.tiingo.com/iex/{ticker}"
    headers = {'Authorization': f'Token {TIINGO_API_KEY}'}
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                row = data[0]
                for key in ['tngoLast', 'last', 'close', 'open']:
                    if key in row and row[key] is not None:
                        return float(row[key])
        else:
            print(f"Tiingo API returned status {resp.status_code} for {ticker}: {resp.text}")
    except Exception as e:
        print(f"Error fetching live price from Tiingo for {ticker}: {e}")
    return None

def update_portfolio_drawdown(supabase, return_pct, position_sizing_str):
    try:
        res = supabase.table("portfolio_state").select("*").order("created_at", desc=True).limit(1).execute()
        portfolio_value = 10000.0
        peak_value = 10000.0
        if res.data:
            state = res.data[0]
            portfolio_value = float(state["portfolio_value"])
            peak_value = float(state["peak_value"])
            
        allocation_pct = 0.05  # Default to 5%
        if position_sizing_str and "Kelly:" in position_sizing_str:
            try:
                allocation_pct = float(position_sizing_str.replace("Kelly:", "").replace("%", "").strip()) / 100.0
            except Exception:
                pass
                
        allocation_dollars = allocation_pct * portfolio_value
        pnl_dollars = allocation_dollars * (return_pct / 100.0)
        new_portfolio_value = portfolio_value + pnl_dollars
        new_peak_value = max(peak_value, new_portfolio_value)
        new_dd = (new_peak_value - new_portfolio_value) / new_peak_value * 100
        
        supabase.table("portfolio_state").insert({
            "date": datetime.today().date().isoformat(),
            "portfolio_value": new_portfolio_value,
            "peak_value": new_peak_value,
            "current_drawdown_pct": new_dd
        }).execute()
        print(f"[PORTFOLIO UPDATE] New value: ${new_portfolio_value:.2f}, Drawdown: {new_dd:.2f}%")
    except Exception as e:
        print(f"Failed to update portfolio state drawdown: {e}")

def monitor_open_positions():
    supabase = get_client()
    try:
        open_positions = supabase.table('signals')\
            .select('*').eq('status', 'open').execute().data
    except Exception as e:
        if 'column signals.status does not exist' in str(e) or '42703' in str(e):
            print("\n" + "="*80)
            print("ERROR: Database columns are missing.")
            print("Please execute the SQL migration script located at:")
            print("  supabase/migration_gtm_complete.sql")
            print("in your Supabase SQL Editor, then rerun this script.")
            print("="*80 + "\n")
            return
        else:
            print(f"Failed to query open positions: {e}")
            return
    
    if not open_positions:
        print("[MONITOR] No active open positions to monitor.")
        return
        
    print(f"[MONITOR] Monitoring {len(open_positions)} open positions...")
    
    for pos in open_positions:
        ticker = pos['ticker']
        current_price = get_live_price(ticker)
        if current_price is None:
            print(f"[MONITOR] Could not retrieve price for {ticker}. Skipping.")
            continue
            
        print(f"[MONITOR] Ticker: {ticker} | Entry: {pos['entry_price']} | Current: {current_price:.2f}")
        
        entry_price = float(pos['entry_price'])
        stop_loss = float(pos['stop_loss'])
        target_1 = float(pos['target_1'])
        target_2 = float(pos['target_2'])
        target_3 = float(pos['target_3'])
        
        sell_triggered = False
        status = 'open'
        reason = ""
        
        # Priority checks: target_1, target_2, target_3, and stop_loss
        if current_price >= target_3:
            sell_triggered = True
            reason = "Target 3 hit – full exit"
            status = 'closed'
        elif current_price >= target_2:
            sell_triggered = True
            reason = "Target 2 hit – sell 30%"
            status = 'open'
        elif current_price >= target_1:
            sell_triggered = True
            reason = "Target 1 hit – sell 50%"
            status = 'open'
        elif current_price <= stop_loss:
            sell_triggered = True
            reason = "Stop loss hit"
            status = 'closed'
            
        if sell_triggered:
            print(f"[MONITOR ALERT] Triggered for {ticker}: {reason} at {current_price:.2f}")
            
            # 1. Update signals table
            update_payload = {
                'sell_signal': True,
                'sell_signal_reason': reason,
                'sell_price': current_price,
                'price': current_price,
                'status': status
            }
            if status == 'closed':
                update_payload['exit_date'] = datetime.today().date().isoformat()
                
            supabase.table('signals').update(update_payload).eq('ticker', ticker).eq('status', 'open').execute()
            
            # 2. Archive to signals_history if closed
            if status == 'closed':
                outcome = 'take_profit' if 'Target' in reason else 'stop_loss'
                # Let update_history_outcome handle calculation & record update
                update_history_outcome(ticker, outcome, current_price, reason)
                
                # 3. Update portfolio state drawdown
                return_pct = ((current_price - entry_price) / entry_price) * 100
                update_portfolio_drawdown(supabase, return_pct, pos.get('position_sizing'))
        else:
            # Just update the current price
            supabase.table('signals').update({
                'price': current_price
            }).eq('ticker', ticker).eq('status', 'open').execute()

if __name__ == '__main__':
    monitor_open_positions()
