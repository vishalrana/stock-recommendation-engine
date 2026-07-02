-- Migration: Widen ticker and position_sizing columns to prevent value too long errors
-- Step 1: Drop the dependent view first
DROP VIEW IF EXISTS recommendations;

-- Step 2: Alter the column types
ALTER TABLE signals ALTER COLUMN ticker TYPE VARCHAR(30);
ALTER TABLE signals ALTER COLUMN position_sizing TYPE VARCHAR(30);
ALTER TABLE signals_history ALTER COLUMN ticker TYPE VARCHAR(30);
ALTER TABLE signals_history ALTER COLUMN position_sizing TYPE VARCHAR(30);

-- Step 3: Recreate recommendations view
CREATE OR REPLACE VIEW recommendations AS
SELECT
  s.scan_date,
  s.ticker,
  s.company_name,
  s.industry,
  s.price,
  s.entry_price,
  s.stop_loss,
  s.exit_price,
  s.upside_pct,
  s.risk_reward,
  s.target_1,
  s.target_2,
  s.target_3,
  s.target_1_pct,
  s.target_2_pct,
  s.target_3_pct,
  s.weighted_rr,
  s.position_sizing,
  s.narrative,
  s.tier_label,
  s.is_fallback,
  s.current_rsi,
  s.volume_ratio,
  s.adx_value,
  s.macd_histogram,
  s.rsi_min_10d,
  s.ema20,
  s.score,
  s.composite_score,
  s.quality_score,
  s.strategy,
  s.strategy_name,
  s.regime,
  s.context_score,
  s.is_momentum_exception,
  s.distance_from_high_pct,
  s.entry_date,
  s.exit_date,
  s.status,
  s.sell_signal,
  s.sell_signal_reason,
  s.sell_price,
  s.context_analyst,
  s.context_earnings,
  s.context_news,
  s.context_fundamental,
  s.allocated_dollars,
  s.max_shares,
  COALESCE(m.win_rate, 0) AS past_win_rate,
  COALESCE(m.wins + m.losses, 0) AS total_trades,
  COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
  COALESCE(m.wins, 0) AS wins,
  COALESCE(m.losses, 0) AS losses
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;

NOTIFY pgrst, 'reload schema';
