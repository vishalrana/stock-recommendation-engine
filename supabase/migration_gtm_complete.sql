-- migration_gtm_complete.sql
-- Run this in the Supabase SQL Editor to complete the GTM persistence database setup.
-- IMPORTANT: This must be executed manually in the Supabase Dashboard > SQL Editor.

-- ============================================================
-- STEP 1: Add missing columns to signals table
-- ============================================================
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_date DATE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS exit_date DATE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_signal BOOLEAN DEFAULT FALSE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_signal_reason TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_price NUMERIC;

-- Context breakdown columns
ALTER TABLE signals ADD COLUMN IF NOT EXISTS context_analyst NUMERIC DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS context_earnings NUMERIC DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS context_news NUMERIC DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS context_fundamental NUMERIC DEFAULT 0;

-- ============================================================
-- STEP 2: Create portfolio_state table
-- ============================================================
CREATE TABLE IF NOT EXISTS portfolio_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    portfolio_value NUMERIC NOT NULL,
    peak_value NUMERIC NOT NULL,
    current_drawdown_pct NUMERIC DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- STEP 3: Recreate recommendations view with ALL columns
-- ============================================================
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
  s.target_1,
  s.target_2,
  s.target_3,
  s.target_1_pct,
  s.target_2_pct,
  s.target_3_pct,
  s.weighted_rr,
  s.position_sizing,
  s.narrative,
  s.tier_label,
  s.is_fallback,
  s.current_rsi,
  s.volume_ratio,
  s.adx_value,
  s.macd_histogram,
  s.rsi_min_10d,
  s.ema20,
  s.score,
  s.composite_score,
  s.quality_score,
  s.strategy,
  s.strategy_name,
  s.regime,
  s.context_score,
  s.is_momentum_exception,
  s.distance_from_high_pct,
  -- GTM persistence columns
  s.entry_date,
  s.exit_date,
  s.status,
  s.sell_signal,
  s.sell_signal_reason,
  s.sell_price,
  -- Context breakdown columns
  s.context_analyst,
  s.context_earnings,
  s.context_news,
  s.context_fundamental,
  -- Joined metrics
  COALESCE(m.win_rate, 0) AS past_win_rate,
  COALESCE(m.wins + m.losses, 0) AS total_trades,
  COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
  COALESCE(m.wins, 0) AS wins,
  COALESCE(m.losses, 0) AS losses
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;

-- ============================================================
-- STEP 4: Backfill existing signals with defaults
-- ============================================================
UPDATE signals SET status = 'open' WHERE status IS NULL;
UPDATE signals SET sell_signal = FALSE WHERE sell_signal IS NULL;
UPDATE signals SET entry_date = (
  CASE 
    WHEN EXTRACT(DOW FROM scan_date::date) = 5 THEN scan_date::date + INTERVAL '3 days'
    WHEN EXTRACT(DOW FROM scan_date::date) = 6 THEN scan_date::date + INTERVAL '2 days'
    ELSE scan_date::date + INTERVAL '1 day'
  END
)::date WHERE entry_date IS NULL AND scan_date IS NOT NULL;

-- Force PostgREST schema cache reload
NOTIFY pgrst, 'reload schema';
