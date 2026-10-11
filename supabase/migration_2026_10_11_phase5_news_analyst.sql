-- Migration (2026-10-11): Phase 5 News, LLM Labels, and Analyst Archive
-- Isolated shadow layer tables: news_items, news_labels, analyst_snapshots

CREATE TABLE IF NOT EXISTS news_items (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT,
    url TEXT,
    items TEXT[],
    accepted_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    text_hash TEXT NOT NULL,
    raw_text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_items_ticker_date ON news_items (ticker, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_news_items_hash ON news_items (text_hash);

CREATE TABLE IF NOT EXISTS news_labels (
    id BIGSERIAL PRIMARY KEY,
    text_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    event_type TEXT,
    direction INTEGER,
    materiality INTEGER,
    one_line_reason TEXT,
    evidence_quote TEXT,
    raw_output JSONB,
    is_valid BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_labels_hash ON news_labels (text_hash);

CREATE TABLE IF NOT EXISTS analyst_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    source TEXT NOT NULL,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT analyst_snapshots_unique UNIQUE (ticker, as_of_date, source)
);

CREATE INDEX IF NOT EXISTS idx_analyst_snapshots_ticker ON analyst_snapshots (ticker, as_of_date);

-- Enable RLS and public read
ALTER TABLE news_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_labels ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyst_snapshots ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'news_items' AND policyname = 'Allow public read on news_items'
    ) THEN
        CREATE POLICY "Allow public read on news_items" ON news_items FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'news_labels' AND policyname = 'Allow public read on news_labels'
    ) THEN
        CREATE POLICY "Allow public read on news_labels" ON news_labels FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'analyst_snapshots' AND policyname = 'Allow public read on analyst_snapshots'
    ) THEN
        CREATE POLICY "Allow public read on analyst_snapshots" ON analyst_snapshots FOR SELECT USING (true);
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
