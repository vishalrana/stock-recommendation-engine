-- Migration: Lifecycle path replay, P/E ratio, and reach-probability sample quality
--
-- entry_fill_price      Actual fill used for outcome tracking: open of the first bar after scan_date (D+1).
--                       entry_price / reference_entry_price remain the indicative entry shown at issuance.
-- position_state        Replayed position state of an open recommendation: open / hit_t1 / hit_t2.
-- current_stop          Effective stop after scale-out ratchets (breakeven after T1, T1 after T2).
-- pe_ratio              Trailing P/E at issuance (informational only; never used in scoring).
-- reach_prob_t1_ci_*    95% Wilson interval of the raw empirical T1 reach rate.
-- reach_prob_effective_samples  Approx. independent samples behind the reach rate.
-- reach_prob_source     strategy_conditional (backtest hit rate of the strategy's setups) or ticker_base_rate.
-- strategy_win_rate / strategy_expectancy_pct / strategy_trades
--                       Production-pipeline backtest evidence for the strategy (config/strategy_performance.json).
-- Outcomes/statuses may now also be 'expired' (strategy holding period ended).

-- 1. signals table
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reach_prob_source TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_win_rate NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_expectancy_pct NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_trades INTEGER;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_fill_price NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS position_state TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS current_stop NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS pe_ratio NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reach_prob_t1_ci_low NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reach_prob_t1_ci_high NUMERIC;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS reach_prob_effective_samples NUMERIC;

-- 2. signals_history table
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reach_prob_source TEXT;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_win_rate NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_expectancy_pct NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_trades INTEGER;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS entry_fill_price NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS pe_ratio NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reach_prob_t1_ci_low NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reach_prob_t1_ci_high NUMERIC;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS reach_prob_effective_samples NUMERIC;

-- Refresh PostgREST schema cache so the new columns are visible immediately
NOTIFY pgrst, 'reload schema';
