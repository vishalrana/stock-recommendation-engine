-- Migration v1.3m: Combined Strategy 1.3 Rev B quality gates DDL + View updates
-- Run this in the Supabase SQL Editor

-- 1. Scan log gate columns
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_minrisk_gate INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_maxgap_gate INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_earnings_gate INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS momentum_exceptions INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_extended_high_gate INT DEFAULT 0;

-- 2. Signal & History columns
ALTER TABLE signals ADD COLUMN IF NOT EXISTS earnings_date DATE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS earnings_date DATE;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS is_momentum_exception BOOLEAN DEFAULT FALSE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS is_momentum_exception BOOLEAN DEFAULT FALSE;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS distance_from_high_pct DECIMAL(5,2);
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS distance_from_high_pct DECIMAL(5,2);

-- 3. Recreate Recommendations View to include the new columns
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
    s.volume_ratio,
    s.adx_value,
    s.macd_histogram,
    s.ema20,
    s.composite_score,
    s.tier_label,
    s.is_fallback,
    s.is_momentum_exception,
    s.distance_from_high_pct,
    COALESCE(m.win_rate, 0) AS past_win_rate,
    COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
    COALESCE(m.total_signals, 0) AS historical_signals,
    COALESCE(m.median_win_return, 0) AS median_win_return
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker
WHERE s.tier_label IN ('Strong Buy', 'Buy');

-- 4. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
