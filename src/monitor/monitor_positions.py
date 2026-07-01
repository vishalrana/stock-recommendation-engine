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

def get_morning_open_price(ticker):
    today_str = datetime.today().strftime('%Y-%m-%d')
    
    if not TIINGO_API_KEY:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            history = t.history(period="1d")
            if not history.empty:
                bar_date = history.index[-1].strftime('%Y-%m-%d')
                if bar_date == today_str:
                    return float(history['Open'].iloc[-1])
        except Exception as e:
            print(f"yfinance open price check failed for {ticker}: {e}")
        return None

    url = f"https://api.tiingo.com/iex/{ticker}"
    headers = {'Authorization': f'Token {TIINGO_API_KEY}'}
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                row = data[0]
                ts = row.get('timestamp', '')
                if ts and ts[:10] == today_str:
                    if 'open' in row and row['open'] is not None:
                        return float(row['open'])
                    for key in ['last', 'tngoLast', 'close']:
                        if key in row and row[key] is not None:
                            return float(row[key])
    except Exception as e:
        print(f"Error fetching open price from Tiingo for {ticker}: {e}")
    
    # Fallback to yfinance if Tiingo does not have today's open yet
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        history = t.history(period="1d")
        if not history.empty:
            bar_date = history.index[-1].strftime('%Y-%m-%d')
            if bar_date == today_str:
                return float(history['Open'].iloc[-1])
    except Exception as e:
        print(f"yfinance fallback open price check failed for {ticker}: {e}")
        
    return None

def process_pending_signals(supabase):
    try:
        pending_signals = supabase.table('signals').select('*').eq('status', 'pending').execute().data
    except Exception as e:
        print(f"Failed to query pending signals: {e}")
        return

    if not pending_signals:
        return

    print(f"[PENDING GATE] Processing {len(pending_signals)} pending signals...")
    MAX_GAP_SLIPPAGE_PCT = 2.0

    for sig in pending_signals:
        ticker = sig['ticker']
        open_price = get_morning_open_price(ticker)
        if open_price is None:
            # ponytail: skip if today's market open price isn't live yet (pre-market or timezone delay)
            print(f"[PENDING GATE] Today's open price not available yet for {ticker}. Skipping.")
            continue

        entry_price = float(sig['entry_price'])
        stop_loss = float(sig['stop_loss'])
        
        # Calculate gap percent
        gap_pct = ((open_price - entry_price) / entry_price) * 100.0
        print(f"[PENDING GATE] Ticker: {ticker} | Entry Reference: {entry_price:.2f} | Open: {open_price:.2f} | Gap: {gap_pct:+.2f}%")

        scan_date = sig.get('scan_date')

        if gap_pct > MAX_GAP_SLIPPAGE_PCT:
            # Cancel: Gapped up too much
            reason = "Cancelled: Stock gapped up beyond acceptable risk tolerance."
            print(f"[PENDING GATE] {ticker} {reason} (Gap: {gap_pct:+.2f}%)")
            supabase.table('signals').update({
                'status': 'cancelled_gap_up',
                'sell_signal': True,
                'sell_signal_reason': reason,
                'price': open_price,
                'sell_price': open_price,
                'exit_date': datetime.today().date().isoformat()
            }).eq('id', sig['id']).execute()
            
            # Update history outcome
            try:
                supabase.table('signals_history').update({
                    'outcome': 'cancelled_gap_up',
                    'entry_price': open_price,
                    'exit_price': open_price
                }).eq('scan_date', scan_date).eq('ticker', ticker).execute()
            except Exception as he:
                print(f"[PENDING GATE] Failed to update history for {ticker}: {he}")
            
        elif open_price <= stop_loss:
            # Cancel: Gapped down below Stop Loss
            reason = "Cancelled: Stock gapped down below Stop Loss."
            print(f"[PENDING GATE] {ticker} {reason} (Open: {open_price:.2f} vs Stop: {stop_loss:.2f})")
            supabase.table('signals').update({
                'status': 'cancelled_gap_down',
                'sell_signal': True,
                'sell_signal_reason': reason,
                'price': open_price,
                'sell_price': open_price,
                'exit_date': datetime.today().date().isoformat()
            }).eq('id', sig['id']).execute()
            
            # Update history outcome
            try:
                supabase.table('signals_history').update({
                    'outcome': 'cancelled_gap_down',
                    'entry_price': open_price,
                    'exit_price': open_price
                }).eq('scan_date', scan_date).eq('ticker', ticker).execute()
            except Exception as he:
                print(f"[PENDING GATE] Failed to update history for {ticker}: {he}")
            
        else:
            # Passed! Transition to open
            print(f"[PENDING GATE] {ticker} passed the gate. Transitioning to open.")
            supabase.table('signals').update({
                'status': 'open',
                'entry_price': open_price, # Update entry price to actual execution/open price
                'price': open_price
            }).eq('id', sig['id']).execute()
            
            # Update history cost basis
            try:
                supabase.table('signals_history').update({
                    'entry_price': open_price
                }).eq('scan_date', scan_date).eq('ticker', ticker).execute()
            except Exception as he:
                print(f"[PENDING GATE] Failed to update history cost basis for {ticker}: {he}")

