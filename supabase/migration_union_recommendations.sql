-- Migration: Redefine recommendations view as union of active signals and signals_history
-- Run this in the Supabase SQL Editor

DROP VIEW IF EXISTS recommendations;

CREATE OR REPLACE VIEW recommendations AS
WITH unified_signals AS (
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
    s.max_shares
  FROM signals s
  UNION ALL
  SELECT
    h.scan_date,
    h.ticker,
    h.company_name,
    h.industry,
    h.price,
    h.entry_price,
    h.stop_loss,
    h.exit_price,
    h.upside_pct,
    h.risk_reward,
    h.target_1,
    h.target_2,
    h.target_3,
    h.target_1_pct,
    h.target_2_pct,
    h.target_3_pct,
    h.weighted_rr,
    h.position_sizing,
    h.narrative,
    h.tier_label,
    h.is_fallback,
    h.current_rsi,
    h.volume_ratio,
    h.adx_value,
    h.macd_histogram,
    h.rsi_min_10d,
    h.ema20,
    h.score,
    h.composite_score,
    h.quality_score,
    h.strategy,
    h.strategy_name,
    h.regime,
    h.context_score,
    h.is_momentum_exception,
    h.distance_from_high_pct,
    h.scan_date AS entry_date,
    h.outcome_date AS exit_date,
    CASE 
      WHEN h.outcome = 'open' THEN 'open'
      WHEN h.outcome IN ('stopped', 'stop_loss') THEN 'closed'
      WHEN h.outcome IN ('hit_t3', 'hit_t2', 'hit_t1', 'closed') THEN 'closed'
      ELSE h.outcome
    END AS status,
    TRUE AS sell_signal,
    CASE 
      WHEN h.outcome = 'stopped' THEN 'Stop loss hit'
      WHEN h.outcome = 'hit_t3' THEN 'Target 3 hit – full exit'
      WHEN h.outcome = 'hit_t2' THEN 'Target 2 hit – sell 30%'
      WHEN h.outcome = 'hit_t1' THEN 'Target 1 hit – sell 50%'
      ELSE 'Closed'
    END AS sell_signal_reason,
    h.exit_price AS sell_price,
    0.0 AS context_analyst,
    0.0 AS context_earnings,
    0.0 AS context_news,
    0.0 AS context_fundamental,
    h.allocated_dollars,
    h.max_shares
  FROM signals_history h
  WHERE h.outcome != 'open'
)
SELECT
  u.*,
  COALESCE(m.win_rate, 0) AS past_win_rate,
  COALESCE(m.wins + m.losses, 0) AS total_trades,
  COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
  COALESCE(m.wins, 0) AS wins,
  COALESCE(m.losses, 0) AS losses
FROM unified_signals u
LEFT JOIN ticker_metrics m ON u.ticker = m.ticker;

-- Force PostgREST schema cache reload
NOTIFY pgrst, 'reload schema';
