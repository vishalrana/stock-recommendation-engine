import { createClient } from '@supabase/supabase-js';

// Market Holiday List 2025-2027
const MARKET_HOLIDAYS = new Set([
  // 2025
  '2025-01-01', '2025-01-20', '2025-02-17', '2025-04-18', '2025-05-26', '2025-06-19', '2025-07-04', '2025-09-01', '2025-11-27', '2025-12-25',
  // 2026
  '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25',
  // 2027
  '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24'
]);

export interface NYDateTime {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
  dayOfWeek: number;
  dateStr: string;
}

export function getNYDateTime(): NYDateTime {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
    hour12: false
  });
  
  const parts = formatter.formatToParts(new Date());
  const result: Record<string, string> = {};
  for (const part of parts) {
    result[part.type] = part.value;
  }
  
  const nyDateStr = `${result.year}-${String(result.month).padStart(2, '0')}-${String(result.day).padStart(2, '0')}`;
  
  // Get day of week in New York Time
  const nyTimeDate = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
  
  return {
    year: parseInt(result.year, 10),
    month: parseInt(result.month, 10),
    day: parseInt(result.day, 10),
    hour: parseInt(result.hour, 10),
    minute: parseInt(result.minute, 10),
    second: parseInt(result.second, 10),
    dayOfWeek: nyTimeDate.getDay(), // 0 = Sunday, 6 = Saturday
    dateStr: nyDateStr
  };
}

export function is_us_market_open(): { open: boolean; reason?: string } {
  const ny = getNYDateTime();
  
  if (ny.dayOfWeek === 0 || ny.dayOfWeek === 6) {
    return { open: false, reason: 'Weekend' };
  }
  if (MARKET_HOLIDAYS.has(ny.dateStr)) {
    return { open: false, reason: 'Market Holiday' };
  }
  
  // Allowed Standard and Extended hours: Monday-Friday, 4:00 AM to 8:00 PM ET
  if (ny.hour < 4 || ny.hour >= 20) {
    return { open: false, reason: 'Outside Extended Market Hours (4:00 AM - 8:00 PM ET)' };
  }
  
  return { open: true };
}

async function fetchLivePrice(ticker: string, tiingoKey?: string, finnhubKey?: string): Promise<number | null> {
  const symbol = ticker.toUpperCase();
  
  if (tiingoKey) {
    try {
      const res = await fetch(`https://api.tiingo.com/iex/${symbol}?token=${tiingoKey}`, { cache: 'no-store' });
      if (res.status === 200) {
        const data = await res.json();
        if (data && data.length > 0) {
          const row = data[0];
          const price = row.last ?? row.tngoLast ?? row.close ?? row.open;
          if (price !== undefined && price !== null) {
            return Number(price);
          }
        }
      }
    } catch (e) {
      console.error(`[EVALUATOR] Tiingo live price fetch failed for ${symbol}:`, e);
    }
  }
  
  if (finnhubKey) {
    try {
      const res = await fetch(`https://finnhub.io/api/v1/quote?symbol=${symbol}&token=${finnhubKey}`, { cache: 'no-store' });
      if (res.status === 200) {
        const data = await res.json();
        if (data && data.c) {
          return Number(data.c);
        }
      }
    } catch (e) {
      console.error(`[EVALUATOR] Finnhub fallback quote fetch failed for ${symbol}:`, e);
    }
  }

  // Final fallback to public Yahoo Chart endpoint
  try {
    const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?range=1d&interval=1m`, { cache: 'no-store' });
    if (res.status === 200) {
      const data = await res.json();
      const meta = data?.chart?.result?.[0]?.meta;
      if (meta && meta.regularMarketPrice) {
        return Number(meta.regularMarketPrice);
      }
    }
  } catch (e) {
    console.error(`[EVALUATOR] Yahoo public quote fallback failed for ${symbol}:`, e);
  }
  
  return null;
}

async function checkStockSplitNode(ticker: string): Promise<number | null> {
  const symbol = ticker.toUpperCase();
  try {
    const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?range=2d&interval=1d&events=splits`, { cache: 'no-store' });
    if (res.status === 200) {
      const json = await res.json();
      const result = json?.chart?.result?.[0];
      const splits = result?.events?.splits;
      if (splits) {
        const todayNYStr = getNYDateTime().dateStr;
        for (const key of Object.keys(splits)) {
          const splitEvent = splits[key];
          // date is unix timestamp in seconds
          const splitDate = new Date(splitEvent.date * 1000);
          // Format split date in NY timezone
          const splitNYStr = new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/New_York',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
          }).format(splitDate);
          
          // format MM/DD/YYYY to YYYY-MM-DD
          const [m, d, y] = splitNYStr.split('/');
          const splitNYFormatted = `${y}-${m}-${d}`;
          
          if (splitNYFormatted === todayNYStr) {
            const splitRatio = Number(splitEvent.numerator) / Number(splitEvent.denominator);
            if (splitRatio > 0 && splitRatio !== 1.0) {
              return splitRatio;
            }
          }
        }
      }
    }
  } catch (e) {
    console.error(`[EVALUATOR] Yahoo split detection failed for ${symbol}:`, e);
  }
  return null;
}

