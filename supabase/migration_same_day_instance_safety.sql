-- ============================================================
-- Same-Day Recommendation Instance Safety Migration
-- ============================================================
-- Purpose:
-- Allows a stock to be recommended as a brand-new instance if it qualifies
-- again on the same day after being manually removed or closed, while:
-- 1. Ensuring at most ONE active recommendation per ticker in `signals`
-- 2. Ensuring exact recommendation instance identity via `signal_id` in `signals_history`
-- 3. Permanently preserving historical recommendation outcomes without overwriting
--
-- This migration is non-destructive (no data is deleted or modified).
-- ============================================================

-- 1. Ensure signal_id index is unique when present in signals_history
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_history_signal_id_uniq
ON signals_history(signal_id)
WHERE signal_id IS NOT NULL;

-- 2. Ensure at most one ACTIVE ('open' or 'pending') recommendation per ticker in signals
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_active_ticker
ON signals(ticker)
WHERE status IN ('open', 'pending');

-- 3. Transition from legacy (scan_date, ticker) constraint to instance-based identity:
-- Note: Dropping these constraints allows a new recommendation to be created on the same day
-- if an earlier recommendation for that ticker was closed or manually removed.
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_scan_date_ticker_key;
ALTER TABLE signals_history DROP CONSTRAINT IF EXISTS signals_history_scan_date_ticker_key;

-- 4. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
