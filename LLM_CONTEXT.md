# Master LLM Project Context & Enhancement Blueprint: Stock Recommendation Engine (Strategy 1.3 Rev B)

> **Purpose of this Document**: This document serves as an exhaustive, self-contained reference manual and code context for the **Stock Recommendation Engine**. Any LLM reading this file will have complete, accurate technical knowledge of the architecture, mathematical formulas, quantitative strategies, database schema, risk management controls, recent bug fixes, and exact improvement vectors to generate high-value architectural and strategy recommendations.

---

## 1. Executive Summary & Trading Philosophy

The **Stock Recommendation Engine** is an automated, data-driven quantitative stock scanner and portfolio management system (inspired by platforms like Danelfin and Finviz Elite). It operates nightly to scan a universe of **500+ US Equities** (S&P 500 + Nasdaq-100), identify high-probability swing trading setups, score candidates using regime-dependent multi-factor scoring models, enforce strict capital allocation controls, and render real-time trade signals on a modern Next.js web application.

### Key Trading Principles Enforced:
1. **Regime-Aware Execution**: Technical indicators, active strategy suites, and composite weight vectors dynamically adapt based on market regime (**Bull**, **Bear**, or **Sideways**).
2. **Strict Single-Stock Allocation Cap**: No individual stock position can receive more than **5.0% of total portfolio capital** at entry ($500 maximum allocation on a $10,000 portfolio).
3. **Fractional Share Support**: High-priced mega-cap equities (e.g. AXON at $571, LLY at $1,185) receive fractional share sizing (e.g. `0.88 sh`, `0.42 sh`), guaranteeing that high-priced stocks receive their full 5.0% dollar allocation ($500) rather than being zeroed out by whole-share integer rounding.
4. **High-Confidence Quality Floor**: Signals are strictly filtered for candidates meeting a **Composite Score $\ge 80.0$** (Strong Buy / High-Confidence), capping nightly output at the **Top 2–3 setups** to maximize win probability.
5. **Asymmetric Risk-Reward Exits**: Every trade features ATR-scaled profit targets ($T_1, T_2, T_3$), automated partial scale-out profit taking (50% exit at $T_1$ with breakeven stop ratcheting), a hard **7.0% maximum loss ceiling**, and nightly trailing stops ($2.0 \times \text{ATR}$).

---

## 2. Full Architecture & Component Map

```
 +-----------------------------------------------------------------------------------+
 |                                 EXTERNAL APIS                                     |
 |  - yfinance (OHLCV, Volume, ADX, RSI, SPY 200-DMA, ^VIX, .earnings_dates)         |
 |  - feedparser 6.0.14 (Google News RSS sentiment analysis via FinBERT)             |
 |  - Tiingo API (Intraday price monitoring & live exit trigger checks)              |
 +-----------------------------------------+-----------------------------------------+
                                           |
                                           v
 +-----------------------------------------------------------------------------------+
 |                            PYTHON BACKEND & SCANNER                               |
 |                                                                                   |
 |  jobs/generate_signals.py ──> Pipeline controller, strategy scanner, risk gate   |
 |  src/ranker.py            ──> Composite ranking & 5.0% fractional share sizing      |
 |  src/regime.py            ──> SPY 200-DMA regime detector & VIX volatility check    |
 |  src/providers/context/   ──> Context aggregator (Analyst, Earnings, News, P/E)   |
 |  src/scorers/             ──> Context scorer (0-100 scale, fixed double-scaling)  |
 |  jobs/strategies/         ──> 7 Quantitative Trading Strategies                   |
 +-----------------------------------------+-----------------------------------------+
                                           |
                                           v
 +-----------------------------------------------------------------------------------+
 |                              SUPABASE POSTGRESQL DB                               |
 |                                                                                   |
 |  - signals           : Active open & pending signal recommendations               |
 |  - signals_history   : Historical trades & completed outcome records              |
 |  - portfolio_state   : Daily portfolio equity value, peak value & drawdown %      |
 |  - scan_log          : Nightly scan execution metrics & filter breakdown          |
 |  - context_cache     : 24-hour cached context scores                              |
 |  - RPC SQL Procedure : execute_position_exit() (Atomic partial & full lot exits) |
 +-----------------------------------------+-----------------------------------------+
                                           |
                                           v
 +-----------------------------------------------------------------------------------+
 |                             NEXT.JS FRONTEND WEB APP                              |
 |                                                                                   |
 |  - frontend/src/app/page.tsx               : Main Dashboard & recommendations view|
 |  - frontend/src/components/recommendations-table.tsx : Interactive TanStack Table|
 |  - frontend/src/components/portfolio-summary.tsx     : Real-time equity & P&L    |
 |  - frontend/src/lib/market-evaluator.ts   : Client-side position exit sync      |
 +-----------------------------------------------------------------------------------+
```

