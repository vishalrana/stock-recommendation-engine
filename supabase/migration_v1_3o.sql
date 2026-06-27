-- Add multi-target columns to signals
ALTER TABLE signals 
  ADD COLUMN IF NOT EXISTS target_1 DECIMAL(10,2),
  ADD COLUMN IF NOT EXISTS target_2 DECIMAL(10,2),
  ADD COLUMN IF NOT EXISTS target_3 DECIMAL(10,2),
  ADD COLUMN IF NOT EXISTS target_1_pct DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS target_2_pct DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS target_3_pct DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS weighted_rr DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS position_sizing VARCHAR(10) DEFAULT '50/30/20',
  ADD COLUMN IF NOT EXISTS narrative TEXT;

-- Same for signals_history
ALTER TABLE signals_history 
  ADD COLUMN IF NOT EXISTS target_1 DECIMAL(10,2),
  ADD COLUMN IF NOT EXISTS target_2 DECIMAL(10,2),
  ADD COLUMN IF NOT EXISTS target_3 DECIMAL(10,2),
  ADD COLUMN IF NOT EXISTS target_1_pct DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS target_2_pct DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS target_3_pct DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS weighted_rr DECIMAL(5,2),
  ADD COLUMN IF NOT EXISTS position_sizing VARCHAR(10) DEFAULT '50/30/20',
  ADD COLUMN IF NOT EXISTS narrative TEXT;

-- Drop NOT NULL from exit_price for backward compatibility
ALTER TABLE signals ALTER COLUMN exit_price DROP NOT NULL;
ALTER TABLE signals_history ALTER COLUMN exit_price DROP NOT NULL;

-- Recreate recommendations view with new columns
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
  s.target_1,
  s.target_2,
  s.target_3,
  s.target_1_pct,
  s.target_2_pct,
  s.target_3_pct,
  s.weighted_rr,
  s.position_sizing,
  s.upside_pct,
  s.risk_reward,
  s.current_rsi,
  s.volume_ratio,
  s.adx_value,
  s.macd_histogram,
  s.ema20,
  s.composite_score,
  s.tier_label,
  s.is_fallback,
  s.narrative,
  COALESCE(m.win_rate, 0) AS past_win_rate,
  COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
  COALESCE(m.total_signals, 0) AS historical_signals,
  COALESCE(m.total_trades, 0) AS total_trades
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker
WHERE s.tier_label IN ('Strong Buy', 'Buy');
