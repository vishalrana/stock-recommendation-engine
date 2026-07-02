-- Atomic realized-lot closing for partial/full exits.
-- Run this whole file in Supabase SQL Editor.

CREATE OR REPLACE FUNCTION execute_position_exit(
  p_signal_id TEXT,
  p_exit_price NUMERIC,
  p_outcome TEXT,
  p_reason TEXT,
  p_split_fraction NUMERIC DEFAULT 1,
  p_live_price NUMERIC DEFAULT NULL,
  p_move_stop_to_entry BOOLEAN DEFAULT FALSE
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $function$
DECLARE
  s signals%ROWTYPE;
  latest_state portfolio_state%ROWTYPE;
  today DATE := (now() AT TIME ZONE 'America/New_York')::DATE;
  split_fraction NUMERIC := COALESCE(p_split_fraction, 1);
  shares_sold INTEGER;
  dollars_sold NUMERIC;
  pnl NUMERIC;
  return_pct NUMERIC;
  holding_days INTEGER;
  history_ticker TEXT;
BEGIN
  SELECT * INTO s
  FROM signals
  WHERE id::TEXT = p_signal_id AND status = 'open'
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'open signal % not found', p_signal_id;
  END IF;

  IF split_fraction <= 0 OR split_fraction > 1 THEN
    RAISE EXCEPTION 'invalid split fraction %', split_fraction;
  END IF;

  shares_sold := CASE
    WHEN split_fraction = 1 THEN COALESCE(s.max_shares, 0)
    ELSE floor(COALESCE(s.max_shares, 0) * split_fraction)::INTEGER
  END;
  dollars_sold := CASE
    WHEN split_fraction = 1 THEN COALESCE(s.allocated_dollars, 0)
    ELSE COALESCE(s.allocated_dollars, 0) * split_fraction
  END;

  IF shares_sold <= 0 THEN
    RAISE EXCEPTION 'exit for % would sell 0 shares', s.ticker;
  END IF;

  pnl := (p_exit_price - s.entry_price) * shares_sold;
  return_pct := CASE WHEN s.entry_price > 0 THEN ((p_exit_price - s.entry_price) / s.entry_price) * 100 ELSE 0 END;
  holding_days := CASE WHEN s.scan_date IS NULL THEN 0 ELSE GREATEST(0, today - s.scan_date::DATE) END;
  history_ticker := CASE
    WHEN split_fraction = 1 THEN s.ticker
    ELSE left(s.ticker || '-' || upper(replace(p_outcome, 'hit_', '')), 30)
  END;

  IF split_fraction < 1 AND EXISTS (
    SELECT 1 FROM signals_history
    WHERE scan_date = s.scan_date
      AND ticker = history_ticker
      AND outcome = p_outcome
  ) THEN
    RETURN jsonb_build_object('ticker', s.ticker, 'outcome', p_outcome, 'skipped', 'lot already closed');
  END IF;

  INSERT INTO signals_history (
    scan_date, ticker, company_name, industry, price, entry_price, stop_loss,
    exit_price, upside_pct, risk_reward, current_rsi, volume_ratio, score,
    regime, composite_score, tier_label, adx_value, macd_histogram,
    rsi_min_10d, ema20, is_fallback,
    target_1, target_2, target_3, target_1_pct, target_2_pct, target_3_pct,
    weighted_rr, position_sizing, narrative, strategy_name, outcome,
    outcome_date, outcome_return_pct, outcome_holding_days, earnings_date,
    is_momentum_exception, distance_from_high_pct, strategy, quality_score,
    context_score, allocated_dollars, max_shares
  ) VALUES (
    s.scan_date, history_ticker, s.company_name, s.industry, p_exit_price, s.entry_price, s.stop_loss,
    p_exit_price, s.upside_pct, s.risk_reward, s.current_rsi, s.volume_ratio, s.score,
    s.regime, s.composite_score, s.tier_label, s.adx_value, s.macd_histogram,
    s.rsi_min_10d, s.ema20, s.is_fallback,
    s.target_1, s.target_2, s.target_3, s.target_1_pct, s.target_2_pct, s.target_3_pct,
    s.weighted_rr, s.position_sizing, s.narrative, s.strategy_name, p_outcome,
    today, return_pct, holding_days, s.earnings_date,
    s.is_momentum_exception, s.distance_from_high_pct, s.strategy, s.quality_score,
    s.context_score, dollars_sold, shares_sold
  )
  ON CONFLICT (scan_date, ticker) DO UPDATE SET
    price = EXCLUDED.price,
    exit_price = EXCLUDED.exit_price,
    outcome = EXCLUDED.outcome,
    outcome_date = EXCLUDED.outcome_date,
    outcome_return_pct = EXCLUDED.outcome_return_pct,
    outcome_holding_days = EXCLUDED.outcome_holding_days,
    allocated_dollars = EXCLUDED.allocated_dollars,
    max_shares = EXCLUDED.max_shares;

  IF split_fraction = 1 THEN
    UPDATE signals
    SET status = 'closed',
        exit_price = p_exit_price,
        sell_price = p_exit_price,
        price = COALESCE(p_live_price, p_exit_price),
        sell_signal = TRUE,
        sell_signal_reason = p_reason,
        sell_signal_date = today,
        exit_date = today
    WHERE id = s.id;
  ELSE
    UPDATE signals
    SET max_shares = COALESCE(s.max_shares, 0) - shares_sold,
        allocated_dollars = COALESCE(s.allocated_dollars, 0) - dollars_sold,
        stop_loss = CASE WHEN p_move_stop_to_entry THEN s.entry_price ELSE s.stop_loss END,
        sell_signal = TRUE,
        sell_signal_reason = p_reason,
        sell_signal_date = today,
        sell_price = p_exit_price,
        price = COALESCE(p_live_price, p_exit_price)
    WHERE id = s.id;
  END IF;

  SELECT * INTO latest_state
  FROM portfolio_state
  ORDER BY created_at DESC
  LIMIT 1
  FOR UPDATE;

  INSERT INTO portfolio_state (date, portfolio_value, peak_value, current_drawdown_pct)
  SELECT
    today,
    COALESCE(latest_state.portfolio_value, 10000) + pnl,
    GREATEST(COALESCE(latest_state.peak_value, 10000), COALESCE(latest_state.portfolio_value, 10000) + pnl),
    CASE
      WHEN GREATEST(COALESCE(latest_state.peak_value, 10000), COALESCE(latest_state.portfolio_value, 10000) + pnl) > 0
      THEN (
        (GREATEST(COALESCE(latest_state.peak_value, 10000), COALESCE(latest_state.portfolio_value, 10000) + pnl)
          - (COALESCE(latest_state.portfolio_value, 10000) + pnl))
        / GREATEST(COALESCE(latest_state.peak_value, 10000), COALESCE(latest_state.portfolio_value, 10000) + pnl)
      ) * 100
      ELSE 0
    END;

  RETURN jsonb_build_object(
    'ticker', s.ticker,
    'outcome', p_outcome,
    'shares_sold', shares_sold,
    'allocated_dollars_sold', dollars_sold,
    'realized_pnl', pnl,
    'remaining_shares', CASE WHEN split_fraction = 1 THEN 0 ELSE COALESCE(s.max_shares, 0) - shares_sold END,
    'remaining_allocated_dollars', CASE WHEN split_fraction = 1 THEN 0 ELSE COALESCE(s.allocated_dollars, 0) - dollars_sold END
  );
END;
$function$;

NOTIFY pgrst, 'reload schema';
