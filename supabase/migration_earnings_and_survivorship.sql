-- Migration: Earnings Calendar Risk Filter & Survivorship Bias Mitigation
-- Adds earnings_calendar, delisted_tickers, and new audit columns on signals / signals_history

-- 1. Earnings Calendar Table (cached daily)
CREATE TABLE IF NOT EXISTS earnings_calendar (
    ticker TEXT PRIMARY KEY,
    next_earnings_date DATE,
    last_earnings_date DATE,
    fiscal_period TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Delisted Tickers Registry
CREATE TABLE IF NOT EXISTS delisted_tickers (
    ticker TEXT PRIMARY KEY,
    company_name TEXT,
    delisted_date DATE,
    reason TEXT,
    sector TEXT,
    final_price NUMERIC,
    peak_price_1yr_prior NUMERIC
);

-- 3. Add Risk Columns to signals table
ALTER TABLE signals ADD COLUMN IF NOT EXISTS next_earnings_date DATE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS days_to_earnings INTEGER;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS earnings_rejected BOOLEAN DEFAULT FALSE;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reach_prob_adjusted NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reach_prob_raw NUMERIC;

-- 4. Add Risk Columns to signals_history table
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS next_earnings_date DATE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS days_to_earnings INTEGER;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS earnings_rejected BOOLEAN DEFAULT FALSE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reach_prob_adjusted NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reach_prob_raw NUMERIC;

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
