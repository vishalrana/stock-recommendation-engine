-- Migration v1.3n: Add signals_strong_buy and signals_buy columns to scan_log table
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS signals_strong_buy INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS signals_buy INT DEFAULT 0;

-- Reload schema cache
NOTIFY pgrst, 'reload schema';