---

## 3. Comprehensive File Inventory & Function Specs

| File Path | Core Purpose & Main Functions |
| :--- | :--- |
| `jobs/generate_signals.py` | Main orchestrator script. Fetches market regime, runs strategy scans across 502 tickers, calculates composite scores, filters top 2-3 setups ($Score \ge 80$), applies risk management, sizes positions, writes to Supabase `signals` and `signals_history`. |
| `src/ranker.py` | Signal ranking engine. Contains `SignalRanker.composite_rank()`, `compute_composite_score()`, `regime_adjustment()`, and `calculate_normalized_sizing()`. Enforces 5.0% capital allocation cap and fractional share calculations. |
| `src/regime.py` | Market regime classifier. Calculates SPY price distance from 200-day SMA (`pct_above_200dma`). Classifies market into `bull` (> +2.0%), `bear` (< -2.0%), or `sideways`. Checks ^VIX emergency threshold (> 40.0). |
| `src/scorers/context_scorer.py` | Multi-factor fundamental/sentiment scoring engine. Evaluates Analyst Targets (max 30), Earnings Momentum (max 30), Fundamental Safety (max 20), News Sentiment (max 20), and Price/Volume events (max 15). **Returns raw [0, 100] scale score** (fixed pre-scaling bug). |
| `src/providers/context/aggregator.py` | Aggregates data from Analyst, Earnings, Fundamental, and News providers. Maintains 24-hour caching in Supabase `context_cache` table to optimize execution time. |
| `src/providers/context/earnings_provider.py` | Fetches quarterly earnings surprise metrics using yfinance `.earnings_dates` API. Calculates `(actual_eps - estimated_eps) / abs(estimated_eps) * 100`. |
| `src/providers/context/news_provider.py` | Fetches Google News RSS feeds using `feedparser 6.0.14` and evaluates news headline sentiment using FinBERT NLP model. |
| `jobs/supabase_client.py` | Provides configured Supabase client using `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`. Exposes helper functions for position updates, exits (`execute_position_exit`), and history tracking. |
| `jobs/strategies/` | Strategy definitions: `pullback.py`, `trend_following.py`, `sector_rotation.py`, `pead.py`, `week_52_high.py`, `cross_sectional.py`, `mean_reversion.py`. |
| `frontend/src/components/recommendations-table.tsx` | Next.js frontend UI table component built with TanStack Table and Tailwind CSS. Renders recommended stocks, entry prices, stops, targets, fractional share counts, and action buttons. |

---

## 4. Quantitative Strategy Suite (7 Strategies)

| Strategy Name | Active Regimes | Entry Trigger | Stop-Loss Rules | Profit Target Rules |
| :--- | :--- | :--- | :--- | :--- |
| **Pullback Recovery** | Bull, Sideways | Price $> 50\text{ DMA}$, $45 \le \text{RSI} \le 67$, 10-day $\text{RSI}_{\min} < 52$, Vol Ratio $\ge 1.0\text{x}$ | 20-day swing low, bounded by max 7.0% loss floor | $T_1 = 1.5\text{ATR}$, $T_2 = 2.5\text{ATR}$, $T_3 = 3.5\text{ATR}$ |
| **Trend Following** | Bull, Sideways | Price $> 50\text{ DMA} > 200\text{ DMA}$, $\text{ADX} \ge 18.0$, Breakout above 20-day High | $\max(\text{Low}_{10}, \text{Entry} - 2.5\text{ATR})$ | $T_1 = +12\%$, $T_2 = +22\%$, $T_3 = +35\%$ (Trailing Stop) |
| **Sector Rotation** | Bull, Sideways | Top 3 performing sector ETFs over 20 days; ticker in top quantile of sector | $\text{SMA}_{50} \times 0.97$ (3% below 50 DMA) | $T_1 = +8\%$, $T_2 = +15\%$, $T_3 = +22\%$ |
| **Post-Earnings Drift (PEAD)** | All Regimes | Quarterly earnings beat $> +5\%$, gap up $> 2\%$, volume $> 2.0\text{x}$ average | $\max(\text{SMA}_{50} \times 0.98, \text{Gap Low} \times 1.02)$ | $T_1 = +8\%$, $T_2 = +15\%$, $T_3 = +22\%$ |
| **52-Week High Breakout** | Bull | Price within 3% of 52-week high, volume ratio $\ge 1.3\text{x}$, RSI $\le 72$ | $\max(\text{SMA}_{50} \times 0.97, \text{High}_{52w} \times 0.95)$ | $T_1 = +10\%$, $T_2 = +18\%$, $T_3 = +28\%$ |
| **Cross-Sectional Momentum** | Bull | Top 10% 60-day price momentum across 500 universe tickers, ADX $\ge 20$ | $\text{SMA}_{50} \times 0.97$ | $T_1 = +10\%$, $T_2 = +18\%$, $T_3 = +25\%$ |
| **Mean Reversion** | Bear only | Oversold RSI $< 32$, price $> 200\text{ DMA}$, bounce candlestick pattern | 2-day low $\times 0.98$ | $T_1 = +5\%$, $T_2 = +10\%$, $T_3 = +15\%$ |