async function calculateATR(ticker: string, tiingoKey: string): Promise<number | null> {
  const symbol = ticker.toUpperCase();
  try {
    // Fetch last 45 calendar days to get 30 daily bars
    const startDate = new Date(Date.now() - 45 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
    const res = await fetch(`https://api.tiingo.com/tiingo/daily/${symbol}/prices?startDate=${startDate}&token=${tiingoKey}`, { cache: 'no-store' });
    if (res.status !== 200) return null;
    const prices = await res.json();
    if (!prices || prices.length < 15) return null;
    
    // Sort ascending by date
    prices.sort((a: any, b: any) => new Date(a.date).getTime() - new Date(b.date).getTime());
    
    const trs: number[] = [];
    for (let i = 1; i < prices.length; i++) {
      const high = prices[i].high;
      const low = prices[i].low;
      const prevClose = prices[i-1].close;
      const tr = Math.max(
        high - low,
        Math.abs(high - prevClose),
        Math.abs(low - prevClose)
      );
      trs.push(tr);
    }
    
    if (trs.length < 14) return null;
    
    // Simple Moving Average for first 14 TR values
    let sum = 0;
    for (let i = 0; i < 14; i++) {
      sum += trs[i];
    }
    let atr = sum / 14;
    
    // Wilder's EMA smoothing formula for the rest
    for (let i = 14; i < trs.length; i++) {
      atr = (atr * 13 + trs[i]) / 14;
    }
    
    return atr;
  } catch (e) {
    console.error(`[EVALUATOR] ATR calculation failed for ${symbol}:`, e);
    return null;
  }
}

async function updatePortfolioRealizedPnL(supabase: any, pnlDollars: number) {
  try {
    const res = await supabase.from("portfolio_state").select("*").order("created_at", { ascending: false }).limit(1);
    let portfolio_value = 10000.0;
    let peak_value = 10000.0;
    if (res.data && res.data.length > 0) {
      const state = res.data[0];
      portfolio_value = parseFloat(state.portfolio_value);
      peak_value = parseFloat(state.peak_value);
    }
    
    const new_portfolio_value = portfolio_value + pnlDollars;
    const new_peak_value = Math.max(peak_value, new_portfolio_value);
    const new_dd = ((new_peak_value - new_portfolio_value) / new_peak_value) * 100;
    
    const todayStr = getNYDateTime().dateStr;
    await supabase.from("portfolio_state").insert({
      date: todayStr,
      portfolio_value: new_portfolio_value,
      peak_value: new_peak_value,
      current_drawdown_pct: new_dd
    });
    
    console.log(`[PORTFOLIO UPDATE] Realized PNL: $${pnlDollars.toFixed(2)}, New value: $${new_portfolio_value.toFixed(2)}, Drawdown: ${new_dd.toFixed(2)}%`);
  } catch (e) {
    console.error('[PORTFOLIO UPDATE] Failed to update portfolio state realized PNL:', e);
  }
}

export async function evaluate_open_positions() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY;
  const tiingoKey = process.env.TIINGO_API_KEY;
  const finnhubKey = process.env.FINNHUB_API_KEY;
  
  if (!supabaseUrl || !supabaseServiceKey) {
    throw new Error('Supabase configuration missing');
  }
  
  const supabase = createClient(supabaseUrl, supabaseServiceKey);
  const todayStr = getNYDateTime().dateStr;
  
  const summary = {
    processedPending: 0,
    cancelledGapUp: 0,
    cancelledGapDown: 0,
    openedPending: 0,
    processedOpen: 0,
    splitsAdjusted: 0,
    closedExits: 0,
    ratchetedStops: 0,
    updatedPrices: 0,
    errors: [] as string[]
  };

  // ==========================================
  // STEP 1: Process Pending Signals (Morning Open Gate)
  // ==========================================
  try {
    const { data: pendingSignals } = await supabase.from('signals').select('*').eq('status', 'pending');
    if (pendingSignals && pendingSignals.length > 0) {
      console.log(`[EVALUATOR] Found ${pendingSignals.length} pending signals to process.`);
      const MAX_GAP_SLIPPAGE_PCT = 2.0;
      
      for (const sig of pendingSignals) {
        summary.processedPending++;
        const ticker = sig.ticker;
        const openPrice = await fetchLivePrice(ticker, tiingoKey, finnhubKey);
        
        if (openPrice === null) {
          console.log(`[PENDING GATE] Open price not yet available for ${ticker}. Skipping.`);
          continue;
        }
        
        const entryPrice = parseFloat(sig.entry_price);
        const stopLoss = parseFloat(sig.stop_loss);
        const gapPct = ((openPrice - entryPrice) / entryPrice) * 100.0;
        
        console.log(`[PENDING GATE] Ticker: ${ticker} | Ref Entry: ${entryPrice.toFixed(2)} | Open: ${openPrice.toFixed(2)} | Gap: ${gapPct.toFixed(2)}%`);
        
        if (gapPct > MAX_GAP_SLIPPAGE_PCT) {
          const reason = "Cancelled: Stock gapped up beyond acceptable risk tolerance.";
          await supabase.from('signals').update({
            status: 'cancelled_gap_up',
            sell_signal: true,
            sell_signal_reason: reason,
            price: openPrice,
            sell_price: openPrice,
            exit_date: todayStr
          }).eq('id', sig.id);
          
          await supabase.from('signals_history').update({
            outcome: 'cancelled_gap_up',
            entry_price: openPrice,
            exit_price: openPrice,
            outcome_date: todayStr
          }).eq('scan_date', sig.scan_date).eq('ticker', ticker);
          
          summary.cancelledGapUp++;
        } else if (openPrice <= stopLoss) {
          const reason = "Cancelled: Stock gapped down below Stop Loss.";
          await supabase.from('signals').update({
            status: 'cancelled_gap_down',
            sell_signal: true,
            sell_signal_reason: reason,
            price: openPrice,
            sell_price: openPrice,
            exit_date: todayStr
          }).eq('id', sig.id);
          
          await supabase.from('signals_history').update({
            outcome: 'cancelled_gap_down',
            entry_price: openPrice,
            exit_price: openPrice,
            outcome_date: todayStr
          }).eq('scan_date', sig.scan_date).eq('ticker', ticker);
          
          summary.cancelledGapDown++;
        } else {
          // Gate passed: Transition to open
          await supabase.from('signals').update({
            status: 'open',
            entry_price: openPrice,
            price: openPrice
          }).eq('id', sig.id);
          
          await supabase.from('signals_history').update({
            entry_price: openPrice
          }).eq('scan_date', sig.scan_date).eq('ticker', ticker);
          
          summary.openedPending++;
        }
      }
    }
  } catch (e: any) {
    summary.errors.push(`Pending check failed: ${e.message}`);
  }

  // ==========================================
  // STEP 2: Process Open Signals (Splits, Exits, Stops)
  // ==========================================
  try {
    const { data: openSignals } = await supabase.from('signals').select('*').eq('status', 'open');
    if (openSignals && openSignals.length > 0) {
      console.log(`[EVALUATOR] Found ${openSignals.length} open signals to sweep.`);
      
      for (const sig of openSignals) {
        summary.processedOpen++;
        const ticker = sig.ticker;
        
        // 2a. Check Stock Splits for today
        const narrative = sig.narrative || '';
        const tag = `[SPLIT_ADJUSTED_${todayStr}]`;
        
        if (!narrative.includes(tag)) {
          const splitRatio = await checkStockSplitNode(ticker);
          if (splitRatio && splitRatio !== 1.0) {
            console.log(`[SPLIT DETECTED] ${ticker} split ratio: ${splitRatio} today.`);
            
            const oldEntry = parseFloat(sig.entry_price);
            const oldStop = parseFloat(sig.stop_loss);
            const newEntry = Math.round((oldEntry / splitRatio) * 100) / 100;
            const newStop = Math.round((oldStop / splitRatio) * 100) / 100;
            
            const newT1 = sig.target_1 ? Math.round((parseFloat(sig.target_1) / splitRatio) * 100) / 100 : null;
            const newT2 = sig.target_2 ? Math.round((parseFloat(sig.target_2) / splitRatio) * 100) / 100 : null;
            const newT3 = sig.target_3 ? Math.round((parseFloat(sig.target_3) / splitRatio) * 100) / 100 : null;
            
            const oldShares = sig.max_shares;
            const newShares = oldShares ? Math.round(parseFloat(oldShares) * splitRatio) : null;
            
            const newNarrative = `${narrative} ${tag}`.trim();
            
            const updatePayload: any = {
              entry_price: newEntry,
              stop_loss: newStop,
              narrative: newNarrative
            };
            if (newT1 !== null) updatePayload.target_1 = newT1;
            if (newT2 !== null) updatePayload.target_2 = newT2;
            if (newT3 !== null) updatePayload.target_3 = newT3;
            if (newShares !== null) updatePayload.max_shares = newShares;
            
            await supabase.from('signals').update(updatePayload).eq('id', sig.id);
            
            // Mirror to history
            await supabase.from('signals_history').update({
              entry_price: newEntry,
              stop_loss: newStop,
              target_1: newT1,
              target_2: newT2,
              target_3: newT3,
              max_shares: newShares,
              narrative: newNarrative
            }).eq('scan_date', sig.scan_date).eq('ticker', ticker);
            
            // Adjust in-memory for subsequent calculations
            sig.entry_price = newEntry;
            sig.stop_loss = newStop;
            if (newT1 !== null) sig.target_1 = newT1;
            if (newT2 !== null) sig.target_2 = newT2;
            if (newT3 !== null) sig.target_3 = newT3;
            if (newShares !== null) sig.max_shares = newShares;
            
            summary.splitsAdjusted++;
          }
        }
        
        // 2b. Fetch Live Price
        const livePrice = await fetchLivePrice(ticker, tiingoKey, finnhubKey);
        if (livePrice === null) {
          console.log(`[EVALUATOR] Failed to retrieve live price for ${ticker}. Skipping price checks.`);
          continue;
        }
        
        const entryPrice = parseFloat(sig.entry_price);
        const stopLoss = parseFloat(sig.stop_loss);
        const t1 = sig.target_1 ? parseFloat(sig.target_1) : null;
        const t2 = sig.target_2 ? parseFloat(sig.target_2) : null;
        const t3 = sig.target_3 ? parseFloat(sig.target_3) : null;
        const hasTargets = t1 !== null;
        
        let sellTriggered = false;
        let isPartialExit = false;
        let partialFraction = 0.0;
        let partialReason = '';
        let status = 'open';
        let reason = '';
        let exitOutcome = '';
        let exitPrice = livePrice;
        
        if (hasTargets) {
          // Category 1: Targets-based scale outs
          if (livePrice >= (t3 || 999999)) {
            sellTriggered = true;
            reason = 'Target 3 hit – full exit';
            status = 'closed';
            exitOutcome = 'hit_t3';
            exitPrice = t3 || livePrice;
          } else if (livePrice >= (t2 || 999999)) {
            // Check if T2 was already processed to avoid repeat triggers
            if (!sig.sell_signal_reason?.includes('Target 2')) {
              sellTriggered = true;
              isPartialExit = true;
              partialFraction = 0.30;
              reason = 'Target 2 hit – sell 30%';
              partialReason = 'Target 2 hit (Partial)';
              status = 'open';
              exitOutcome = 'hit_t2';
              exitPrice = t2 || livePrice;
            }
          } else if (livePrice >= (t1 || 999999)) {
            // Check if T1 was already processed to avoid repeat triggers
            if (!sig.sell_signal_reason?.includes('Target 1')) {
              sellTriggered = true;
              isPartialExit = true;
              partialFraction = 0.50;
              reason = 'Target 1 hit – sell 50%';
              partialReason = 'Target 1 hit (Partial)';
              status = 'open';
              exitOutcome = 'hit_t1';
              exitPrice = t1 || livePrice;
            }
          } else if (livePrice <= stopLoss) {
            sellTriggered = true;
            reason = 'Stop loss hit';
            status = 'closed';
            exitOutcome = 'stopped';
            exitPrice = stopLoss; // strictly exact stop_loss price
          }
        } else {
          // Category 2: Trailing Stop only
          if (livePrice <= stopLoss) {
            sellTriggered = true;
            reason = 'Trailing stop hit';
            status = 'closed';
            exitOutcome = 'stopped';
            exitPrice = livePrice; // live market price that breached the stop
          }
        }
        
        // 2c. Execute exits if triggered
        if (sellTriggered) {
          console.log(`[MONITOR ALERT] Triggered for ${ticker}: ${reason} at ${exitPrice}`);
          
          if (isPartialExit) {
            // ponytail: Position Lot Splitting logic
            const originalMaxShares = sig.max_shares ? parseInt(sig.max_shares) : 0;
            const originalAllocated = sig.allocated_dollars ? parseFloat(sig.allocated_dollars) : 0.0;
            
            const sharesSold = Math.floor(originalMaxShares * partialFraction);
            const dollarsSold = originalAllocated * partialFraction;
            
            if (sharesSold > 0 && dollarsSold > 0) {
              const returnPct = entryPrice > 0 ? ((exitPrice - entryPrice) / entryPrice) * 100 : 0;
              let holdingDays = 0;
              if (sig.scan_date) {
                const start = new Date(sig.scan_date);
                const end = new Date(todayStr);
                holdingDays = Math.max(0, Math.floor((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)));
              }
              
              // 1. Insert CLOSED portion into signals_history with dynamic outcome
              const histRow = {
                scan_date: sig.scan_date,
                ticker: `${sig.ticker} (P)`,
                company_name: sig.company_name,
                industry: sig.industry,
                price: exitPrice,
                entry_price: entryPrice,
                stop_loss: stopLoss,
                exit_price: exitPrice,
                outcome: exitOutcome,
                outcome_date: todayStr,
                outcome_return_pct: returnPct,
                outcome_holding_days: holdingDays,
                allocated_dollars: dollarsSold,
                max_shares: sharesSold,
                strategy_name: sig.strategy_name || sig.strategy
              };
              
              console.log(`[MONITOR] Inserting partial closed history lot for ${ticker}: ${sharesSold} shares, $${dollarsSold.toFixed(2)}`);
              await supabase.from('signals_history').insert(histRow);
              
              // Realized Equity Sync: Update portfolio state realized P&L
              const pnlDollars = (exitPrice - entryPrice) * sharesSold;
              await updatePortfolioRealizedPnL(supabase, pnlDollars);
              
              // 2. UPDATE existing OPEN row in signals table
              const remainingShares = originalMaxShares - sharesSold;
              const remainingDollars = originalAllocated - dollarsSold;
              
              const updatePayload: any = {
                max_shares: remainingShares,
                allocated_dollars: remainingDollars,
                sell_signal_reason: reason,
                sell_signal: true,
                sell_price: exitPrice,
                price: livePrice
              };
              
              if (exitOutcome === 'hit_t1') {
                console.log(`[MONITOR] Target 1 hit. Setting stop loss to entry price (breakeven): ${entryPrice}`);
                updatePayload.stop_loss = entryPrice;
                sig.stop_loss = entryPrice; // local update
              }
              
              await supabase.from('signals').update(updatePayload).eq('id', sig.id);
            }
          } else {
            // Retrieve current allocation from active signals row
            const originalMaxShares = sig.max_shares ? parseInt(sig.max_shares) : 0;
            const originalAllocated = sig.allocated_dollars ? parseFloat(sig.allocated_dollars) : 0.0;
            
            // Full exit — update signals row and signals_history row
            const updatePayload: any = {
              sell_signal: true,
              sell_signal_reason: reason,
              price: exitPrice,
              sell_price: exitPrice,
              status: status
            };
            if (status === 'closed') {
              updatePayload.exit_date = todayStr;
            }
            await supabase.from('signals').update(updatePayload).eq('id', sig.id);
            
            if (exitOutcome) {
              const returnPct = entryPrice > 0 ? ((exitPrice - entryPrice) / entryPrice) * 100 : 0;
              let holdingDays = 0;
              if (sig.scan_date) {
                const start = new Date(sig.scan_date);
                const end = new Date(todayStr);
                holdingDays = Math.max(0, Math.floor((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24)));
              }
              
              await supabase.from('signals_history').update({
                outcome: exitOutcome,
                outcome_date: todayStr,
                outcome_return_pct: returnPct,
                outcome_holding_days: holdingDays,
                exit_price: exitPrice,
                allocated_dollars: originalAllocated,
                max_shares: originalMaxShares
              }).eq('scan_date', sig.scan_date).eq('ticker', ticker);
              
              if (status === 'closed') {
                summary.closedExits++;
                // Realized Equity Sync: Update portfolio state realized P&L
                const pnlDollars = (exitPrice - entryPrice) * originalMaxShares;
                await updatePortfolioRealizedPnL(supabase, pnlDollars);
              }
            }
          }
        } else {
          // 2d. Ratchet Trailing Stops for open momentum/trend trades
          if (!hasTargets && tiingoKey) {
            const currentATR = await calculateATR(ticker, tiingoKey);
            if (currentATR !== null) {
              const newStop = Math.round((livePrice - 3.0 * currentATR) * 100) / 100;
              if (newStop > stopLoss) {
                console.log(`[MONITOR] Ratcheting trailing stop for ${ticker}: ${stopLoss} -> ${newStop}`);
                await supabase.from('signals').update({
                  stop_loss: newStop,
                  price: livePrice
                }).eq('id', sig.id);
                
                await supabase.from('signals_history').update({
                  stop_loss: newStop
                }).eq('scan_date', sig.scan_date).eq('ticker', ticker);
                
                summary.ratchetedStops++;
                continue; // skipped standard price update
              }
            }
          }
          
          // 2e. Update Current Price standard sweep
          await supabase.from('signals').update({
            price: livePrice
          }).eq('id', sig.id);
          summary.updatedPrices++;
        }
      }
    }
  } catch (e: any) {
    summary.errors.push(`Open positions sweep failed: ${e.message}`);
  }

  return summary;
}
