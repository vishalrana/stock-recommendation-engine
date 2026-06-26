-- Migration v1.3l: Add failed_extended_high_gate column to scan_log
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_extended_high_gate INT DEFAULT 0;
