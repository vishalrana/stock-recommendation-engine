-- ============================================================
-- Recommendation Lifecycle & Manual Removal Migration
-- ============================================================
-- Run this in the Supabase SQL Editor if columns are not yet present.
-- All statements are idempotent (safe to re-run).

-- 1. Add manual removal auditing columns to signals
ALTER TABLE signals ADD COLUMN IF NOT EXISTS removal_reason TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS removal_note TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;

-- 2. Add manual removal auditing columns and signal_id linkage to signals_history
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS removal_reason TEXT;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS removal_note TEXT;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS signal_id UUID;
CREATE INDEX IF NOT EXISTS idx_signals_history_signal_id ON signals_history(signal_id);

-- 3. Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

