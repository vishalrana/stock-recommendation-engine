-- Migration v1.3i: Add failed_earnings_gate column to scan_log
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_earnings_gate INT DEFAULT 0;
