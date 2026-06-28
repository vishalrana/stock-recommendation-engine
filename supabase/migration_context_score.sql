-- Add context_score column to signals and signals_history tables
ALTER TABLE signals ADD COLUMN IF NOT EXISTS context_score FLOAT DEFAULT 0.0;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS context_score FLOAT DEFAULT 0.0;
