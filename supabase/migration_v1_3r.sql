-- Add strategy tracking
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS quality_score FLOAT;

ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy TEXT;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS quality_score FLOAT;

-- Ensure ticker_metrics has wins/losses columns
ALTER TABLE ticker_metrics ADD COLUMN IF NOT EXISTS wins INT DEFAULT 0;
ALTER TABLE ticker_metrics ADD COLUMN IF NOT EXISTS losses INT DEFAULT 0;

-- Track guardrail-blocked signals in scan_log
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS signals_blocked INT DEFAULT 0;

-- Recreate recommendations view with correct track record columns
DROP VIEW IF EXISTS recommendations;
CREATE VIEW recommendations AS
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
  s.narrative,
  s.tier_label,
  s.is_fallback,
  s.current_rsi,
  s.volume_ratio,
  s.adx_value,
  s.macd_histogram,
  s.ema20,
  s.composite_score,
  s.quality_score,
  s.strategy,
  COALESCE(m.win_rate, 0) AS past_win_rate,
  COALESCE(m.wins + m.losses, 0) AS total_trades,
  COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
  COALESCE(m.wins, 0) AS wins,
  COALESCE(m.losses, 0) AS losses,
  COALESCE(m.wins, 0) AS past_wins,
  COALESCE(m.losses, 0) AS past_losses
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker
WHERE s.tier_label IN ('Strong Buy', 'Buy');

-- Force PostgREST schema cache reload
NOTIFY pgrst, 'reload schema';
