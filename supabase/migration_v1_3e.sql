-- Migration v1.3e: Add median_win_return column to ticker_metrics table
-- Run this in the Supabase SQL Editor

ALTER TABLE ticker_metrics ADD COLUMN IF NOT EXISTS median_win_return FLOAT DEFAULT 0.0;
