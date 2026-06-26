-- Migration v1.3h: Add Max Gap % gate (failed_maxgap_gate) to scan_log
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_maxgap_gate INT DEFAULT 0;
