-- Migration v1.3d: Replace Risk/Reward hard gate with Max Risk % gate
-- Run this in the Supabase SQL Editor

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS failed_maxrisk_gate INT DEFAULT 0;
