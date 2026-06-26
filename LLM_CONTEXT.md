# LLM Project Context: Stock Recommendation Engine (Strategy 1.3 Rev B)

This document provides a comprehensive overview of the **Stock Recommendation Engine** codebase, architecture, database schemas, and integration points. You can feed this directly into an LLM (such as Gemini, Claude, or ChatGPT) to give it full context on how to help you write code, debug, or implement new features.

---

## 1. Project Overview & Objectives

The project is a personal stock recommendation platform inspired by Danelfin. It scans a combined universe of S&P 500 and Nasdaq-100 stocks nightly, runs technical backtests to compute historical win rates and expectancies, generates nightly signals, and presents them in a clean, filterable Next.js frontend.

### Primary Strategy: Strategy 1.3 Rev B (Regime-Aware Composite Scoring & Fallback)
- **Market Regime Detection**: Uses `SPY` vs its 200-day Simple Moving Average (SMA) to classify the market as `bull`, `bear`, or `sideways`.
- **Technical Filters**:
  - **Trend**:
    - **BULL Regime**: relaxed trend gate requiring `Price > 50 DMA` only.
    - **BEAR / SIDEWAYS Regimes**: full trend stack requiring `Price > 50 DMA > 200 DMA`.
  - **RSI Pullback-Recovery**: Wilder RSI(14) dipped below `52.0` in the last 10 trading days, and current RSI is between `45.0` and `67.0` (inclusive).
  - **ADX Trend Strength**: Current ADX(14) is `>= 18.0`.
  - **Volume Confirmation**: Current Volume >= `1.0x` of the 20-day Volume Moving Average.
  - **Trades Floor**: The ticker must have at least `10` historical trades in backtest metrics (`ticker_metrics`).
  - **Risk Setup**:
    - **Entry**: Next day's high * 1.001
    - **Stop Loss**: Recent swing low (lowest low of the last 20 days, requiring a 2-day left/right clearance)
    - **Exit (Target)**: 3R Target (Exit = Entry + 3 * Risk)
- **Universe Expansion**: Fetches S&P 500 (~502 tickers) + Nasdaq-100 (~12 non-overlapping tickers) from Wikipedia for a total universe of ~514 tickers.
- **Blacklist**: Skipped tickers `{'XYZ', 'TEST', 'PLACEHOLDER'}` before downloading or scanning.
- **Extended Bull Fallback**:
  - Triggered in a `bull` regime when the recommended signals count is `< 3` AND the primary scan's `rsi_breadth_pct` is `< 25.0%`.
  - Re-scans tickers with relaxed parameters: `FALLBACK_RSI_PULLBACK_THRESHOLD = 57.0` and `FALLBACK_ADX_MIN = 15.0`.
  - Fallback signals are flagged with `is_fallback = True` and capped at the `"Watch"` tier.

---

## 2. Technology Stack & Architecture

```
                 +-----------------------------------------+
                 |              yfinance API               |
                 +--------------------+--------------------+
                                      |
                                      | (Daily Data Fetch)
                                      v
                 +--------------------+--------------------+
                 |           GitHub Actions                |
                 |      (Runs nightly_scan.yml workflow)   |
                 +--------------------+--------------------+
                                      |
                                      | (Runs Python jobs/generate_signals.py)
                                      v
                 +--------------------+--------------------+
                 |            Supabase Database            |
                 |  (Tables: ticker_metrics, signals, etc) |
                 +--------------------+--------------------+
                                      |
                                      | (Real-time dynamic query)
                                      v
                 +--------------------+--------------------+
                 |             Next.js Frontend            |
                 |  (TailwindCSS, TanStack Table, Vercel)  |
                 +--------------------+--------------------+
```

- **Backend**: Python 3.11/3.12 (utilizes `yfinance`, `pandas`, `numpy`, `pyarrow` for processing; `supabase-py` for upserting).
- **Database**: Supabase (PostgreSQL).
- **Frontend**: Next.js 16 (App Router, TypeScript, TailwindCSS, `@tanstack/react-table` for sorting/filtering).
- **Hosting**: Vercel (Frontend).
- **CI/CD / Automation**: GitHub Actions (runs data scans and updates database nightly, setting `PYTHONPATH: .`).

---

## 3. Directory Structure

