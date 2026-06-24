-- ============================================================
-- Migration v1.3c — Strategy 1.3 Rev B prerequisites
-- Run once in Supabase SQL Editor before the first Rev B scan.
-- Safe to run multiple times (uses IF NOT EXISTS / IF EXISTS).
-- ============================================================

-- ── 1. Unique constraint on signals_history ─────────────────
-- Prevents duplicate (scan_date, ticker) rows when the archive
-- step is retried or runs more than once in the same day.
ALTER TABLE signals_history
    ADD CONSTRAINT IF NOT EXISTS signals_history_scan_date_ticker_key
    UNIQUE (scan_date, ticker);

-- ── 2. is_fallback column on signals ────────────────────────
-- Marks rows produced by the Extended Bull Fallback re-scan.
-- Fallback signals are capped at tier "Watch".
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN DEFAULT FALSE;

-- ── 3. is_fallback column on signals_history ────────────────
ALTER TABLE signals_history
    ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN DEFAULT FALSE;

-- ── 4. rsi_breadth_pct on scan_log ──────────────────────────
-- Percentage of scanned tickers that passed the RSI
-- pullback-recovery gate. Used to decide whether to trigger
-- the Extended Bull Fallback (threshold: < 25%).
ALTER TABLE scan_log
    ADD COLUMN IF NOT EXISTS rsi_breadth_pct NUMERIC(5, 1) DEFAULT NULL;

-- Verification: uncomment to confirm columns exist after running.
-- SELECT column_name, data_type
-- FROM information_schema.columns
-- WHERE table_name IN ('signals', 'signals_history', 'scan_log')
--   AND column_name IN ('is_fallback', 'rsi_breadth_pct')
-- ORDER BY table_name, column_name;
