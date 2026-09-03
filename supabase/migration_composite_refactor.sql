-- Migration: Composite Scoring Engine Refactor
-- Adds exact_shares (canonical broker sizing), de_ratio, current_ratio, earnings_surprise_pct, and finbert_sentiment

ALTER TABLE signals ADD COLUMN IF NOT EXISTS exact_shares NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS de_ratio NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS current_ratio NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS earnings_surprise_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS finbert_sentiment NUMERIC;

ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS exact_shares NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS de_ratio NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS current_ratio NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS earnings_surprise_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS finbert_sentiment NUMERIC;

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
