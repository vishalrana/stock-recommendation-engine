-- Step 1: Add strategy name to signals table (if not already present)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(50) DEFAULT 'Pullback Recovery';

-- Step 2: Add strategy name and target prices to signals_history
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(50) DEFAULT 'Pullback Recovery';
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS target_1 NUMERIC(10,2) DEFAULT NULL;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS target_2 NUMERIC(10,2) DEFAULT NULL;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS target_3 NUMERIC(10,2) DEFAULT NULL;

-- Step 3: Add outcome tracking columns to signals_history only
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS outcome VARCHAR(20) DEFAULT 'open';
-- Values: 'open', 'hit_t1', 'hit_t2', 'hit_t3', 'stopped', 'expired'
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS outcome_return_pct NUMERIC(8,4) DEFAULT NULL;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS outcome_date DATE DEFAULT NULL;
ALTER TABLE signals_history ADD COLUMN IF NOT EXISTS outcome_holding_days INT DEFAULT NULL;
