-- =========================================================================
-- Migration: Clean Recommendation Engine Baseline
-- Architecture: Instance-Based Recommendation Engine Only
-- =========================================================================
-- Purpose:
-- 1. Ensure signals_history metric columns (past_win_rate, expectancy_pct,
--    total_trades) default to NULL instead of 0, preserving unavailable semantics.
-- 2. Cleanly isolate and decouple obsolete portfolio sizing columns
--    (allocated_dollars, exact_shares, max_shares, position_sizing).
-- =========================================================================

-- 1. Drop 0 defaults for historical metrics in signals_history
ALTER TABLE signals_history 
    ALTER COLUMN past_win_rate DROP DEFAULT,
    ALTER COLUMN past_win_rate SET DEFAULT NULL;

ALTER TABLE signals_history 
    ALTER COLUMN expectancy_pct DROP DEFAULT,
    ALTER COLUMN expectancy_pct SET DEFAULT NULL;

ALTER TABLE signals_history 
    ALTER COLUMN total_trades DROP DEFAULT,
    ALTER COLUMN total_trades SET DEFAULT NULL;

-- 2. Nullify legacy sizing defaults in signals and signals_history
ALTER TABLE signals 
    ALTER COLUMN allocated_dollars SET DEFAULT NULL,
    ALTER COLUMN exact_shares SET DEFAULT NULL,
    ALTER COLUMN max_shares SET DEFAULT NULL,
    ALTER COLUMN position_sizing SET DEFAULT NULL;

ALTER TABLE signals_history 
    ALTER COLUMN allocated_dollars SET DEFAULT NULL,
    ALTER COLUMN exact_shares SET DEFAULT NULL,
    ALTER COLUMN max_shares SET DEFAULT NULL,
    ALTER COLUMN position_sizing SET DEFAULT NULL;

-- 3. Confirm exact instance identity uniqueness constraint on signals_history
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_history_signal_id_uniq
ON signals_history (signal_id)
WHERE signal_id IS NOT NULL;
