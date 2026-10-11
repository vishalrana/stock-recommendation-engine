-- Migration (2026-10-11): Swing Signals Program Tables
-- Creates dedicated tables for Swing Signals (isolated from Ideas 'signals' table):
--   1. swing_signals: Open, closed, and pending swing signals across sleeves T, E, N, P, I
--   2. swing_events: Point-in-time catalysts, 8-K filings, earnings reactions, insider clusters
--   3. swing_sleeve_status: Live vs shadow status and kill rule states for each sleeve
--   4. swing_ledger: Immutable forward audit ledger of daily actions (BUY/SELL)

CREATE TABLE IF NOT EXISTS swing_signals (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    sleeve TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- 'open', 'closed', 'cancelled'
    signal_date DATE NOT NULL,
    entry_signal_close NUMERIC NOT NULL,
    entry_fill_price NUMERIC,
    fill_date DATE,
    stop_loss NUMERIC NOT NULL,
    target NUMERIC,
    exit_date DATE,
    exit_price NUMERIC,
    exit_reason TEXT,
    holding_sessions INTEGER DEFAULT 0,
    net_return_pct NUMERIC,
    spy_return_pct NUMERIC,
    excess_pct NUMERIC,
    beta_adj_pct NUMERIC,
    beta NUMERIC,
    atr14 NUMERIC,
    c6_pct NUMERIC,
    ear_pct NUMERIC,
    avol_pct NUMERIC,
    cluster_value NUMERIC,
    gap_pct NUMERIC,
    rsi2 NUMERIC,
    earnings_date DATE,
    earnings_unknown BOOLEAN DEFAULT FALSE,
    event_lock_unknown BOOLEAN DEFAULT FALSE,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT swing_signals_sleeve_ticker_date_unique UNIQUE (sleeve, ticker, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_swing_signals_status ON swing_signals (status);
CREATE INDEX IF NOT EXISTS idx_swing_signals_signal_date ON swing_signals (signal_date);
CREATE INDEX IF NOT EXISTS idx_swing_signals_ticker ON swing_signals (ticker);
CREATE INDEX IF NOT EXISTS idx_swing_signals_sleeve ON swing_signals (sleeve);

CREATE TABLE IF NOT EXISTS swing_events (
    id BIGSERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    form TEXT,
    items TEXT[],
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_swing_events_ticker_date ON swing_events (ticker, event_date);

CREATE TABLE IF NOT EXISTS swing_sleeve_status (
    sleeve TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SHADOW',  -- 'LIVE', 'LIVE-SP500', 'SHADOW'
    universe TEXT NOT NULL DEFAULT 'shadow',
    u2_beta_adj NUMERIC,
    u1_beta_adj NUMERIC,
    kill_status TEXT DEFAULT 'NORMAL',      -- 'NORMAL', 'KILLED_K3', 'KILLED_K12', 'KILLED_KDD'
    reasons TEXT[],
    last_evaluated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS swing_ledger (
    id BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL,
    action TEXT NOT NULL,  -- 'BUY', 'SELL'
    ticker TEXT NOT NULL,
    sleeve TEXT NOT NULL,
    price NUMERIC NOT NULL,
    units NUMERIC DEFAULT 1.0,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_swing_ledger_date ON swing_ledger (session_date);

-- Enable RLS and grant read access to anon & authenticated, full access to service_role
ALTER TABLE swing_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE swing_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE swing_sleeve_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE swing_ledger ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'swing_signals' AND policyname = 'Allow public read on swing_signals'
    ) THEN
        CREATE POLICY "Allow public read on swing_signals" ON swing_signals FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'swing_events' AND policyname = 'Allow public read on swing_events'
    ) THEN
        CREATE POLICY "Allow public read on swing_events" ON swing_events FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'swing_sleeve_status' AND policyname = 'Allow public read on swing_sleeve_status'
    ) THEN
        CREATE POLICY "Allow public read on swing_sleeve_status" ON swing_sleeve_status FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'swing_ledger' AND policyname = 'Allow public read on swing_ledger'
    ) THEN
        CREATE POLICY "Allow public read on swing_ledger" ON swing_ledger FOR SELECT USING (true);
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
