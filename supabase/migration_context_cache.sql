-- Migration: Create context_cache table
-- Description: Stores NLP context scores and metadata for tickers to reuse for 24h.

CREATE TABLE IF NOT EXISTS public.context_cache (
    ticker TEXT PRIMARY KEY,
    date DATE NOT NULL,
    context_score DOUBLE PRECISION NOT NULL,
    analyst_target DOUBLE PRECISION,
    news_sentiment DOUBLE PRECISION,
    earnings_surprise DOUBLE PRECISION,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Index for date-based lookups
CREATE INDEX IF NOT EXISTS idx_context_cache_date ON public.context_cache (date);

-- Reload PostgREST schema cache to make the new table visible
NOTIFY pgrst, 'reload schema';