---

## 5. Mathematical Specification of Scoring & Allocation

### A. Sub-Component Scores (Scale: 0 to 100)

1. **Technical Momentum Score ($S_{\text{mom}}$)**:
   $$S_{\text{rsi}} = \text{clip}(100 - |RSI - 50| \times 4, 0, 100)$$
   $$S_{\text{dma}} = \text{clip}\left(100 - \left|\frac{Price}{50\text{DMA}} - 1\right| \times 500, 0, 100\right)$$
   $$S_{\text{vol}} = \text{clip}(\text{Volume Ratio} \times 50, 0, 100)$$
   $$S_{\text{macd}} = \text{clip}(50.0 + \text{MACD Histogram} \times 200.0, 0, 100)$$
   $$S_{\text{raw}} = \frac{S_{\text{rsi}} + S_{\text{dma}} + S_{\text{vol}} + S_{\text{macd}}}{4}$$
   $$S_{\text{mom}} = \text{PercentileRank}(S_{\text{raw}}) \times \frac{1}{1 + e^{-(S_{\text{raw}} - 55)/5}}$$

2. **Risk-Adjusted Expectancy Score ($S_{\text{exp}}$)**:
   $$Z = \frac{\text{Expectancy}_{\%} - \mu_{\text{exp}}}{\sigma_{\text{exp}}}$$
   $$S_{\text{exp}} = \frac{100}{1 + e^{-Z}}$$
   *Negative Penalty*: If $\text{Expectancy}_{\%} < 0$, $S_{\text{exp}} = \max(5.0, S_{\text{exp}} - 30.0)$.

3. **Historical Win Rate Score ($S_{\text{wr}}$)**:
   $$S_{\text{wr}} = \text{PercentileRank}(\text{Win Rate}_{\%})$$

4. **Regime Alignment Score ($S_{\text{reg}}$)**:
   * Bull: $100.0$ if $50 \le RSI \le 70$ AND $Price > 50\text{DMA}$, else $0.0$.
   * Bear: $100.0$ if Defensive Sector or $\text{Beta} < 1.0$, else $0.0$.
   * Sideways: $100.0$ if $|RSI - 50| < 8.0$, else $0.0$.

5. **Context Score ($S_{\text{ctx}}$)**:
   * Aggregates Analyst Target Upside (max 30), Earnings Surprise (max 30), Fundamentals (max 20), FinBERT News (max 20), Price/Volume Signal (max 15). Clamped to `[0, 100]` raw scale.

### B. Composite Score Formula & Weight Vector

$$\text{Composite Score} = (w_{\text{mom}} \cdot S_{\text{mom}}) + (w_{\text{exp}} \cdot S_{\text{exp}}) + (w_{\text{wr}} \cdot S_{\text{wr}}) + (w_{\text{reg}} \cdot S_{\text{reg}}) + (w_{\text{ctx}} \cdot S_{\text{ctx}})$$

