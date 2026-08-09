# Master LLM Project Context: Stock Recommendation Engine (Strategy 1.3 Rev B)

This document serves as the definitive reference manual for the **Stock Recommendation Engine** codebase. It provides a complete, structured context covering the engine's core algorithm (Strategy 1.3 Rev B), scoring mechanics, directory architecture, database schema, risk allocation caps, frontend UI specifications, and enhancement opportunities.

---

## 1. Executive Summary & Core Philosophy

The **Stock Recommendation Engine** is an automated, data-driven quantitative stock scanner and portfolio management system (inspired by platforms like Danelfin). It operates nightly to scan a universe of **500+ US Equities** (S&P 500 + Nasdaq-100), identify high-probability swing trading setups, score candidates using regime-dependent multi-factor models, enforce strict risk allocation controls, and render real-time trade signals on a Next.js web application.

### Key Trading Principles Enforced:
1. **Regime-Aware Execution**: Technical indicators and composite weights dynamically adapt depending on whether the market is in a **Bull**, **Bear**, or **Sideways** regime.
2. **Strict Single-Stock Allocation Cap**: No individual stock position can receive more than **5.0% of total portfolio capital** at entry.
3. **High-Confidence Filtering**: Only top-tier setups meeting a **Composite Score $\ge 80.0$** are output, capping nightly recommendations at the **Top 2–3 setups**.
4. **Asymmetric Risk-Reward Exits**: Every trade features ATR-scaled profit targets ($T_1, T_2, T_3$), automated partial profit taking (50% scale-out at $T_1$), stop-loss ratcheting to breakeven, a hard **7.0% max loss ceiling**, and nightly trailing stops ($2.0 \times \text{ATR}$).

---

## 2. System Architecture & Component Map

