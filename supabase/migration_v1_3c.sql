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

-- ── 5. Recreate recommendations view with is_fallback ───────
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
    s.is_fallback,
    COALESCE(m.win_rate, 0)            AS past_win_rate,
    COALESCE(m.expectancy_pct, 0)      AS expectancy_pct,
    COALESCE(m.total_signals, 0)       AS historical_signals,
    COALESCE(m.wins, 0)                AS historical_wins,
    COALESCE(m.losses, 0)              AS historical_losses,
    COALESCE(m.median_holding_days, 0) AS median_holding_days
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;

-- Verification: confirm columns exist after running.
-- SELECT column_name, data_type
-- FROM information_schema.columns
-- WHERE table_name IN ('signals', 'signals_history', 'scan_log')
--   AND column_name IN ('is_fallback', 'rsi_breadth_pct')
-- ORDER BY table_name, column_name;

