-- Migration (2026-10-11): profitability program display fields (all display-only; never scored).
--   strategy_expected_r        mean realized R multiple (net return / planned stop distance) of the
--                              strategy's backtest setups, from config/strategy_performance.json
--   strategy_exit_*_pct        share of those setups that exited at the stop, a final target, or the
--                              holding-period limit
--   strategy_t2_hit_pct / strategy_t3_hit_pct   how often T2 / T3 were reached in the backtest
--   earnings_unknown           no earnings date was known when the idea was issued (full-hold rule)
--   event_lock_unknown         SEC filings could not be checked for deals when the idea was issued
-- Frozen at issuance like the other strategy_* fields. Idempotent: safe to run more than once.
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_expected_r NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_exit_stop_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_exit_target_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_exit_expiry_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_t2_hit_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_t3_hit_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS earnings_unknown BOOLEAN;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS event_lock_unknown BOOLEAN;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_expected_r NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_exit_stop_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_exit_target_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_exit_expiry_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_t2_hit_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_t3_hit_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS earnings_unknown BOOLEAN;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS event_lock_unknown BOOLEAN;

NOTIFY pgrst, 'reload schema';
