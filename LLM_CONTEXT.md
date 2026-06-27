# Master LLM Project Context: Stock Recommendation Engine (Strategy 1.3 Rev B)

This document serves as the definitive reference manual for the **Stock Recommendation Engine** codebase. It provides a complete, structured context covering the engine's core algorithm (Strategy 1.3 Rev B), scoring mechanics, directory architecture, database schema, frontend UI specifications, and CI/CD pipelines.

---

## 1. Core Architecture Overview

The system is a fully automated, data-driven stock scanner inspired by Danelfin. It operates nightly to discover, score, rank, and publish premium buy recommendations from a universe of **514 US equities** (S&P 500 + Nasdaq-100).

```
                     +---------------------------------------+
                     |             yfinance API              |
                     +-------------------+-------------------+
                                         |
                                         | (Daily data fetch)
                                         v
                     +-------------------+-------------------+
                     |          GitHub Actions Cron          |
                     |      (Runs nightly at 6:00 AM UTC)    |
                     +-------------------+-------------------+
                                         |
                                         | (Runs Python jobs/generate_signals.py)
                                         v
                     +-------------------+-------------------+
                     |         Supabase PostgreSQL DB        |
                     | (Tables: signals, scan_log, metrics)  |
                     +-------------------+-------------------+
                                         |
                                         | (Real-time dynamic query)
                                         v
                     +-------------------+-------------------+
                     |         Next.js Web Application       |
                     |  (TailwindCSS, TanStack Table, Vercel) |
                     +---------------------------------------+
```

---

## 2. Technical Strategy: Strategy 1.3 Rev B

Nightly scanning executes the **Strategy 1.3 Rev B** parameters. Each ticker must pass through multiple regime-aware indicators and quality gate checks to qualify as a candidate.

### A. Market Regime Detection
* **Sensing Asset**: `SPY` (S&P 500 ETF).
* **Reference Line**: 200-day Simple Moving Average (SMA).
* **Regimes**:
  * **BULL**: `SPY Close > 200 SMA`
  * **BEAR / SIDEWAYS**: `SPY Close <= 200 SMA` (or Sideways if specified).

### B. Technical Indicators & Gated Parameters
1. **Regime-Aware Trend Gate**:
   * **BULL Regime**: `Close > 50 DMA` only (relaxed to prevent over-filtering during strong markets).
   * **BEAR / SIDEWAYS Regime**: Strict trend stack requiring `Close > 50 DMA > 200 DMA`.
2. **RSI Pullback-Recovery Gate**:
   * Standard Wilder RSI(14) must have dipped below **`52.0`** within the last 10 trading days (`rsi_min_10d < 52.0`).
   * Current RSI(14) must reside between **`45.0`** and **`67.0`** (inclusive) to confirm recovery from the pullback.
3. **Regime-Aware ADX Gate**:
   * **BULL Regime**: Current ADX(14) must be `>= 15.0` (captures emerging trends).
   * **BEAR / SIDEWAYS Regime**: Current ADX(14) must be `>= 18.0` (requires stronger trend confirmation).
4. **Volume Confirmation**:
   * Daily volume must be `>= 1.0x` of the 20-day Volume Simple Moving Average.
5. **Historical Trades Floor**:
   * Tickers must have generated `>= 10` historical backtested trades in `ticker_metrics` to filter out low-sample-size anomalies.

### C. Dynamic Trade Setup Calculations
* **Entry Price**: Next day's high * 1.001 (representing a breakout entry trigger).
* **Stop-Loss Price**: Recent 20-day swing low (the lowest low of the last 20 days, requiring a 2-day left/right clearance).
* **Dynamic profit target (`target_pct`)**:
  * If the ticker has winning trades in `ticker_metrics` (`median_win_return > 0`): `median_win_return * 1.15` (15% premium buffer), capped between **`5.0%`** and **`20.0%`**.
  * If no winning history: ATR fallback: `2.5 * ATR(14) / Price * 100` (capped at **`20.0%`**).
* **Target Exit Price**: `Entry Price * (1 + target_pct / 100)`
* **Risk/Reward Ratio**: `target_pct / ((entry_price - stop_loss) / entry_price * 100)`

