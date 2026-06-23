-- ============================================================
-- Migration v1.3b — RSI Breadth Monitor
-- Run once in Supabase SQL Editor
-- ============================================================

-- Add rsi_breadth_pct to scan_log
-- Tracks the percentage of scanned tickers that passed the RSI
-- pullback-recovery gate (RSI dip < threshold then recovered).
-- Useful for monitoring whether the market is breadth-expanding
-- (many stocks recovering) or breadth-contracting (few qualify).
ALTER TABLE scan_log
    ADD COLUMN IF NOT EXISTS rsi_breadth_pct NUMERIC(5, 1) DEFAULT NULL;

COMMENT ON COLUMN scan_log.rsi_breadth_pct IS
    'Percentage of scanned tickers that passed the RSI pullback-recovery gate. '
    'Computed as: (tickers that passed trend+RSI gates / total scanned) * 100. '
    'A value below 5% in a bull regime suggests RSI thresholds may still be too strict.';
