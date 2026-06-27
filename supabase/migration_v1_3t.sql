-- Migration v1.3t: Track regime-aware strategy activation in scan_log

ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS active_strategies INT DEFAULT 0;
ALTER TABLE scan_log ADD COLUMN IF NOT EXISTS skipped_strategies JSONB DEFAULT '{}';