### D. The 4 Active Quality Gates (Rev B)
1. **Min Risk % Gate**: Rejects signals if the stop-loss risk is `< 2.5%` of the entry price (avoids noise-level shakeouts).
2. **Max Gap % Gate**: Rejects signals if the single-day drop in the last 5 trading days exceeds `5.0%` (guards against flash crashes and falling knives).
3. **Earnings Calendar Filter**: Rejects signals if the next earnings date (scraped via `yfinance`) falls within the next `7` days (prevents holding through binary earnings events).
4. **Momentum Exception**: Bypasses the RSI pullback gate entirely if strong breakout momentum is detected: `Price > 50 DMA by >= 20%`, `Volume Ratio >= 1.5x`, and `ADX >= 20.0` (with current RSI `<= 75` to prevent peak buying).

*(Note: The "Distance from 20-Day High" gate has been permanently removed to restore standard pullback selection).*

---

## 3. Gated Composite Scoring & Tier Ranking

Scoring and ranking are processed by `SignalRanker` (`src/ranker.py`). Every candidate ticker receives a 0-100 composite score composed of four weighted components:

$$\text{Composite Score} = 0.30 \times \text{Momentum} + 0.40 \times \text{Expectancy} + 0.20 \times \text{Win Rate} + 0.10 \times \text{Regime}$$

### Component Math
1. **Momentum Score (30% weight)**:
   * Combines:
     * **RSI Proximity**: $100 - |RSI - 50| \times 4$
     * **50 DMA Proximity**: $100 - |\frac{Price}{50DMA} - 1| \times 500$
     * **Volume Score**: $Volume Ratio \times 50$ (capped at 100)
     * **MACD Score**: $50.0 + MACD Histogram \times 200.0$ (clipped to [0, 100])
   * The average of these four is percentile-normalized (0 to 100) within the candidate pool.
   * *Absolute Floor*: If the raw momentum average is $< 55.0$, the momentum score is forced to `0.0`.
2. **Risk-Adjusted Expectancy Score (40% weight)**:
   * Z-score of `expectancy_pct` is computed and mapped via sigmoid: $100 / (1 + e^{-z})$.
   * Negative expectancy penalty: if $Expectancy < 0$, the score is penalized by $-30.0$ and capped at a minimum of $5.0$.
3. **Win Rate Score (20% weight)**:
   * Percentile-normalized value of the ticker's backtest `win_rate` relative to the current candidate pool.
4. **Regime Score (10% weight)**:
   * **BULL**: 100.0 if RSI is between 50 and 70 AND price is above 50 DMA, else 0.0.
   * **BEAR**: 100.0 if the sector is defensive (Utilities, Consumer Staples, Health Care, Insurance, Telecom) OR Beta < 1.0, else 0.0.
   * **SIDEWAYS**: 100.0 if $|RSI - 50| < 8.0$ (mean-reversion setup), else 0.0.

### Tier Mapping & Absolute Floors
Candidates are classified into four tiers:
* **Strong Buy (Tier 1)**: Composite Score $\ge 65.0$, expectancy $> 0.0$, win rate $\ge 35.0\%$, and total trades $\ge 10$.
* **Buy (Tier 2)**: Composite Score $\ge 50.0$, expectancy $\ge 0.0$, win rate $\ge 25.0\%$, and total trades $\ge 10$.
* **Watch (Tier 3)**: Composite Score $\ge 40.0$, expectancy $\ge -2.0$.
* **Speculative (Tier 4)**: Composite Score $< 40.0$ or fails Watch criteria.
* *Absolute Floor*: If expectancy is negative and win rate is $< 25\%$, the composite score is capped at `40.0` (forces ticker into Speculative/Watch).

### Selection & Auto-Relax Logic
* The engine **only** recommends **Strong Buy** and **Buy** signals. Watch and Speculative signals are completely filtered out.
* **Auto-Relax Selection**: If there are $< 3$ Strong Buy signals, the engine merges the Buy signals and sorts by composite score.
* **Cash Override**: If the combined count of Strong Buy + Buy signals is **`0`**, the database `signals` table is wiped clean and `scan_log` records `signals_recommended = 0`. If 1, 2, or more quality signals exist, they are successfully output.

---

## 4. Database Schema (Supabase PostgreSQL)

### A. Table: `ticker_metrics`
Holds historical backtest metrics populated by seeding scripts.
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
    median_win_return FLOAT DEFAULT 0.0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### B. Table: `signals`
