-- Add strategy breakdown tracking to scan_log
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS strategy_breakdown JSONB;

-- Force PostgREST schema cache reload
NOTIFY pgrst, 'reload schema';