def check_stock_splits(supabase, open_positions):
    today_str = datetime.today().strftime('%Y-%m-%d')
    adjusted_any = False
    
    for i, pos in enumerate(open_positions):
        ticker = pos['ticker']
        
        # ponytail: use narrative column to tag adjusted entries to prevent multiple run double-adjustments
        narrative = pos.get('narrative') or ""
        tag = f"[SPLIT_ADJUSTED_{today_str}]"
        if tag in narrative:
            continue
            
        try:
            import yfinance as yf
            ticker_obj = yf.Ticker(ticker)
            splits = ticker_obj.splits
            if not splits.empty:
                for dt, ratio in splits.items():
                    if dt.strftime('%Y-%m-%d') == today_str:
                        split_ratio = float(ratio)
                        if split_ratio == 1.0 or split_ratio == 0.0:
                            continue
                            
                        # Split detected! Apply adjustments
                        print(f"[SPLIT DETECTED] {ticker} split ratio: {split_ratio:.2f} on {today_str}")
                        
                        old_entry = float(pos['entry_price'])
                        old_stop = float(pos['stop_loss'])
                        
                        new_entry = round(old_entry / split_ratio, 2)
                        new_stop = round(old_stop / split_ratio, 2)
                        
                        new_target_1 = round(float(pos['target_1']) / split_ratio, 2) if pos.get('target_1') is not None else None
                        new_target_2 = round(float(pos['target_2']) / split_ratio, 2) if pos.get('target_2') is not None else None
                        new_target_3 = round(float(pos['target_3']) / split_ratio, 2) if pos.get('target_3') is not None else None
                        
                        old_max_shares = pos.get('max_shares')
                        new_max_shares = round(float(old_max_shares) * split_ratio) if old_max_shares is not None else None
                        
                        new_narrative = f"{narrative} {tag}".strip()
                        
                        # Update Supabase signals table
                        update_data = {
                            'entry_price': new_entry,
                            'stop_loss': new_stop,
                            'narrative': new_narrative
                        }
                        if new_target_1 is not None: update_data['target_1'] = new_target_1
                        if new_target_2 is not None: update_data['target_2'] = new_target_2
                        if new_target_3 is not None: update_data['target_3'] = new_target_3
                        if new_max_shares is not None: update_data['max_shares'] = new_max_shares
                        
                        supabase.table('signals').update(update_data).eq('id', pos['id']).execute()
                        
                        # Update Supabase signals_history table
                        try:
                            history_update = {
                                'entry_price': new_entry,
                                'stop_loss': new_stop,
                                'narrative': new_narrative
                            }
                            if new_target_1 is not None: history_update['target_1'] = new_target_1
                            if new_target_2 is not None: history_update['target_2'] = new_target_2
                            if new_target_3 is not None: history_update['target_3'] = new_target_3
                            
                            supabase.table('signals_history').update(history_update).eq('scan_date', pos['scan_date']).eq('ticker', ticker).execute()
                        except Exception as he:
                            print(f"[SPLIT ADJUST] Failed to update history for {ticker}: {he}")
                            
                        print(f"[SPLIT ADJUSTED] Ticker {ticker} adjusted in Supabase. Entry: {old_entry} -> {new_entry}, Stop: {old_stop} -> {new_stop}")
                        
                        # Update in-memory values for the current run
                        open_positions[i]['entry_price'] = new_entry
                        open_positions[i]['stop_loss'] = new_stop
                        if new_target_1 is not None: open_positions[i]['target_1'] = new_target_1
                        if new_target_2 is not None: open_positions[i]['target_2'] = new_target_2
                        if new_target_3 is not None: open_positions[i]['target_3'] = new_target_3
                        if new_max_shares is not None: open_positions[i]['max_shares'] = new_max_shares
                        open_positions[i]['narrative'] = new_narrative
                        
                        adjusted_any = True
        except Exception as e:
            print(f"[SPLIT ERROR] Failed to check splits for {ticker}: {e}")
            
    return adjusted_any

