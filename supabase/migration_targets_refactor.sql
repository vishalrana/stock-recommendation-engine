-- ====================================================================
-- Migration: Add Strategy-Specific ATR Targets & Reach Probability Columns
-- ====================================================================

-- 1. Add new columns to active signals table
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS target_1_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_2_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_3_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t1 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t2 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t3 NUMERIC,
    ADD COLUMN IF NOT EXISTS scale_out_weights TEXT NOT NULL DEFAULT '50/30/20',
    ADD COLUMN IF NOT EXISTS weighted_rr_honest NUMERIC;

-- 2. Add new columns to historical signals table for historical tracking
ALTER TABLE signals_history
    ADD COLUMN IF NOT EXISTS target_1_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_2_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS target_3_atr NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t1 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t2 NUMERIC,
    ADD COLUMN IF NOT EXISTS reach_prob_t3 NUMERIC,
    ADD COLUMN IF NOT EXISTS scale_out_weights TEXT NOT NULL DEFAULT '50/30/20',
    ADD COLUMN IF NOT EXISTS weighted_rr_honest NUMERIC;

-- Comments for documentation
COMMENT ON COLUMN signals.target_1_atr IS 'ATR-based Target 1 before max() comparison with fixed floor';
COMMENT ON COLUMN signals.target_2_atr IS 'ATR-based Target 2 before max() comparison with fixed floor';
COMMENT ON COLUMN signals.target_3_atr IS 'ATR-based Target 3 before max() comparison with fixed floor';
COMMENT ON COLUMN signals.reach_prob_t1 IS 'Historical empirical reach probability for Target 1 over holding period';
COMMENT ON COLUMN signals.reach_prob_t2 IS 'Historical empirical reach probability for Target 2 over holding period';
COMMENT ON COLUMN signals.reach_prob_t3 IS 'Historical empirical reach probability for Target 3 over holding period';
COMMENT ON COLUMN signals.scale_out_weights IS 'Scale-out weight allocation across targets (e.g., 50/30/20, 60/30/10, 70/30/0)';
COMMENT ON COLUMN signals.weighted_rr_honest IS 'Honest weighted risk-to-reward ratio using only surviving reachable targets';
