-- Migration v1.3g: Add Min Risk % gate (failed_minrisk_gate) to scan_log
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_minrisk_gate INT DEFAULT 0;
