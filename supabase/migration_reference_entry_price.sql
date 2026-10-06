-- Migration: Add reference_entry_price, weighted_scaleout_rr, and entry_location_zone
-- Enables explicit reference entry price immutability, scale-out R:R naming, and entry location zone classification.

-- 1. signals table
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reference_entry_price NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS weighted_scaleout_rr NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_location_zone TEXT;

-- 2. signals_history table
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reference_entry_price NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS weighted_scaleout_rr NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS entry_location_zone TEXT;

-- 3. Backfill existing recommendations
UPDATE signals
SET reference_entry_price = entry_price
WHERE reference_entry_price IS NULL AND entry_price IS NOT NULL;

UPDATE signals_history
SET reference_entry_price = entry_price
WHERE reference_entry_price IS NULL AND entry_price IS NOT NULL;

UPDATE signals
SET weighted_scaleout_rr = weighted_rr_honest
WHERE weighted_scaleout_rr IS NULL AND weighted_rr_honest IS NOT NULL;

UPDATE signals_history
SET weighted_scaleout_rr = weighted_rr_honest
WHERE weighted_scaleout_rr IS NULL AND weighted_rr_honest IS NOT NULL;
