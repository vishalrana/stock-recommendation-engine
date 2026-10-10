-- Migration (2026-10-10): per-idea strategy evidence measured against SPY (display only).
--   strategy_beta_adjusted_pct  mean net return minus beta x SPY over each backtest trade's window
--   strategy_excess_vs_spy_pct  mean net return minus SPY over the same window
-- Both come from config/strategy_performance.json (production-universe backtest) and are frozen
-- at issuance like the other strategy_* fields. Idempotent: safe to run more than once.
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_beta_adjusted_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_excess_vs_spy_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_beta_adjusted_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_excess_vs_spy_pct NUMERIC;

NOTIFY pgrst, 'reload schema';