| Market Regime | Momentum ($w_{\text{mom}}$) | Expectancy ($w_{\text{exp}}$) | Win Rate ($w_{\text{wr}}$) | Regime ($w_{\text{reg}}$) | Context ($w_{\text{ctx}}$) | Sum of Weights |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BULL** | **30%** | **30%** | **15%** | **10%** | **15%** | **100%** |
| **SIDEWAYS** | **25%** | **35%** | **15%** | **10%** | **15%** | **100%** |
| **BEAR** | **15%** | **35%** | **10%** | **10%** | **30%** | **100%** |

### C. Position Sizing & Fractional Share Math

For a portfolio value $PV$ (e.g. $10,000$):
$$\text{Max Allocation Dollars} = 0.05 \times PV = \$500.00$$
$$\text{Exact Shares} = \text{round}\left(\frac{\text{Allocated Dollars}}{\text{Entry Price}}, 4\right)$$
$$\text{Position Sizing Label} = \begin{cases} \text{"K: 5.0\% (0.88 sh)"} & \text{if Exact Shares } < 1.0 \\ \text{"K: 5.0\% (14 sh)"} & \text{if Exact Shares } \ge 1.0 \end{cases}$$

---

## 6. Database Schema & RPC Specs

### `signals` Table (Active Signals)
```sql
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    industry TEXT,
    strategy TEXT NOT NULL,
    strategy_name TEXT,
    entry_price NUMERIC(10,2) NOT NULL,
    stop_loss NUMERIC(10,2) NOT NULL,
    target_1 NUMERIC(10,2),
    target_2 NUMERIC(10,2),
    target_3 NUMERIC(10,2),
    target_1_pct NUMERIC(6,2),
    target_2_pct NUMERIC(6,2),
    target_3_pct NUMERIC(6,2),
    composite_score NUMERIC(6,2) NOT NULL,
    quality_score NUMERIC(6,2),
    tier_label TEXT NOT NULL, -- 'Strong Buy', 'Buy', 'Watch', 'Speculative'
    allocated_dollars NUMERIC(10,2) NOT NULL,
    max_shares INTEGER NOT NULL,
    position_sizing TEXT, -- e.g. 'K: 5.0% (0.88 sh)'
    status TEXT NOT NULL DEFAULT 'open', -- 'open', 'closed'
    regime TEXT NOT NULL,
    current_rsi NUMERIC(5,2),
    volume_ratio NUMERIC(5,2),
    adx_value NUMERIC(5,2),
    context_score NUMERIC(6,2),
    context_analyst NUMERIC(6,2),
    context_earnings NUMERIC(6,2),
    context_fundamental NUMERIC(6,2),
    context_news NUMERIC(6,2),
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### RPC `execute_position_exit` (Atomic Exits)
```sql
CREATE OR REPLACE FUNCTION execute_position_exit(
    p_signal_id UUID,
    p_exit_price NUMERIC,
    p_outcome TEXT,
    p_reason TEXT,
    p_split_fraction NUMERIC DEFAULT 1.0,
    p_live_price NUMERIC DEFAULT NULL,
    p_move_stop_to_entry BOOLEAN DEFAULT FALSE
) RETURNS JSONB ...
```

---

## 7. Audit & Bug Fix History (Verified Status)

1. **Context Scorer Double-Scaling Bug [FIXED]**:
   - *Problem*: `ContextScorer.calculate()` returned `raw_score * 0.15`. `SignalRanker` then applied `w["ctx"] * context_score` ($0.15 \times 0.15 = 0.0225$), capping context influence at **~0.9%** instead of **15%**.
   - *Fix*: `ContextScorer` now returns raw `[0, 100]` score. Weighting is applied exclusively by `SignalRanker`.
2. **Fallback Context Composite Recomputation [FIXED]**:
   - *Problem*: On-the-fly context fallback used an ad-hoc formula (`old_score + context_score * 0.15`).
   - *Fix*: Replaces ad-hoc math with proper `ranker.compute_composite_score()` call using regime-specific weights.
3. **yfinance Earnings API Deprecation [FIXED]**:
   - *Problem*: `stock.earnings` was deprecated in yfinance, returning `None`.
   - *Fix*: Updated to `stock.earnings_dates` API to extract reported vs estimated EPS surprise percentages.
4. **Google News feedparser Deprecation [FIXED]**:
   - *Problem*: `feedparser 6.0.10` failed on Python 3.13+ due to missing `cgi` module.
   - *Fix*: Upgraded to `feedparser 6.0.14`.

---

## 8. Master Enhancement Guide: 7 Core Categories for LLM Suggestions

If you are an LLM prompt user or assistant analyzing this project for architectural, quantitative, or engineering enhancements, evaluate and suggest improvements across these **7 core domains**:

### Domain 1: Quantitative Strategy & Alpha Generation
* **Multi-Timeframe Trend Confirmation**: Incorporating 1-hour or 4-hour EMA alignment before confirming daily pullback setups.
* **Dynamic ATR Target Expansion**: Expanding target multipliers ($T_1 = 2.0\text{ATR}, T_2 = 3.5\text{ATR}$) during high-volatility expansion regimes ($\text{VIX} > 25$).
* **Volume Profile & VWAP Anchoring**: Adding Volume-Weighted Average Price (VWAP) support/resistance bounce confirmation to `pullback.py`.
* **Volatility Squeeze Signals**: Integrating Bollinger Band / Keltner Channel squeeze expansion detection.

### Domain 2: NLP & Alternative Data Context
* **LLM Microservice for News Sentiment**: Replacing local FinBERT pipeline with a lightweight Llama-3 / Claude 3.5 Sonnet microservice for deep financial news reasoning.
* **Social Sentiment & Options Flow Integration**: Integrating Reddit WallStreetBets / Unusual Whales options order flow sentiment.
* **SEC Form 4 Insider Buying Signals**: Incorporating cluster insider purchases by C-suite executives.

### Domain 3: Portfolio Construction & Risk Controls
* **Sector Concentration Limits**: Enforcing a strict **15% max sector exposure cap** (e.g. maximum 3 concurrent Tech positions) to eliminate industry cluster risk.
* **Correlation-Adjusted Sizing**: Scaling down position sizes for candidate stocks displaying $> 0.85$ correlation with active open positions.
* **Dynamic Portfolio Kelly Adjustment**: Scaling Kelly fraction dynamically based on trailing 30-day equity curve drawdown.

### Domain 4: Engineering, Database & Real-Time Sync
* **Supabase Realtime WebSockets**: Replacing client-side polling with Supabase Realtime channels (`supabase.channel('signals')`) for instantaneous frontend UI updates.
* **Vectorized Event-Driven Backtesting Engine**: Building an offline event-driven backtesting suite (`scripts/backtest_engine.py`) using date-partitioned parquet cache files to evaluate 5-year Sharpe ratio, Maximum Drawdown, and Win Rate under the 5% cap.
* **Redis In-Memory Caching**: Replacing file-based parquet cache with Redis for sub-millisecond ticker history lookups.

### Domain 5: Frontend Experience & Visual Analytics
* **Interactive Equity Curve & Drawdown Visualizer**: Adding a Recharts timeline showing portfolio growth vs. S&P 500 benchmark.
* **Trade Breakdown Radar Chart**: Adding an interactive radar chart modal in `recommendations-table.tsx` displaying candidate sub-scores (Momentum, Expectancy, Win Rate, Regime, Context).
* **Live Trade Execution Simulator / Paper Trading Toggle**: Adding one-click paper trade execution with simulated entry/exit tracking.

### Domain 6: Machine Learning & Adaptive Weights
* **Reinforcement Learning Weight Adaptation**: Implementing a Q-learning / Contextual Bandit model to continuously optimize sub-score weights ($w_{\text{mom}}, w_{\text{exp}}, w_{\text{wr}}, w_{\text{reg}}, w_{\text{ctx}}$) based on rolling 60-day strategy performance.
* **XGBoost Setup Classifier**: Training an XGBoost classifier on historical 5-year price/volume features to output trade success probability.

### Domain 7: Automated Alerting & Notification Ecosystem
* **Telegram / Discord Bot Notifications**: Pushing real-time trade signals, $T_1$ partial exits, and trailing stop triggers directly to a Telegram channel or Discord webhook.
* **Interactive SMS / Email Signals**: Weekly summary reports of portfolio performance, win rate, and realized P&L.

---

## 9. Verification & Execution Checklist

When modifying or testing code in this workspace:
1. **Frontend Build Verification**: `cd frontend && npm run build` (Ensures zero TypeScript/JSX errors).
2. **Backend Signal Generation Test**: `python -m jobs.generate_signals` (Executes 502-ticker scan, composite scoring, 5% sizing caps, and Supabase writes).
3. **Fresh Signals Check**: `python scratch/check_fresh_signals.py` (Confirms active signals in Supabase have positive dollar allocations and valid stops/targets).
