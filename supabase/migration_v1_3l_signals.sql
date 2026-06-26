-- Migration v1.3l_signals: Add distance_from_high_pct column to signals and signals_history
-- Run this in the Supabase SQL Editor

ALTER TABLE signals ADD COLUMN IF NOT EXISTS distance_from_high_pct DECIMAL(5,2);
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS distance_from_high_pct DECIMAL(5,2);
