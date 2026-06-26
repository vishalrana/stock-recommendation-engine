-- Migration v1.3k_signals: Add is_momentum_exception column to signals and signals_history
-- Run this in the Supabase SQL Editor

ALTER TABLE signals ADD COLUMN IF NOT EXISTS is_momentum_exception BOOLEAN DEFAULT FALSE;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS is_momentum_exception BOOLEAN DEFAULT FALSE;
