-- ============================================================
-- Strategy 1.2 Migration
-- Run this in the Supabase SQL Editor AFTER the base schema.
-- All statements are idempotent (safe to re-run).
-- ============================================================


-- =========================
-- 1. Extend scan_log
-- =========================
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS regime VARCHAR(10);
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS signals_qualified INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS signals_recommended INT DEFAULT 0;


-- =========================
-- 2. Extend signals
-- =========================
ALTER TABLE signals ADD COLUMN IF NOT EXISTS regime VARCHAR(10);


-- =========================
-- 3. Create signals_history
-- =========================
CREATE TABLE IF NOT EXISTS signals_history (
    id              BIGSERIAL PRIMARY KEY,
    scan_date       DATE NOT NULL,
    ticker          VARCHAR(10) NOT NULL,
    company_name    VARCHAR(150),
    industry        VARCHAR(100),
    price           NUMERIC(10,2),
    entry_price     NUMERIC(10,2) NOT NULL,
    stop_loss       NUMERIC(10,2) NOT NULL,
    exit_price      NUMERIC(10,2) NOT NULL,
    upside_pct      NUMERIC(8,2),
    risk_reward     NUMERIC(5,2),
    current_rsi     NUMERIC(5,2),
    volume_ratio    NUMERIC(8,2),
    score           NUMERIC(8,4),
    past_win_rate   NUMERIC(5,2),
    expectancy_pct  NUMERIC(8,4),
    total_trades    INT,
    regime          VARCHAR(10),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_history_scan_date ON signals_history(scan_date);
CREATE INDEX IF NOT EXISTS idx_signals_history_ticker ON signals_history(ticker);


-- =========================
-- 4. RLS for signals_history
-- =========================
ALTER TABLE signals_history ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'signals_history' AND policyname = 'anon_read_signals_history'
    ) THEN
        CREATE POLICY "anon_read_signals_history" ON signals_history
            FOR SELECT TO anon USING (true);
    END IF;
END
$$;


-- =========================
-- 5. Add Strategy 1.2 Rev B Columns
-- =========================
ALTER TABLE signals ADD COLUMN IF NOT EXISTS composite_score FLOAT DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS tier_label TEXT DEFAULT 'Speculative';

ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS composite_score FLOAT DEFAULT 0;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS tier_label TEXT DEFAULT 'Speculative';


-- =========================
-- 6. Update recommendations view
-- =========================
-- Drop and recreate to add new columns.
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
    s.score,
    s.regime,
    s.composite_score,
    s.tier_label,
    COALESCE(m.win_rate, 0)            AS past_win_rate,
    COALESCE(m.expectancy_pct, 0)      AS expectancy_pct,
    COALESCE(m.total_signals, 0)       AS historical_signals,
    COALESCE(m.wins, 0)                AS historical_wins,
    COALESCE(m.losses, 0)              AS historical_losses,
    COALESCE(m.median_holding_days, 0) AS median_holding_days
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;


-- ============================================================
-- DONE. Verify in Table Editor:
--   - scan_log has columns: regime, signals_qualified, signals_recommended
--   - signals has columns: regime, composite_score, tier_label
--   - signals_history table exists with indexes and columns: composite_score, tier_label
--   - recommendations view includes regime, composite_score, tier_label
-- ============================================================

