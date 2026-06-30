-- migration_position_persistence.sql
-- Run this in the Supabase SQL Editor to add persistence columns.

ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_date DATE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS exit_date DATE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_signal_date DATE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_price NUMERIC;

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
  s.context_score,
  s.entry_date,
  s.exit_date,
  s.status,
  s.sell_signal_date,
  s.sell_price,
  COALESCE(m.win_rate, 0) AS past_win_rate,
  COALESCE(m.wins + m.losses, 0) AS total_trades,
  COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
  COALESCE(m.wins, 0) AS wins,
  COALESCE(m.losses, 0) AS losses,
  COALESCE(m.wins, 0) AS past_wins,
  COALESCE(m.losses, 0) AS past_losses
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;

-- Force PostgREST schema cache reload
NOTIFY pgrst, 'reload schema';