```
 +-----------------------------------------------------------------------------------+
 |                                 EXTERNAL APIS                                     |
 |  - yfinance (OHLCV, Volume, ADX, RSI, SPY, ^VIX, Analyst Targets, Earnings)       |
 |  - Tiingo API (Intraday price monitoring)                                         |
 +-----------------------------------------+-----------------------------------------+
                                           |
                                           v
 +-----------------------------------------------------------------------------------+
 |                            PYTHON BACKEND & SCANNER                               |
 |                                                                                   |
 |  jobs/generate_signals.py ──> Main pipeline controller & signal generation        |
 |  src/ranker.py            ──> Composite ranking & 5% capital allocation engine     |
 |  src/market_context.py    ──> Market regime detection (SPY 200-DMA & VIX override)  |
 |  src/providers/context/   ──> Context aggregator (Analyst, Earnings, News, P/E)   |
 |  src/scorers/             ──> Sub-component scoring engines                       |
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
 |  - context_cache     : 24-hour cached context scores (analyst, news, P/E)          |
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

## 3. Data Pipeline & API Integration Details

| Provider | Purpose | Trigger / Frequency | Fallback Logic |
| :--- | :--- | :--- | :--- |
| **`yfinance` (History)** | Daily OHLCV price history (125 daily bars) | Nightly scan run | Local Parquet cache (`data/cache/by_date`) |
| **`yfinance` (SPY)** | SPY Close vs. 200-day DMA for regime sensing | Nightly scan run | Defaults to Sideways regime if fetch fails |
| **`yfinance` (^VIX)** | Emergency Market Volatility Check | Nightly scan run | Skips VIX override if empty |
| **`yfinance` (Info)** | Analyst target mean price, P/E ratio, Revenue Growth | Nightly scan run | Neutral score (`0.0`) if fields missing |
| **`Supabase REST API`** | Persistent DB reads/writes & context caching | Real-time & Nightly | In-memory local fallback calculations |
| **`Tiingo API`** | Intraday price updates for active position monitoring | Every 15 minutes | Uses latest close bar if API rate-limited |

---

## 4. Strategy 1.3 Rev B Execution Rules

### A. Regime Sensing & Strategy Activation
* **Sensing Asset**: `SPY` (S&P 500 ETF).
* **Reference Line**: 200-day Simple Moving Average (SMA).
* **VIX Emergency Check**: If `^VIX > 40.0`, force **BEAR regime** and cut sizing by 50%.
* **Regime Strategy Mapping**:
  * **BULL Regime**: `Pullback Recovery`, `Trend Following`, `Sector Rotation`, `52-Week High`, `Cross-Sectional Momentum`.
  * **BEAR Regime**: `Mean Reversion`, `Post-Earnings Drift` (defensive strategies only).
  * **SIDEWAYS Regime**: All strategies active with increased expectancy weighting.

### B. Quality Gates & Qualification Rules
1. **Regime-Aware Trend Gate**:
   * **Bull**: `Price > 50 DMA` (relaxed to catch early pullbacks).
   * **Bear/Sideways**: `Price > 50 DMA > 200 DMA` (strict trend confirmation).
2. **RSI Pullback-Recovery Gate**:
   * Wilder RSI(14) minimum in last 10 days: `rsi_min_10d < 52.0`.
   * Current RSI(14): `45.0 <= RSI <= 67.0` (confirming turnaround).
3. **ADX Trend Strength Gate**:
   * Bull regime: `ADX(14) >= 15.0`.
   * Bear/Sideways regime: `ADX(14) >= 18.0`.
4. **Volume Confirmation Gate**: `Volume Ratio >= 1.0x` (20-day SMA).
5. **Risk Ceiling Gate**: Hard stop loss ceiling capping risk at **max 7.0% below entry price**: `stop_loss = max(stop_loss, entry_price * 0.93)`.
6. **Max Gap Filter**: Rejects setup if single-day drop in last 5 days exceeds `5.0%`.
7. **Earnings Filter**: Rejects setup if earnings date is within `7` days.
8. **Momentum Exception**: Bypasses RSI pullback if: `Price > 50 DMA + 20%`, `Volume Ratio >= 1.5x`, `ADX >= 20.0`, and `RSI <= 75.0`.

---

## 5. Mathematical Specification of Scoring Formulas

Candidate scoring is handled by `SignalRanker` ([src/ranker.py](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/ranker.py)).

### A. Component Score Calculations (0 to 100)

1. **Technical Momentum Score ($S_{\text{mom}}$)**:
   * **RSI Proximity**: $S_{\text{rsi}} = \text{clip}(100 - |RSI - 50| \times 4, 0, 100)$
   * **50 DMA Proximity**: $S_{\text{dma}} = \text{clip}\left(100 - \left|\frac{Price}{50DMA} - 1\right| \times 500, 0, 100\right)$
   * **Volume Score**: $S_{\text{vol}} = \text{clip}(\text{Volume Ratio} \times 50, 0, 100)$
   * **MACD Score**: $S_{\text{macd}} = \text{clip}(50.0 + \text{MACD Histogram} \times 200.0, 0, 100)$
   * Raw Average: $S_{\text{raw}} = \frac{S_{\text{rsi}} + S_{\text{dma}} + S_{\text{vol}} + S_{\text{macd}}}{4}$
   * Sigmoid Normalization: $S_{\text{mom}} = \text{PercentileRank}(S_{\text{raw}}) \times \frac{1}{1 + e^{-(S_{\text{raw}} - 55)/5}}$

2. **Risk-Adjusted Expectancy Score ($S_{\text{exp}}$)**:
   * Z-score of backtested expectancy: $Z = \frac{\text{Expectancy}_{\%} - \mu_{\text{exp}}}{\sigma_{\text{exp}}}$
   * Sigmoid Mapping: $S_{\text{exp}} = \frac{100}{1 + e^{-Z}}$
   * Penalty: If $\text{Expectancy}_{\%} < 0$, $S_{\text{exp}} = \max(5.0, S_{\text{exp}} - 30.0)$.

3. **Historical Win Rate Score ($S_{\text{wr}}$)**:
   * $S_{\text{wr}} = \text{PercentileRank}(\text{Win Rate}_{\%})$

4. **Regime Alignment Score ($S_{\text{reg}}$)**:
   * **Bull**: $100.0$ if $50 \le RSI \le 70$ AND $Price > 50 DMA$, else $0.0$.
   * **Bear**: $100.0$ if Industry is Defensive OR Beta $< 1.0$, else $0.0$.
   * **Sideways**: $100.0$ if $|RSI - 50| < 8.0$, else $0.0$.

5. **Context Score ($S_{\text{ctx}}$)**:
   * Aggregates Analyst Target Upside ($+3$), Positive Earnings Surprise ($+3$), P/E & Growth ($+3$), FinBERT News Sentiment ($+3$), Technical Alignment ($+3$). Total max: $15.0$.

### B. Regime-Dependent Composite Weighting

$$\text{Composite Score} = (w_{\text{mom}} \cdot S_{\text{mom}}) + (w_{\text{exp}} \cdot S_{\text{exp}}) + (w_{\text{wr}} \cdot S_{\text{wr}}) + (w_{\text{reg}} \cdot S_{\text{reg}}) + (w_{\text{ctx}} \cdot S_{\text{ctx}})$$

| Regime | Momentum ($w_{\text{mom}}$) | Expectancy ($w_{\text{exp}}$) | Win Rate ($w_{\text{wr}}$) | Regime ($w_{\text{reg}}$) | Context ($w_{\text{ctx}}$) | Total Weight |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BULL** | **30%** | **30%** | **15%** | **10%** | **15%** | **100%** |
| **SIDEWAYS** | **25%** | **35%** | **15%** | **10%** | **15%** | **100%** |
| **BEAR** | **15%** | **35%** | **10%** | **10%** | **30%** | **100%** |

---

## 6. Risk Management & Position Sizing Architecture

### A. 5.0% Single-Stock Allocation Hard Cap
No single position may receive more than **5.0% of total portfolio value** ($PV$) at entry:
$$\text{Max Single Stock Allocation} = 0.05 \times PV$$

In [src/ranker.py](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/ranker.py#L495-L538) (`calculate_normalized_sizing`), double-capping is enforced:
1. **Pre-normalization cap**: $\text{Raw Demand}_i = \min(PV \times \text{HalfKelly}_i, 0.05 \times PV)$
2. **Post-normalization cap**: $\text{Final Dollar}_i = \min(\text{Raw Demand}_i \times \text{Multiplier}, 0.05 \times PV)$
3. **Share floor**: $\text{Max Shares}_i = \lfloor \frac{\text{Final Dollar}_i}{\text{Entry Price}_i} \rfloor$. If $\text{Max Shares}_i == 0$, $\text{Final Dollar}_i$ is zeroed out.

### B. Exit Architecture & Profit Scale-Outs
* **$T_1$ Target ($1.5 \times \text{ATR}$)**: Sells **50% of position lot** and ratchets stop loss to **breakeven** (`entry_price`).
* **$T_2$ Target ($2.5 \times \text{ATR}$)**: Sells **30% of position lot**.
* **$T_3$ Target ($3.5 \times \text{ATR}$)**: Sells remaining **20% of position lot**.
* **Trailing Stop Ratcheting**: Nightly job updates stop loss if price advances: $\text{Stop}_{\text{new}} = \max(\text{Stop}_{\text{current}}, \text{Price}_{\text{current}} - 2.0 \times \text{ATR})$.
* **SQL RPC Procedure**: Database procedure `execute_position_exit()` performs atomic partial exits, lot splitting, and portfolio equity updates in Postgres.

---

## 7. Database Schema & RPC Reference

### `signals` Table
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary Key |
| `scan_date` | `date` | Date of scan execution |
| `ticker` | `text` | Stock ticker symbol |
| `entry_price` | `numeric` | Execution trigger price |
| `stop_loss` | `numeric` | Stop-loss price |
| `target_1`, `target_2`, `target_3` | `numeric` | Scale-out profit targets |
| `composite_score` | `numeric` | Overall 0–100 composite score |
| `tier_label` | `text` | "Strong Buy", "Buy", "Watch", "Speculative" |
| `allocated_dollars` | `numeric` | Dollar allocation (capped at 5% of portfolio) |
| `max_shares` | `integer` | Number of shares allocated |
| `status` | `text` | "pending", "open", "closed" |

### `portfolio_state` Table
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `uuid` | Primary Key |
| `date` | `date` | Record date |
| `portfolio_value` | `numeric` | Current total equity value |
| `peak_value` | `numeric` | All-time high equity value |
| `current_drawdown_pct` | `numeric` | Equity drawdown percentage from peak |

---

## 8. Guide for Future LLMs: High-Impact Improvement Opportunities

If you are an LLM reading this codebase to suggest architectural, quantitative, or engineering improvements, focus on these **5 high-impact areas**:

### 1. Quantitative Strategy & Signal Generation
* **Fractional Share Support for Expensive Equities**: Currently, stocks priced $> 5.0\% \times PV$ (e.g. $\$1,185$ stock on a $\$10,000$ account) receive $0$ shares due to whole-share rounding. Adding fractional share execution logic will enable trading high-priced mega-caps like LLY, NVD, or BKNG.
* **Dynamic ATR Multiplier by Market Volatility**: Adjust profit target ATR multipliers ($T_1 = 1.5\text{ATR}$) dynamically based on VIX regime (e.g. expand to $2.0\text{ATR}$ in high-volatility regimes).
* **Multi-Timeframe Trend Confirmation**: Require 1-hour or 4-hour EMA alignment before confirming daily pullback recovery.

### 2. Context & NLP Aggregation
* **Real-time News Sentiment Engine**: Enhance `src/providers/context/news_provider.py` with FinBERT / Llama-3 microservice to analyze news headlines with higher domain accuracy than standard VADER.
* **Earnings Surprise Drift Scoring**: Incorporate 3-day post-earnings announcement drift (PEAD) momentum metrics into `earnings_provider.py`.

### 3. Risk Management & Portfolio Construction
* **Sector Concentration Limits**: Implement a max **15% sector allocation cap** (e.g. max 3 Tech stocks concurrently) to prevent sector cluster risk.
* **Correlation-Adjusted Sizing**: Reduce allocation sizes for candidates that exhibit $> 0.85$ 60-day price correlation with existing open positions.

### 4. Database & Backtesting Validation
* **Vectorized Event-Driven Backtester**: Build an offline Python event-driven backtesting engine (`scripts/backtest_engine.py`) using historical date-partitioned parquet files to simulate 5-year strategy equity curves under the 5% cap.
* **Supabase Real-Time Subscriptions**: Migrate frontend polling to WebSocket Supabase realtime channel (`supabase.channel('signals')`) for instant position exit updates.

### 5. Frontend & Visual Analytics
* **Interactive Equity Curve & Drawdown Chart**: Add a Recharts / Chart.js visual timeline of `portfolio_state` history showing equity progression vs. S&P 500 baseline.
* **Trade Detail Modal**: Add a modal drawer in `recommendations-table.tsx` showing the full technical breakdown spider/radar chart (Momentum, Expectancy, Win Rate, Regime, Context).

---

## 9. Developer Verification Checklist

When making changes to this codebase, always run:
1. **Frontend Build Verification**: `cd frontend && npm run build` (Ensures zero TypeScript/JSX errors).
2. **Backend Dry-Run Verification**: `python -m jobs.generate_signals --dry-run` (Validates indicator calculations and 5% sizing caps).
3. **Allocation Cap Audit**: `python scratch/test_allocation_gate.py` (Confirms `allocated_dollars <= 0.05 * portfolio_value`).
