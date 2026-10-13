-- Migration (2026-10-12): swing-fix. Sleeve status carries its real holdout numbers (from the
-- GitHub research run recorded in config/swing_sleeves.json) and the run id. Idempotent.
ALTER TABLE swing_sleeve_status ADD COLUMN IF NOT EXISTS details JSONB DEFAULT '{}'::jsonb;
ALTER TABLE swing_sleeve_status ADD COLUMN IF NOT EXISTS research_run_id TEXT;
ALTER TABLE swing_signals ADD COLUMN IF NOT EXISTS mode TEXT;   -- sleeve mode when issued: LIVE / LIVE-SP500 / SHADOW

NOTIFY pgrst, 'reload schema';
