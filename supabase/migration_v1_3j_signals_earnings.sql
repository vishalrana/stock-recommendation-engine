-- Migration v1.3j: Add earnings_date column to signals and signals_history
-- Run this in the Supabase SQL Editor

ALTER TABLE signals ADD COLUMN IF NOT EXISTS earnings_date DATE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS earnings_date DATE;