```
stock-recommendation-engine/
├── .github/
│   └── workflows/
│       └── nightly_scan.yml      # GitHub Actions automation workflow (runs nightly)
├── data/
│   └── cache/                    # Parquet files containing OHLCV history
├── docs/
│   └── GITHUB_ACTIONS_SETUP.md   # Deployment setup documentation
├── frontend/                     # Next.js App
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx        # App wrapper & fonts
│   │   │   └── page.tsx          # Dynamic page fetching Supabase view & scan_log
│   │   ├── components/
│   │   │   └── recommendations-table.tsx # TanStack table UI with composite bars, badges, fallback styles
│   │   ├── lib/
│   │   │   └── supabase.ts       # Lazy Supabase Client
│   │   └── types/
│   │       └── database.ts       # TypeScript type interfaces (includes is_fallback, composite_score)
│   ├── package.json
│   ├── tsconfig.json
│   └── tailwind.config.ts
├── jobs/                         # Python Automation Scripts
│   ├── __init__.py
│   ├── generate_signals.py       # Main generator (reads cache, computes indicators, calls ranker, writes to DB)
│   ├── seed_metrics.py           # Populates historical ticker_metrics table
│   ├── supabase_client.py        # Python Supabase connection setup
│   ├── validate_ranking.py       # Post-deployment script evaluating signals after 20 trading days
│   └── requirements.txt          # Python packages for jobs
├── src/                          # Math & Core Backtest Logic
│   ├── downloader.py             # yfinance fetcher and Parquet cacher
│   ├── indicators.py             # Computes DMA, RSI(Wilder), Volume MA
│   ├── regime.py                 # Detects market regime (SPY vs 200 SMA)
│   ├── ranker.py                 # Gated composite scoring and tiered selection logic
│   └── main.py                   # Orchestrator (mostly legacy/backtest runner)
├── supabase/
│   ├── schema.sql                # SQL base schema & view definitions (Strategy 1.1)
│   ├── migration_v1_2.sql        # Migration SQL adding composite_score, tier_label, and signals_history
│   ├── migration_v1_3.sql        # Migration SQL adding indicator columns (adx_value, macd_histogram, etc.)
│   ├── migration_v1_3b.sql       # Migration SQL adding rsi_breadth_pct column
│   └── migration_v1_3c.sql       # Migration SQL adding is_fallback, unique constraints, and view updates
├── requirements.txt              # Primary project packages
└── venv/                         # Local python virtual environment
```

---

## 4. Supabase Database Schema

The database uses Row Level Security (RLS) bypassed using the service_role key for writes, and anon key for public reads on the view.

