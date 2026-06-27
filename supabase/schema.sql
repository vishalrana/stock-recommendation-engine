-- ============================================================
-- Stock Recommendation Engine — Supabase Schema
-- Strategy 1.1 Beta
-- ============================================================
-- Run this entire file in the Supabase SQL Editor (single execution).
-- ============================================================


-- =========================
-- TABLE 1: signals
-- =========================
-- Daily qualified recommendations. Written by the nightly job.
-- The frontend reads these to show today's picks.

CREATE TABLE signals (
    id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    scan_date     DATE NOT NULL,
    ticker        TEXT NOT NULL,
    company_name  TEXT,
    industry      TEXT,
    price         NUMERIC(10,2),
    entry_price   NUMERIC(10,2) NOT NULL,
    stop_loss     NUMERIC(10,2) NOT NULL,
    exit_price    NUMERIC(10,2) NOT NULL,
    upside_pct    NUMERIC(6,2),
    risk_reward   NUMERIC(5,2),
    current_rsi   NUMERIC(5,2),
    volume_ratio  NUMERIC(5,2),
    score         NUMERIC(8,4),
    created_at    TIMESTAMPTZ DEFAULT now(),

    UNIQUE (scan_date, ticker)
);

CREATE INDEX idx_signals_scan_date ON signals (scan_date DESC);


-- =========================
-- TABLE 2: ticker_metrics
-- =========================
-- Per-ticker historical backtest metrics. Seeded once from cached data.
-- Provides "Past Win Rate" and "Holding Time" columns on the frontend.

CREATE TABLE ticker_metrics (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticker              TEXT NOT NULL UNIQUE,
    industry            TEXT,
    total_signals       INTEGER DEFAULT 0,
    wins                INTEGER DEFAULT 0,
    losses              INTEGER DEFAULT 0,
    win_rate            NUMERIC(6,2) DEFAULT 0,
    expectancy_pct      NUMERIC(8,4) DEFAULT 0,
    median_holding_days NUMERIC(6,1) DEFAULT 0,
    updated_at          TIMESTAMPTZ DEFAULT now()
);


-- =========================
-- TABLE 3: scan_log
-- =========================
-- Audit trail for nightly runs. One row per scan date.

CREATE TABLE scan_log (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    scan_date           DATE NOT NULL UNIQUE,
    tickers_scanned     INTEGER NOT NULL,
    signals_generated   INTEGER NOT NULL,
    scan_duration_secs  NUMERIC(8,2),
    status              TEXT DEFAULT 'success',
    error_message       TEXT,
    active_strategies   INTEGER DEFAULT 0,
    skipped_strategies  JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT now()
);


-- =========================
-- VIEW: recommendations
-- =========================
-- Joins today's signals with historical metrics.
-- This is the single query the frontend uses.

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
    s.score,
    COALESCE(m.win_rate, 0)            AS past_win_rate,
    COALESCE(m.expectancy_pct, 0)      AS expectancy_pct,
    COALESCE(m.total_signals, 0)       AS historical_signals,
    COALESCE(m.wins, 0)                AS historical_wins,
    COALESCE(m.losses, 0)              AS historical_losses,
    COALESCE(m.median_holding_days, 0) AS median_holding_days
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;


-- =========================
-- ROW-LEVEL SECURITY
-- =========================
-- Public anonymous reads. Writes only via service_role key.

ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read_signals" ON signals
    FOR SELECT TO anon USING (true);

ALTER TABLE ticker_metrics ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read_metrics" ON ticker_metrics
    FOR SELECT TO anon USING (true);

ALTER TABLE scan_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read_scan_log" ON scan_log
    FOR SELECT TO anon USING (true);


-- ============================================================
-- DONE. Verify in Table Editor:
--   - signals, ticker_metrics, scan_log tables exist
--   - recommendations view exists (under Database > Views)
-- ============================================================
