-- Migration (2026-10-10): SEC fundamentals provenance + previously unapplied columns
-- Idempotent: safe to run more than once.
--
-- 1. Columns from migration_reference_entry_price.sql, which was never applied in production.
--    While they were missing, every insert fell back to dropping ALL optional fields.
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reference_entry_price NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS weighted_scaleout_rr NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_location_zone TEXT;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reference_entry_price NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS weighted_scaleout_rr NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS entry_location_zone TEXT;

UPDATE signals SET reference_entry_price = entry_price
WHERE reference_entry_price IS NULL AND entry_price IS NOT NULL;
UPDATE signals_history SET reference_entry_price = entry_price
WHERE reference_entry_price IS NULL AND entry_price IS NOT NULL;

-- 2. Fundamentals provenance (display only; never used for qualification)
--    eps_ttm              trailing-12-month diluted EPS (negative = loss-maker, so P/E is not meaningful)
--    fundamentals_source  "sec", "yahoo" or "sec+yahoo"
--    fundamentals_as_of   balance-sheet date of the filing the D/E and current ratio come from
ALTER TABLE signals ADD COLUMN IF NOT EXISTS eps_ttm NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS fundamentals_source TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS fundamentals_as_of DATE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS eps_ttm NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS fundamentals_source TEXT;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS fundamentals_as_of DATE;

NOTIFY pgrst, 'reload schema';
