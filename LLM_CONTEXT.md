# LLM Project Context: Stock Recommendation Engine

This document provides a complete overview of the **Stock Recommendation Engine** codebase, architecture, database schemas, and integration points. You can feed this directly into an LLM (such as Gemini, Claude, or ChatGPT) to give it full context on how to help you write code, debug, or implement new features.

---

## 1. Project Overview & Objectives

The project is a personal stock recommendation platform inspired by Danelfin. It scans S&P 500 stocks, runs technical backtests to compute historical win rates and expectancies, generates nightly signals, and presents them in a clean, filterable frontend.

### Primary Strategy: Strategy 1.1 Beta
- **Trend Filter**: Price > 50 DMA > 200 DMA
- **RSI Pullback**: Minimum Wilder RSI(14) in the last 10 trading days < 45
- **RSI Recovery**: Current Wilder RSI(14) is between 45 and 65 (inclusive)
- **Volume Filter**: Current Volume > 1.0x of the 20-day Volume Moving Average
- **Risk Setup**: 
  - **Entry**: Next day's high * 1.001
  - **Stop Loss**: Recent swing low (lowest low of the last 20 days, requiring a 2-day left/right clearance)
  - **Exit (Target)**: 3R Target (Exit = Entry + 3 * Risk)

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
- **CI/CD / Automation**: GitHub Actions (runs data scans and updates database nightly).

---

## 3. Directory Structure

```
stock-recommendation-engine/
├── .github/
│   └── workflows/
│       └── nightly_scan.yml      # GitHub Actions automation workflow
├── data/
│   └── cache/                    # Parquet files containing OHLCV history
├── docs/
│   └── GITHUB_ACTIONS_SETUP.md   # Deployment setup documentation
├── frontend/                     # Next.js App
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx        # App wrapper & fonts
│   │   │   └── page.tsx          # Dynamic page fetching Supabase view
│   │   ├── components/
│   │   │   └── recommendations-table.tsx # TanStack table UI
│   │   ├── lib/
│   │   │   └── supabase.ts       # Lazy Supabase Client
│   │   └── types/
│   │       └── database.ts       # TypeScript type interfaces
│   ├── package.json
│   ├── tsconfig.json
│   └── tailwind.config.ts
├── jobs/                         # Python Automation Scripts
│   ├── __init__.py
│   ├── generate_signals.py       # Main generator (reads cache, computes, writes to DB)
│   ├── seed_metrics.py           # Populates historical ticker_metrics table
│   ├── supabase_client.py        # Python Supabase connection setup
│   └── requirements.txt          # Python packages for jobs
├── src/                          # Math & Core Backtest Logic
│   ├── downloader.py             # yfinance fetcher and Parquet cacher
│   ├── indicators.py             # Computes DMA, RSI(Wilder), Volume MA
│   └── main.py                   # Orchestrator (mostly legacy/backtest runner)
├── supabase/
│   └── schema.sql                # SQL schema & view definitions
├── requirements.txt              # Primary project packages
└── venv/                         # Local python virtual environment
```

---

## 4. Supabase Database Schema

The database uses Row Level Security (RLS) disabled or bypassed using the service_role key for writes, and anon key for public reads on the view.

### `ticker_metrics` Table
Stores the historical performance metrics generated by backtests on the ticker's history.
```sql
CREATE TABLE ticker_metrics (
    ticker VARCHAR(10) PRIMARY KEY,
    industry VARCHAR(100),
    total_trades INT DEFAULT 0,
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    win_rate NUMERIC(5,2) DEFAULT 0.00,
    expectancy_pct NUMERIC(8,4) DEFAULT 0.0000,
    median_holding_days INT DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### `signals` Table
Stores current qualified recommendations generated during the most recent nightly scan. Cleared before every new scan runs.
```sql
CREATE TABLE signals (
    ticker VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(150),
    industry VARCHAR(100),
    scan_date DATE NOT NULL,
    price NUMERIC(10,2),
    entry_price NUMERIC(10,2) NOT NULL,
    stop_loss NUMERIC(10,2) NOT NULL,
    exit_price NUMERIC(10,2) NOT NULL,
    upside_pct NUMERIC(8,2),
    risk_reward NUMERIC(5,2),
    current_rsi NUMERIC(5,2),
    volume_ratio NUMERIC(8,2),
    score NUMERIC(8,4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### `scan_log` Table
Maintains audit logs of every nightly scan execution.
```sql
CREATE TABLE scan_log (
    scan_date DATE PRIMARY KEY,
    tickers_scanned INT NOT NULL,
    signals_generated INT NOT NULL,
    scan_duration_secs NUMERIC(8,2) NOT NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### `recommendations` View
Joins active daily `signals` with their historical performance metrics from `ticker_metrics` for frontend display.
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
    s.volume_ratio,
    s.score,
    COALESCE(m.win_rate, 0.00) AS past_win_rate,
    COALESCE(m.expectancy_pct, 0.0000) AS expectancy_pct,
    COALESCE(m.total_trades, 0) AS historical_signals,
    COALESCE(m.wins, 0) AS historical_wins,
    COALESCE(m.losses, 0) AS historical_losses,
    COALESCE(m.median_holding_days, 0) AS median_holding_days
FROM signals s
LEFT JOIN ticker_metrics m ON UPPER(s.ticker) = UPPER(m.ticker);
```

---

## 5. Critical Ranking Metric

The `score` represents the recommendation quality. It's computed at scan time in `generate_signals.py` and saved to the `signals` table:

$$\text{score} = 0.4 \times \text{win\_rate} + 0.4 \times \text{expectancy\_pct} + 0.2 \times \text{upside\_pct}$$

*(Note: There is a known scale mismatch flaw where `win_rate` (15–65) dominates `expectancy_pct` (-5 to +10). Standardizing or gating these values is an expected goal for future strategy iterations).*

---

## 6. Key Configuration & Setup Instructions

### Environment Variables (.env)
Create a `.env` file in the project root for local Python scripts:
```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_SERVICE_KEY=your-supabase-service-role-key-secret
```

Create a `frontend/.env.local` file for Next.js local development:
```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project-id.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-supabase-anon-public-key
```

### Run Python Scripts Locally
Activate your virtual environment and run modules:
```powershell
# Windows
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
python -m jobs.generate_signals
```

### Run Next.js Frontend Locally
```bash
cd frontend
npm run dev
# Server runs on http://localhost:3000
```
Note: Next.js is configured with `export const dynamic = 'force-dynamic'` in `page.tsx` and uses a lazy-initialized client in `lib/supabase.ts` to prevent missing-env-var crashes during the production build step on Vercel.