def monitor_open_positions():
    supabase = get_client()
    
    # 1. Process any pending signals first (Gap Tolerance Gate)
    process_pending_signals(supabase)
    
    # 2. Query open positions
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
        
    # 3. Check for stock splits before executing price sweeps (Stock Split Gate)
    check_stock_splits(supabase, open_positions)
        
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
        # ponytail: targets can be None for trend/momentum strategies
        target_1 = float(pos['target_1']) if pos.get('target_1') is not None else None
        target_2 = float(pos['target_2']) if pos.get('target_2') is not None else None
        target_3 = float(pos['target_3']) if pos.get('target_3') is not None else None
        has_targets = target_1 is not None
        
        sell_triggered = False
        status = 'open'
        reason = ""
        
        if has_targets:
            # === Category 1: Short-Term strategies with T1/T2/T3 scale-out ===
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
                # ponytail: Breakeven stop — move stop_loss up to entry_price
                print(f"[MONITOR] Breakeven stop activated for {ticker}: stop_loss -> {entry_price:.2f}")
                supabase.table('signals').update({
                    'stop_loss': entry_price
                }).eq('ticker', ticker).eq('status', 'open').execute()
            elif current_price <= stop_loss:
                sell_triggered = True
                reason = "Stop loss hit"
                status = 'closed'
        else:
            # === Category 2: Trend/Momentum — trailing stop only ===
            if current_price <= stop_loss:
                sell_triggered = True
                reason = "Trailing stop hit"
                status = 'closed'
            else:
                # ponytail: Ratchet trailing stop upward using 3*ATR, never lower
                try:
                    import yfinance as yf
                    hist = yf.Ticker(ticker).history(period="30d")
                    if hist is not None and len(hist) >= 14:
                        tr = hist[['High', 'Low', 'Close']].copy()
                        tr['tr1'] = tr['High'] - tr['Low']
                        tr['tr2'] = (tr['High'] - tr['Close'].shift(1)).abs()
                        tr['tr3'] = (tr['Low'] - tr['Close'].shift(1)).abs()
                        tr['TR'] = tr[['tr1', 'tr2', 'tr3']].max(axis=1)
                        current_atr = float(tr['TR'].ewm(span=14, adjust=False).mean().iloc[-1])
                        new_stop = round(current_price - 3.0 * current_atr, 2)
                        if new_stop > stop_loss:
                            print(f"[MONITOR] Trailing stop ratcheted for {ticker}: {stop_loss:.2f} -> {new_stop:.2f}")
                            supabase.table('signals').update({
                                'stop_loss': new_stop,
                                'price': current_price,
                            }).eq('ticker', ticker).eq('status', 'open').execute()
                            continue  # Already updated price, skip to next
                except Exception as e:
                    print(f"[MONITOR] Failed to compute trailing stop for {ticker}: {e}")
            
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