### `ticker_metrics` Table
Stores the historical performance metrics generated by backtests on the ticker's history.
```sql
CREATE TABLE ticker_metrics (
    ticker VARCHAR(10) PRIMARY KEY,
    industry VARCHAR(100),
    total_signals INT DEFAULT 0,
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    win_rate NUMERIC(6,2) DEFAULT 0.00,
    expectancy_pct NUMERIC(8,4) DEFAULT 0.0000,
    median_holding_days NUMERIC(6,1) DEFAULT 0.0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### `signals` Table
Stores current qualified recommendations generated during the most recent nightly scan. Cleared before every new scan runs.
```sql
CREATE TABLE signals (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    scan_date DATE NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    company_name VARCHAR(150),
    industry VARCHAR(100),
    price NUMERIC(10,2),
    entry_price NUMERIC(10,2) NOT NULL,
    stop_loss NUMERIC(10,2) NOT NULL,
    exit_price NUMERIC(10,2) NOT NULL,
    upside_pct NUMERIC(8,2),
    risk_reward NUMERIC(5,2),
    current_rsi NUMERIC(5,2),
    volume_ratio NUMERIC(8,2),
    score NUMERIC(8,4),
    regime VARCHAR(10),
    composite_score FLOAT DEFAULT 0,
    tier_label TEXT DEFAULT 'Speculative',
    adx_value FLOAT DEFAULT NULL,
    macd_histogram FLOAT DEFAULT NULL,
    rsi_min_10d FLOAT DEFAULT NULL,
    ema20 FLOAT DEFAULT NULL,
    is_fallback BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    UNIQUE (scan_date, ticker)
);
```

### `signals_history` Table
Archives daily picks before the `signals` table is wiped. Uses a unique constraint on `(scan_date, ticker)` to prevent duplicate archiving.
```sql
CREATE TABLE signals_history (
    id BIGSERIAL PRIMARY KEY,
    scan_date DATE NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    company_name VARCHAR(150),
    industry VARCHAR(100),
    price NUMERIC(10,2),
    entry_price NUMERIC(10,2) NOT NULL,
    stop_loss NUMERIC(10,2) NOT NULL,
    exit_price NUMERIC(10,2) NOT NULL,
    upside_pct NUMERIC(8,2),
    risk_reward NUMERIC(5,2),
    current_rsi NUMERIC(5,2),
    volume_ratio NUMERIC(8,2),
    score NUMERIC(8,4),
    past_win_rate NUMERIC(5,2),
    expectancy_pct NUMERIC(8,4),
    total_trades INT,
    regime VARCHAR(10),
    composite_score FLOAT DEFAULT 0,
    tier_label TEXT DEFAULT 'Speculative',
    adx_value FLOAT DEFAULT NULL,
    macd_histogram FLOAT DEFAULT NULL,
    rsi_min_10d FLOAT DEFAULT NULL,
    ema20 FLOAT DEFAULT NULL,
    is_fallback BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    UNIQUE (scan_date, ticker)
);
```

### `scan_log` Table
Maintains audit logs of every nightly scan execution. Contains columns for gate rejection breakdown.
```sql
CREATE TABLE scan_log (
    scan_date DATE PRIMARY KEY,
    tickers_scanned INT NOT NULL,
    signals_generated INT NOT NULL,
    signals_qualified INT DEFAULT 0,
    signals_recommended INT DEFAULT 0,
    scan_duration_secs NUMERIC(8,2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    regime VARCHAR(10),
    failed_rsi_gate INT DEFAULT 0,
    failed_adx_gate INT DEFAULT 0,
    failed_macd_gate INT DEFAULT 0,
    failed_trend_gate INT DEFAULT 0,
    failed_volume_gate INT DEFAULT 0,
    failed_rr_gate INT DEFAULT 0,
    failed_trades_gate INT DEFAULT 0,
    rsi_breadth_pct NUMERIC(5,1) DEFAULT NULL,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### `recommendations` View
Joins active daily `signals` with their historical performance metrics from `ticker_metrics` for frontend display. Exposes `is_fallback` and Strategy 1.3 indicator values.
```sql
CREATE OR REPLACE VIEW recommendations AS
SELECT
    s.scan_date,
    s.ticker,
    s.company_name,
    s.industry,
    s.price,
    s.entry_price,
    s.stop_loss,
    s.exit_price,
    s.upside_pct,
    s.risk_reward,
    s.current_rsi,
    s.rsi_min_10d,
    s.volume_ratio,
    s.adx_value,
    s.macd_histogram,
    s.ema20,
    s.score,
    s.regime,
    s.composite_score,
    s.tier_label,
    s.is_fallback,
    COALESCE(m.win_rate, 0)            AS past_win_rate,
    COALESCE(m.expectancy_pct, 0)      AS expectancy_pct,
    COALESCE(m.total_signals, 0)       AS historical_signals,
    COALESCE(m.wins, 0)                AS historical_wins,
    COALESCE(m.losses, 0)              AS historical_losses,
    COALESCE(m.median_holding_days, 0) AS median_holding_days
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker;
```

---

## 5. Gated Composite Scoring System

The ranking engine calculates a 0-100 composite score for every candidate:

### 1. Technical Momentum (30% weight)
Combines:
- **RSI Proximity**: `max(0, 100 - abs(RSI - 50) * 4)`
- **50 DMA Proximity**: `max(0, 100 - abs(Price / 50DMA - 1) * 500)`
- **Volume Ratio**: `min(100, volume_ratio * 50)`
- **MACD Histogram**: `50.0 + macd_histogram * 200.0` (clipped in [0, 100])
The average of these scores is percentile-normalized (0 to 100) within the candidate pool.
*Absolute Floor*: If the raw momentum average is `< 55`, the momentum score is capped at `0.0` regardless of pool rank.

### 2. Risk-Adjusted Expectancy (40% weight)
- Computes the Z-score of `expectancy_pct` within the pool.
- Maps it using a sigmoid: `raw = 100 / (1 + exp(-z))`.
- Softens negative expectancy penalty: if `expectancy_pct < 0`, `raw = max(5, raw - 30)` (clipped in [5, 100]).

### 3. Historical Win Rate (20% weight)
- Percentile-normalizes the ticker's backtest `win_rate` (0 to 100) within the candidate pool.

### 4. Regime Adjustment (10% weight)
- **Bull**: `100.0` if `RSI` is between 50 and 70 AND price is above 50 DMA, else `0.0`.
- **Bear**: `100.0` if `industry` is defensive (Utilities, Consumer Staples, Health Care, Insurance, Telecommunication Services) OR beta < 1.0, else `0.0`.
- **Sideways**: `100.0` if `abs(RSI - 50) < 8.0` (mean-reversion candidate), else `0.0`.

---

## 6. Tiered Fallback & Selection Logic

Candidates are mapped to a selection tier:
- **Tier 1 (Strong Buy)**: `composite_score >= 70`, `expectancy_pct > 0.0`, `win_rate >= 35%`, `total_trades >= 10`.
- **Tier 2 (Buy)**: `composite_score >= 58`, `expectancy_pct >= 0.0`, `win_rate >= 30%`, `total_trades >= 10`.
- **Tier 3 (Watch)**: `composite_score >= 45`, `expectancy_pct >= -1.0`.
- **Tier 4 (Speculative)**: `composite_score < 45` or doesn't meet Watch.
*Absolute Floor*: A stock with negative expectancy AND win rate < 30% is capped at a composite score of `45.0` (cannot be Tier 1 or Tier 2).

### Selection Workflow:
1. Try selecting using **Auto-Relax**:
   - If Tier 1 has `< 3` candidates, relax search to Tier 2.
   - If Tier 1 + 2 has `< 3` candidates, relax search to Tier 3.
2. Select candidates in order of their tier priority (T1 > T2 > T3 > T4), sorting by score descending within each.
3. Slice the top 5 (`top_n=5`).

---

## 7. Setup & Run Instructions

### Setup Environment
Create a `.env` in the project root:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key-secret
```

Create a `frontend/.env.local` for local frontend development:
```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-public-key
```

### Activate Environment and Scan (Windows PowerShell)
```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
# Test ranking engine unit tests
python -m src.ranker
# Run nightly generation scan
python -m jobs.generate_signals
```

### Running Next.js Frontend
```bash
cd frontend
npm install
npm run dev
# Server runs on http://localhost:3000
```
