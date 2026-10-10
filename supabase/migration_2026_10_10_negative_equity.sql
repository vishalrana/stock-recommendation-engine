-- Migration (2026-10-10): flag companies whose total equity is zero or negative.
-- Display only: the card shows "Neg. equity" instead of a meaningless (or missing) D/E.
-- Idempotent: safe to run more than once.
ALTER TABLE signals ADD COLUMN IF NOT EXISTS negative_equity BOOLEAN;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS negative_equity BOOLEAN;

NOTIFY pgrst, 'reload schema';
