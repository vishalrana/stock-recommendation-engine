-- Migration v1.3k: Add momentum_exceptions column to scan_log
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS momentum_exceptions INT DEFAULT 0;
