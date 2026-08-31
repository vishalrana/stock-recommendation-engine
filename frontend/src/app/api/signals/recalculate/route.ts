import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { parseScaleOut, getDollarExits } from '@/lib/position-utils';
import { getNYDateTime } from '@/lib/market-evaluator';

export const dynamic = 'force-dynamic';

interface QuoteOHLC {
  open: number;
  high: number;
  low: number;
  close: number;
}

async function fetchOHLCQuote(ticker: string): Promise<QuoteOHLC | null> {
  const symbol = ticker.toUpperCase().replace(' (P)', '');
  const tiingoKey = process.env.TIINGO_API_KEY;
  const finnhubKey = process.env.FINNHUB_API_KEY;

  // 1. Tiingo IEX
  if (tiingoKey) {
    try {
      const res = await fetch(`https://api.tiingo.com/iex/${symbol}?token=${tiingoKey}`, { cache: 'no-store' });
      if (res.status === 200) {
        const data = await res.json();
        if (data && data.length > 0) {
          const row = data[0];
          const open = row.open ?? row.last ?? row.close;
          const high = row.high ?? row.last ?? row.close;
          const low = row.low ?? row.last ?? row.close;
          const close = row.last ?? row.tngoLast ?? row.close ?? row.open;
          if (close !== undefined && close !== null) {
            return {
              open: Number(open || close),
              high: Number(high || close),
              low: Number(low || close),
              close: Number(close),
            };
          }
        }
      }
    } catch (e) {
      console.warn(`[RECALCULATE] Tiingo quote error for ${symbol}:`, e);
    }
  }

  // 2. Finnhub
  if (finnhubKey) {
    try {
      const res = await fetch(`https://finnhub.io/api/v1/quote?symbol=${symbol}&token=${finnhubKey}`, { cache: 'no-store' });
      if (res.status === 200) {
        const data = await res.json();
        if (data && data.c && data.c > 0) {
          return {
            open: Number(data.o || data.c),
            high: Number(data.h || data.c),
            low: Number(data.l || data.c),
            close: Number(data.c),
          };
        }
      }
    } catch (e) {
      console.warn(`[RECALCULATE] Finnhub quote error for ${symbol}:`, e);
    }
  }

  // 3. Public Yahoo Chart API fallback
  try {
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'application/json'
    };
    const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?range=1d&interval=1m`, {
      cache: 'no-store',
      headers
    });
    if (res.status === 200) {
      const data = await res.json();
      const meta = data?.chart?.result?.[0]?.meta;
      if (meta) {
        const close = meta.regularMarketPrice ?? meta.chartPreviousClose ?? meta.previousClose;
        const open = meta.regularMarketOpen ?? meta.regularMarketPrice ?? close;
        const high = meta.regularMarketDayHigh ?? Math.max(Number(open), Number(close));
        const low = meta.regularMarketDayLow ?? Math.min(Number(open), Number(close));
        if (close && Number(close) > 0) {
          return {
            open: Number(open),
            high: Number(high),
            low: Number(low),
            close: Number(close),
          };
        }
      }
    }
  } catch (e) {
    console.warn(`[RECALCULATE] Yahoo chart quote error for ${symbol}:`, e);
  }

  return null;
}

export async function POST() {
  return handleRecalculate();
}

export async function GET() {
  return handleRecalculate();
}

async function handleRecalculate() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY;

  if (!supabaseUrl || !supabaseServiceKey) {
    return NextResponse.json({ error: 'Supabase configuration missing' }, { status: 500 });
  }

  const supabase = createClient(supabaseUrl, supabaseServiceKey);
  const todayStr = getNYDateTime().dateStr;

  try {
    // 1. Fetch all active or in-progress signals
    const { data: signals, error: fetchErr } = await supabase
      .from('signals')
      .select('*')
      .in('status', ['pending', 'open', 'hit_t1', 'hit_t2']);

    if (fetchErr) {
      return NextResponse.json({ error: `Failed to fetch signals: ${fetchErr.message}` }, { status: 500 });
    }

    if (!signals || signals.length === 0) {
      return NextResponse.json({
        success: true,
        message: 'No active or pending signals found to recalculate.',
        summary: {
          updatedCount: 0,
          openCount: 0,
          hitT1Count: 0,
          hitT2Count: 0,
          hitT3Count: 0,
          stoppedCount: 0,
          cancelledCount: 0,
        },
        signals: [],
      });
    }

    const updatedSignals: any[] = [];
    const summary = {
      updatedCount: 0,
      openCount: 0,
      hitT1Count: 0,
      hitT2Count: 0,
      hitT3Count: 0,
      stoppedCount: 0,
      cancelledCount: 0,
    };

    for (const sig of signals) {
      const ticker = sig.ticker;
      const quote = await fetchOHLCQuote(ticker);

      let currentStatus = sig.status || 'open';
      let entryPrice = parseFloat(sig.entry_price || sig.price || '0');
      let stopLoss = parseFloat(sig.stop_loss || '0');
      const t1 = sig.target_1 ? parseFloat(sig.target_1) : null;
      const t2 = sig.target_2 ? parseFloat(sig.target_2) : null;
      const t3 = sig.target_3 ? parseFloat(sig.target_3) : null;

      const [w1, w2, w3] = parseScaleOut(sig.scale_out_weights);
      const allocatedDollars = parseFloat(sig.allocated_dollars || '0');
      const maxShares = parseFloat(sig.max_shares || '0');
      const effectiveShares = maxShares > 0 ? maxShares : (entryPrice > 0 ? allocatedDollars / entryPrice : 0);

      const updatePayload: Record<string, any> = {};
      let exitPriceT1 = sig.exit_price_t1 ?? null;
      let exitPriceT2 = sig.exit_price_t2 ?? null;
      let exitPriceT3 = sig.exit_price_t3 ?? null;
      let exitPriceFinal = sig.exit_price ?? null;
      let sellSignal = sig.sell_signal ?? false;
      let sellReason = sig.sell_signal_reason ?? null;

      if (quote) {
        updatePayload.price = quote.close;

        // State Machine Transitions
        if (currentStatus === 'pending') {
          // Check morning gap rule
          const gapPct = entryPrice > 0 ? ((quote.open - entryPrice) / entryPrice) * 100 : 0;
          if (gapPct > 3.0) {
            currentStatus = 'cancelled_gap_up';
            sellSignal = true;
            sellReason = 'Cancelled: Stock gapped up > 3% above reference entry.';
            exitPriceFinal = quote.open;
            updatePayload.exit_date = todayStr;
            summary.cancelledCount++;
          } else if (quote.open <= stopLoss) {
            currentStatus = 'cancelled_gap_down';
            sellSignal = true;
            sellReason = 'Cancelled: Stock gapped down below stop loss.';
            exitPriceFinal = quote.open;
            updatePayload.exit_date = todayStr;
            summary.cancelledCount++;
          } else {
            // Normal market open transition
            currentStatus = 'open';
            const originalRisk = entryPrice - stopLoss;
            const newStop = Math.round((quote.open - originalRisk) * 100) / 100;
            entryPrice = quote.open;
            stopLoss = newStop;
            updatePayload.entry_price = quote.open;
            updatePayload.stop_loss = newStop;
            summary.openCount++;
          }
        } else if (currentStatus === 'open') {
          if (quote.low <= stopLoss) {
            currentStatus = 'stopped';
            sellSignal = true;
            sellReason = 'Stop loss hit';
            exitPriceFinal = stopLoss;
            updatePayload.exit_date = todayStr;
            summary.stoppedCount++;
          } else if (t1 !== null && quote.high >= t1) {
            currentStatus = 'hit_t1';
            exitPriceT1 = t1;
            sellSignal = true;
            sellReason = `Target 1 hit – sold ${w1}%`;
            // Ratchet stop to breakeven
            stopLoss = entryPrice;
            updatePayload.stop_loss = entryPrice;
            updatePayload.exit_price_t1 = t1;
            summary.hitT1Count++;
          } else {
            summary.openCount++;
          }
        } else if (currentStatus === 'hit_t1') {
          if (quote.low <= stopLoss) {
            currentStatus = 'stopped';
            sellSignal = true;
            sellReason = 'Breakeven stop hit on remaining position';
            exitPriceFinal = stopLoss;
            updatePayload.exit_date = todayStr;
            summary.stoppedCount++;
          } else if (t2 !== null && quote.high >= t2) {
            currentStatus = 'hit_t2';
            exitPriceT2 = t2;
            sellSignal = true;
            sellReason = `Target 2 hit – sold ${w2}%`;
            updatePayload.exit_price_t2 = t2;
            summary.hitT2Count++;
          } else {
            summary.hitT1Count++;
          }
        } else if (currentStatus === 'hit_t2') {
          if (quote.low <= stopLoss) {
            currentStatus = 'stopped';
            sellSignal = true;
            sellReason = 'Stop hit on runner position';
            exitPriceFinal = stopLoss;
            updatePayload.exit_date = todayStr;
            summary.stoppedCount++;
          } else if (t3 !== null && quote.high >= t3) {
            currentStatus = 'hit_t3';
            exitPriceT3 = t3;
            exitPriceFinal = t3;
            sellSignal = true;
            sellReason = 'Target 3 hit – full exit';
            updatePayload.exit_date = todayStr;
            updatePayload.exit_price_t3 = t3;
            summary.hitT3Count++;
          } else {
            summary.hitT2Count++;
          }
        }
      }

      // Calculate dollar exits
      const dollarExits = getDollarExits(allocatedDollars, sig.scale_out_weights, {
        target_1: sig.target_1,
        target_2: sig.target_2,
        target_3: sig.target_3,
      });

      // Calculate unrealized P&L
      const currentPrice = quote ? quote.close : (parseFloat(sig.price) || entryPrice);
      let activeShares = effectiveShares;
      if (currentStatus === 'hit_t1') {
        activeShares = effectiveShares * (1.0 - w1 / 100.0);
      } else if (currentStatus === 'hit_t2') {
        activeShares = effectiveShares * (1.0 - (w1 + w2) / 100.0);
      } else if (currentStatus === 'hit_t3' || currentStatus === 'stopped' || currentStatus.startsWith('cancelled')) {
        activeShares = 0;
      }

      const unrealizedPnL = entryPrice > 0 
        ? Math.round((currentPrice - entryPrice) * activeShares * 100) / 100 
        : 0;

      updatePayload.status = currentStatus;
      updatePayload.sell_signal = sellSignal;
      updatePayload.sell_signal_reason = sellReason;
      updatePayload.sell_price = exitPriceFinal ?? (quote ? quote.close : null);
      updatePayload.exit_price = exitPriceFinal;

      // Update database
      await supabase.from('signals').update(updatePayload).eq('id', sig.id);

      // Mirror status in signals_history
      if (exitPriceFinal || currentStatus === 'open' || currentStatus.startsWith('cancelled')) {
        const histUpdate: Record<string, any> = {
          entry_price: entryPrice,
          stop_loss: stopLoss,
        };
        if (currentStatus === 'stopped' || currentStatus === 'hit_t3' || currentStatus.startsWith('cancelled')) {
          histUpdate.outcome = currentStatus;
          histUpdate.outcome_date = todayStr;
          histUpdate.exit_price = exitPriceFinal;
        }
        await supabase
          .from('signals_history')
          .update(histUpdate)
          .eq('scan_date', sig.scan_date)
          .eq('ticker', ticker);
      }

      summary.updatedCount++;

      updatedSignals.push({
        id: sig.id,
        ticker: sig.ticker,
        company_name: sig.company_name,
        strategy: sig.strategy_name || sig.strategy,
        previous_status: sig.status,
        new_status: currentStatus,
        entry_price: entryPrice,
        current_price: currentPrice,
        stop_loss: stopLoss,
        quote: quote || null,
        exit_prices: {
          exit_price_t1: exitPriceT1,
          exit_price_t2: exitPriceT2,
          exit_price_t3: exitPriceT3,
          exit_price_final: exitPriceFinal,
        },
        unrealized_pnl: unrealizedPnL,
        dollar_exit_breakdown: dollarExits,
      });
    }

    return NextResponse.json({
      success: true,
      summary,
      signals: updatedSignals,
    });
  } catch (err: any) {
    console.error('[RECALCULATE] Fatal error:', err);
    return NextResponse.json({ error: err.message || 'Internal error during recalculation' }, { status: 500 });
  }
}
