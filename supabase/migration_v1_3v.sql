-- Widen position_sizing column to VARCHAR(30) to prevent value too long errors
ALTER TABLE signals ALTER COLUMN position_sizing TYPE VARCHAR(30);
ALTER TABLE signals_history ALTER COLUMN position_sizing TYPE VARCHAR(30);
