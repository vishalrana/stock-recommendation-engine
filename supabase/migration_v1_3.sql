-- Migration v1.3 — Strategy 1.3 Rev A additions
-- Run this once in Supabase SQL Editor

-- Add new indicator columns to signals
ALTER TABLE signals
  ADD COLUMN IF NOT EXISTS adx_value      FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS macd_histogram FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS rsi_min_10d    FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS ema20          FLOAT DEFAULT NULL;

-- Mirror new columns in signals_history
ALTER TABLE signals_history
  ADD COLUMN IF NOT EXISTS adx_value      FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS macd_histogram FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS rsi_min_10d    FLOAT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS ema20          FLOAT DEFAULT NULL;

-- Update scan_log to capture gate rejection breakdown
ALTER TABLE scan_log
  ADD COLUMN IF NOT EXISTS failed_rsi_gate      INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_adx_gate      INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_macd_gate     INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_trend_gate    INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_volume_gate   INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_rr_gate       INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_trades_gate   INT DEFAULT 0;

-- Update the recommendations view to include new columns
DROP VIEW IF EXISTS recommendations;

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
    s.current_rsi,
    s.rsi_min_10d,
    s.volume_ratio,
    s.adx_value,
    s.macd_histogram,
    s.ema20,
    s.score,
    s.regime,
    s.composite_score,
    s.tier_label,
    COALESCE(m.win_rate, 0)            AS past_win_rate,
    COALESCE(m.expectancy_pct, 0)      AS expectancy_pct,
    COALESCE(m.total_signals, 0)       AS historical_signals,
    COALESCE(m.wins, 0)                AS historical_wins,
    COALESCE(m.losses, 0)              AS historical_losses,
    COALESCE(m.median_holding_days, 0) AS median_holding_days
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;
