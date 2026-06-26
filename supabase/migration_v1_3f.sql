-- Migration v1.3f: Update recommendations view to filter to Strong Buy and Buy only
-- Run this in the Supabase SQL Editor

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
    COALESCE(m.win_rate, 0) AS past_win_rate,
    COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
    COALESCE(m.total_signals, 0) AS historical_signals,
    COALESCE(m.median_win_return, 0) AS median_win_return
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker
WHERE s.tier_label IN ('Strong Buy', 'Buy');
