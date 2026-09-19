-- ============================================================
-- Complete Production Schema Synchronization Migration
-- ============================================================
-- Purpose:
-- Brings the production Supabase schema into 100% parity with the current
-- repository recommendation engine architecture:
-- 1. signals table: adds missing strategy, ATR targets, and reach probability columns
-- 2. signals_history table: adds missing lifecycle, rejection, ATR targets, and reach probability columns
-- 3. Ensures exact instance identity (signals_history.signal_id)
-- 4. Reloads PostgREST schema cache
--
-- This migration is completely non-destructive (adds columns and drops legacy constraints only).
-- ============================================================

-- 1. signals table: add missing strategy & analytical columns
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS target_1_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_2_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_3_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t1 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t2 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t3 NUMERIC,
    ADD COLUMN IF NOT EXISTS scale_out_weights TEXT NOT NULL DEFAULT '50/30/20',
    ADD COLUMN IF NOT EXISTS weighted_rr_honest NUMERIC,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

-- 2. signals_history table: add missing lifecycle, rejection, & analytical columns
ALTER TABLE signals_history
    ADD COLUMN IF NOT EXISTS sell_signal_reason TEXT,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
    ADD COLUMN IF NOT EXISTS target_1_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_2_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_3_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t1 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t2 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t3 NUMERIC,
    ADD COLUMN IF NOT EXISTS scale_out_weights TEXT NOT NULL DEFAULT '50/30/20',
    ADD COLUMN IF NOT EXISTS weighted_rr_honest NUMERIC;

-- 3. Ensure instance uniqueness constraints
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_history_signal_id_uniq
ON signals_history(signal_id)
WHERE signal_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_active_ticker
ON signals(ticker)
WHERE status IN ('open', 'pending');

-- 4. Ensure legacy composite uniqueness on (scan_date, ticker) is dropped
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_scan_date_ticker_key;
ALTER TABLE signals_history DROP CONSTRAINT IF EXISTS signals_history_scan_date_ticker_key;

-- 5. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