Stores daily qualified active signals. Wiped and inserted fresh nightly.
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
    earnings_date DATE,
    is_momentum_exception BOOLEAN DEFAULT FALSE,
    distance_from_high_pct DECIMAL(5,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    UNIQUE (scan_date, ticker)
);
```

### C. Table: `signals_history`
Archives daily signals before they are wiped.
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
    earnings_date DATE,
    is_momentum_exception BOOLEAN DEFAULT FALSE,
    distance_from_high_pct DECIMAL(5,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    UNIQUE (scan_date, ticker)
);
```

### D. Table: `scan_log`
Tracks nightly scan executions, gate failure distributions, and tier metrics.
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
    failed_maxrisk_gate INT DEFAULT 0,
    failed_minrisk_gate INT DEFAULT 0,
    failed_maxgap_gate INT DEFAULT 0,
    failed_earnings_gate INT DEFAULT 0,
    momentum_exceptions INT DEFAULT 0,
    failed_extended_high_gate INT DEFAULT 0,
    signals_strong_buy INT DEFAULT 0,
    signals_buy INT DEFAULT 0,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);
```

### E. View: `recommendations`
Exposes the database to the Next.js frontend by joining `signals` with `ticker_metrics`.
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
    s.adx_value,
    s.macd_histogram,
    s.ema20,
    s.composite_score,
    s.tier_label,
    s.is_fallback,
    s.is_momentum_exception,
    s.distance_from_high_pct,
    COALESCE(m.win_rate, 0) AS past_win_rate,
    COALESCE(m.expectancy_pct, 0) AS expectancy_pct,
    COALESCE(m.total_signals, 0) AS historical_signals,
    COALESCE(m.median_win_return, 0) AS median_win_return
FROM signals s
LEFT JOIN ticker_metrics m ON s.ticker = m.ticker
WHERE s.tier_label IN ('Strong Buy', 'Buy');
```

---

## 5. Directory Structure & Key Files

```
stock-recommendation-engine/
├── .github/workflows/
│   └── nightly_scan.yml      # Run nightly cron (6:00 AM UTC Mon-Fri)
├── frontend/                 # Next.js App
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx    # Inter font layout
│   │   │   └── page.tsx      # Main server component loading data
│   │   ├── components/
│   │   │   └── recommendations-table.tsx # TanStack table with TradingView link
│   │   ├── lib/
│   │   │   └── supabase.ts   # Client initializer
│   │   └── types/
│   │       └── database.ts   # Typescript mappings (RSI, ADX, Exception, etc)
├── jobs/                     # Python Scan Jobs
│   ├── generate_signals.py   # Main nightly scan pipeline
│   ├── seed_metrics.py       # Seeds backtest metrics into DB
│   └── supabase_client.py    # Supabase authentication client
├── src/                      # Underlying Math & Indicators
│   ├── downloader.py         # Universe fetcher & cacher
│   ├── indicators.py         # RSI pullbacks, ATR, DMA, swing low indicators
│   ├── regime.py             # Regime detection (SPY Close > 200 SMA)
│   └── ranker.py             # Gated composite ranker & selection logic
├── verify_scan.py            # Local DB scan validation script
```

---

## 6. Frontend Specifications (Next.js)

### recommendations-table.tsx Features
* **TanStack Table Framework**: Fully sortable columns with client-side global filtering.
* **TradingView Chart Integration**:
  * Includes a `"Chart"` column next to the `"Tier"` column.
  * Cell renders a **32x32px** custom blue button (`bg-blue-600 hover:bg-blue-700 text-white transition-colors duration-200`) containing a line-chart SVG.
  * Opens a new tab pointing to: `https://www.tradingview.com/chart/?symbol={TICKER}`.
  * Hover tooltip says: `"Open {ticker} on TradingView"`.
* **Regime Banner**: Displays a dynamic colored alert corresponding to the detected market regime (Bull: Green, Bear: Red, Sideways: Blue).
* **MACD Arrow Indicators**: Displays a green up-arrow `↑` if MACD Histogram is positive and a red down-arrow `↓` if negative next to the composite score.
* **RSI Pullback Tooltip**: The current RSI value displays a dotted underline on hover, rendering a tooltip showing the lowest RSI value reached in the last 10 trading days.
* **Momentum Badges**: Tickers qualifying under the Momentum Exception are flagged with an amber `"Breakout"` badge.

---

## 7. Execution Commands

### Run Local Dry Run
```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
python -m jobs.generate_signals --dry-run
```

### Run Live Nightly Scan
```powershell
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
python -m jobs.generate_signals
```

### Start Next.js Development Server
```bash
cd frontend
npm run dev
```
