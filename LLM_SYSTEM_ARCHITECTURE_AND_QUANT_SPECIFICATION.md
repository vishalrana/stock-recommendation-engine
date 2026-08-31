# Stock Recommendation Engine — Master Architecture & Quantitative Specification

> **Document Type**: Comprehensive Quantitative & Technical Architecture Specification  
> **Target Audience**: Quantitative Developers, Trading Systems Architects, and Large Language Models (LLMs)  
> **System Classification**: Multi-Strategy Systematic Equity Momentum & Mean-Reversion CTA Engine  
> **Primary Asset Universe**: S&P 500 (~502 constituents) + Selected Sector ETFs (~15 tickers)

---

## Table of Contents
1. [Executive Summary & Operational Paradigm](#1-executive-summary--operational-paradigm)
2. [End-to-End System Architecture](#2-end-to-end-system-architecture)
3. [Data Ingestion & Storage Architecture](#3-data-ingestion--storage-architecture)
4. [Macro Market Regime Detection](#4-macro-market-regime-detection)
5. [Strategy Mathematical Specifications](#5-strategy-mathematical-specifications)
6. [Multi-Factor Composite Scoring Engine](#6-multi-factor-composite-scoring-engine)
7. [Context & Multimodal Scoring Pipeline](#7-context--multimodal-scoring-pipeline)
8. [Risk Management, Sizing & Capital Allocation Math](#8-risk-management-sizing--capital-allocation-math)
9. [Trade Lifecycle & Execution Engine](#9-trade-lifecycle--execution-engine)
10. [Real-World Calculation Walkthroughs (Live Production Data)](#10-real-world-calculation-walkthroughs-live-production-data)
11. [Quantitative Expectancy & Probabilistic Edge](#11-quantitative-expectancy--probabilistic-edge)
12. [Database Schema & Data Flow Specification](#12-database-schema--data-flow-specification)

---

## 1. Executive Summary & Operational Paradigm

The **Stock Recommendation Engine** is an institutional-grade, daily-cadence algorithmic trading system designed to identify high-probability swing and trend-following opportunities across US equities.

### Core Mathematical Philosophy:
1. **Asymmetric Payoff Structure**: The system operates on a classical CTA trend-following expectancy model:
   $$\text{Expectancy} = (P_{\text{win}} \times \overline{\text{Win}}) - (P_{\text{loss}} \times \overline{\text{Loss}})$$
   Rather than optimizing for an unnaturally high win rate (e.g., >70%), the engine targets a **33%–40% win rate** with an average risk-to-reward ratio of **1:2.5 to 1:3.5**, producing strong positive mathematical expectancy.
2. **Strict Capital Preservation**:
   - Single-stock exposure is strictly capped at **5.0% of total portfolio value**.
   - Stop-losses are bounded between a **4.0% minimum noise floor** and a **7.0% hard loss ceiling**.
   - Sizing uses **Half-Kelly Criterion** scaled by portfolio drawdown status.
3. **Multi-Horizon Scale-Outs**: Winning trades lock in profit across 3 sequential tiers:
   - **Target 1 (+10% to +12%)**: 50% position exit + stop loss ratcheted to Breakeven.
   - **Target 2 (+18% to +22%)**: 30% position exit.
   - **Target 3 (+25% to +35%)**: Remaining 20% position exit (runner).

---

## 2. End-to-End System Architecture

```
                                  [ MARKET DATA INGESTION ]
                                             │
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
            Yahoo Finance / Wikipedia                     Tiingo / Finnhub API
            (Historical OHLCV + Tickers)                 (Live Quotes & Intraday)
                        │                                         │
                        ▼                                         ▼
         ┌─────────────────────────────┐           ┌─────────────────────────────┐
         │ data/cache/by_date/*.parquet│           │  frontend/api/sync-market   │
         └──────────────┬──────────────┘           └──────────────┬──────────────┘
                        │                                         │
                        ▼                                         │
           [ JOBS: SIGNAL GENERATION ]                            │
                        │                                         │
          ┌─────────────┴─────────────┐                           │
          ▼                           ▼                           │
   Regime Classifier           Strategy Scanners                  │
   (SPY vs 200 DMA + VIX)     (6 Active Strategies)               │
          │                           │                           │
          └─────────────┬─────────────┘                           │
                        ▼                                         │
           Quality Gate (Buy / Strong Buy)                        │
                        │                                         │
                        ▼                                         │
             Context Enrichment Layer                             │
             (Analyst + Earn + Fund + NLP)                        │
                        │                                         │
                        ▼                                         │
           Half-Kelly Position Sizing                             │
           (5% Portfolio Cap + Stop Clamping)                     │
                        │                                         │
                        ▼                                         │
         ┌─────────────────────────────┐                          │
         │  SUPABASE POSTGRESQL DB     │ ◄────────────────────────┘
         │  - signals (open/pending)   │
         │  - signals_history (closed) │
         │  - portfolio_state          │
         │  - scan_log                 │
         └──────────────┬──────────────┘
                        │
                        ▼
         [ FRONTEND: NEXT.JS 16 DASHBOARD ]
         - Server Component Direct Queries
         - Trade Lifecycle Evaluator (Morning Open & Exits)
         - Real-Time P&L & Allocation Monitoring
```

---

## 3. Data Ingestion & Storage Architecture

### 3.1 Date-Partitioned Parquet Cache
Market data is stored in `data/cache/by_date/{YYYY-MM-DD}.parquet`. Each file contains a MultiIndex DataFrame indexed by `(Ticker, Date)` containing:
$$\text{Columns} = [\text{OPEN}, \text{HIGH}, \text{LOW}, \text{CLOSE}, \text{VOLUME}]$$

### 3.2 Data Hygiene & Corruption Protection
To prevent incomplete market close snapshots from poisoning technical indicators:
1. **Write-Time Validation**:
   $$\text{Reject File if } \frac{\sum \text{is\_null}(\text{CLOSE})}{N_{\text{total}}} > 0.50$$
2. **Preload-Time Validation**:
   Files with $>50\%$ null values in `CLOSE` are skipped and logged during historical preloading.

---

## 4. Macro Market Regime Detection

Market state is evaluated nightly using SPY historical price vs its 200-day Simple Moving Average (DMA) and CBOE Volatility Index (VIX):

$$\text{Regime} = \begin{cases} 
\mathbf{BULL}, & \text{if } \text{Price}_{\text{SPY}} > \text{SMA}_{200}(\text{SPY}) \\ 
\mathbf{BEAR}, & \text{if } \text{Price}_{\text{SPY}} \le \text{SMA}_{200}(\text{SPY}) 
\end{cases}$$

### Volatility Modifier (VIX):
- **VIX < 20**: Normal volatility ($1.0\times$ sizing multiplier).
- **20 ≤ VIX < 30**: Elevated volatility ($0.8\times$ sizing multiplier).
- **VIX ≥ 30**: High risk regime ($0.5\times$ sizing multiplier, Mean Reversion disabled).

---

## 5. Strategy Mathematical Specifications

The engine runs **6 active strategies** in Bull regimes and **1 specialized strategy** in Bear regimes:

| Strategy | Market Regime | Primary Indicators | Entry Trigger | Stop-Loss Calculation |
| :--- | :--- | :--- | :--- | :--- |
| **Pullback Recovery** | Bull / Sideways | RSI(14), 50 DMA, 200 DMA, ADX(14) | Close > 50 DMA, RSI was $\le 45$ in last 10 days, RSI crossing up | $\min(\text{Low}_{10}, \text{Entry} - 2.0 \times \text{ATR}_{14})$ |
| **Trend Following** | Bull / Sideways | EMA(20), SMA(50), SMA(200), ADX(14) | Close > EMA(20) > SMA(50) > SMA(200), ADX > 25 | $\min(\text{Low}_{10}, \text{Entry} - 2.5 \times \text{ATR}_{14})$ |
| **52-Week High Breakout** | Bull | 52-Week High, Volume, ADX(14) | Close within 3% of 52W High, Volume $> 1.5\times$ VolMA20 | $\min(\text{SMA}_{50} \times 0.97, \text{High}_{52\text{W}} \times 0.95)$ |
| **Cross-Sectional Momentum**| Bull | 3-Month Return Rank, EMA(20) | Top 15% 3-month performance across universe, Close > EMA(20) | $\text{Entry} - 2.0 \times \text{ATR}_{14}$ |
| **Sector Rotation** | Bull / Sideways | 1-Month vs 3-Month ETF Return | Top 3 momentum sector ETFs vs SPY | $\text{Entry} - 2.5 \times \text{ATR}_{14}$ |
| **Post-Earnings Drift (PEAD)**| Bull / Sideways | Earnings Surprise %, Gap % | EPS Beat $> +5\%$, Gap Up $+2\%$ to $+8\%$, Volume $> 2\times$ | $\min(\text{SMA}_{50} \times 0.98, \text{GapLow} \times 1.02)$ |
| **Mean Reversion** | Bear (Oversold) | RSI(14), Bollinger Bands (20, 2) | RSI $< 30$, Close $<$ Lower Bollinger Band | $\text{Entry} - 1.5 \times \text{ATR}_{14}$ |

---

## 6. Multi-Factor Composite Scoring Engine

Candidate signals that pass technical strategy triggers are scored on a scale from $0.0$ to $100.0$ using a regime-weighted linear combination:

$$\text{Composite Score} = w_{\text{mom}} S_{\text{mom}} + w_{\text{exp}} S_{\text{exp}} + w_{\text{wr}} S_{\text{wr}} + w_{\text{reg}} S_{\text{reg}} + w_{\text{ctx}} S_{\text{ctx}}$$

### Regime Weight Vectors ($w$):

$$\begin{aligned}
\mathbf{w}_{\text{bull}} &= \{ \text{mom}: 0.30, \text{exp}: 0.30, \text{wr}: 0.15, \text{reg}: 0.10, \text{ctx}: 0.15 \} \\
\mathbf{w}_{\text{sideways}} &= \{ \text{mom}: 0.20, \text{exp}: 0.30, \text{wr}: 0.20, \text{reg}: 0.15, \text{ctx}: 0.15 \} \\
\mathbf{w}_{\text{bear}} &= \{ \text{mom}: 0.15, \text{exp}: 0.35, \text{wr}: 0.10, \text{reg}: 0.10, \text{ctx}: 0.30 \}
\end{aligned}$$

### Sub-Score Formulations:
1. **Momentum Score ($S_{\text{mom}}$)**:
   $$S_{\text{mom}} = \text{PercentileRank}(\text{ADX}_{14} \times \text{TrendSlope})$$
2. **Historical Expectancy Score ($S_{\text{exp}}$)**:
   $$S_{\text{exp}} = \min\left(100.0, \max\left(0.0, \frac{\text{Expectancy}_{\%} + 2.0}{10.0} \times 100.0\right)\right)$$
3. **Historical Win-Rate Score ($S_{\text{wr}}$)**:
   $$S_{\text{wr}} = \min\left(100.0, \max\left(0.0, \frac{\text{WinRate}_{\%} - 20.0}{40.0} \times 100.0\right)\right)$$
4. **Regime Alignment Score ($S_{\text{reg}}$)**:
   $$S_{\text{reg}} = \begin{cases} 100.0, & \text{if strategy matches regime} \\ 50.0, & \text{if neutral} \\ 0.0, & \text{if mismatched} \end{cases}$$
5. **Context Score ($S_{\text{ctx}}$)**: Detailed below.

---

## 7. Context & Multimodal Scoring Pipeline

The context score ($S_{\text{ctx}} \in [0, 100]$) incorporates fundamental safety, analyst price targets, and NLP sentiment:

$$S_{\text{ctx}} = \text{Analyst Score} + \text{Earnings Score} + \text{Fundamental Score} + \text{News Score}$$

```
                          ┌────────────────────────┐
                          │   CONTEXT SCORE (100)  │
                          └───────────┬────────────┘
         ┌──────────────────┬─────────┴─────────┬──────────────────┐
         ▼                  ▼                   ▼                  ▼
┌─────────────────┐┌─────────────────┐┌─────────────────┐┌─────────────────┐
│ Analyst Cons.   ││ Earnings Surp.  ││ Fundamental     ││ FinBERT News    │
│ Target Upside   ││ Beat Magnitude  ││ D/E & Current   ││ Sentiment NLP   │
│ Max: 40 pts     ││ Max: 30 pts     ││ Max: 20 pts     ││ Max: 10 pts     │
└─────────────────┘└─────────────────┘└─────────────────┘└─────────────────┘
```

1. **Analyst Consensus Score ($0 \text{ to } 40 \text{ pts}$)**:
   $$\text{Upside}_{\%} = \frac{\text{Target}_{\text{consensus}} - \text{Price}}{\text{Price}} \times 100$$
   - $\text{Upside} \ge 20\% \implies +40 \text{ pts}$
   - $10\% \le \text{Upside} < 20\% \implies +30 \text{ pts}$
   - $0\% \le \text{Upside} < 10\% \implies +15 \text{ pts}$
   - $\text{Upside} < 0\% \implies 0 \text{ pts}$

2. **Earnings Surprise Score ($0 \text{ to } 30 \text{ pts}$)**:
   - Latest quarterly EPS surprise $> +5\% \implies +30 \text{ pts}$
   - In-line surprise ($0\%$ to $+5\%$) $\implies +15 \text{ pts}$
   - Earnings miss ($< 0\%$) $\implies -10 \text{ pts}$

3. **Fundamental Health Score ($0 \text{ to } 20 \text{ pts}$)**:
   - Debt-to-Equity Ratio $< 1.5 \implies +10 \text{ pts}$
   - Current Ratio $> 1.0 \implies +10 \text{ pts}$

4. **FinBERT News Sentiment Score ($-10 \text{ to } +10 \text{ pts}$)**:
   - Native Yahoo Finance news headlines scored via `ProsusAI/finbert` NLP model.
   - Compound sentiment $> +0.10 \implies +10 \text{ pts}$
   - Compound sentiment $< -0.10 \implies -10 \text{ pts}$

---

## 8. Risk Management, Sizing & Capital Allocation Math

### 8.1 Stop Loss Bounds (Noise Floor & Risk Ceiling)
For an entry price $P_{\text{entry}}$ and initial strategy stop $P_{\text{stop, raw}}$:

1. **Hard 7.0% Max Loss Ceiling**:
   $$P_{\text{stop}} = \max\left(P_{\text{stop, raw}}, \text{round}(P_{\text{entry}} \times 0.93, 2)\right)$$
2. **4.0% Minimum Noise Floor**:
   $$P_{\text{stop, final}} = \min\left(P_{\text{stop}}, \text{round}(P_{\text{entry}} \times 0.96, 2)\right)$$

$$\text{Final Stop Range: } \mathbf{0.93 \times P_{\text{entry}} \le P_{\text{stop}} \le 0.96 \times P_{\text{entry}}}$$

### 8.2 Profit Target Hierarchy & Weighted Risk-to-Reward
Targets are calculated via ATR-scaling or minimum percentage expansions:
$$\begin{aligned}
T_1 &= \text{round}(P_{\text{entry}} \times 1.12, 2) \quad (+12.0\%) \\
T_2 &= \text{round}(P_{\text{entry}} \times 1.22, 2) \quad (+22.0\%) \\
T_3 &= \text{round}(P_{\text{entry}} \times 1.35, 2) \quad (+35.0\%)
\end{aligned}$$

The **Weighted Reward** ($\overline{R}$) across scale-out exit weights ($w_1=0.50, w_2=0.30, w_3=0.20$):
$$\overline{R} = 0.50(T_1 - P_{\text{entry}}) + 0.30(T_2 - P_{\text{entry}}) + 0.20(T_3 - P_{\text{entry}})$$
$$\text{Weighted R:R} = \frac{\overline{R}}{P_{\text{entry}} - P_{\text{stop}}}$$

### 8.3 Position Sizing: Half-Kelly Criterion with 5.0% Cap
The theoretical Kelly fraction ($f^*$) for win probability $p$ and reward-to-risk ratio $b$:
$$f^* = p - \frac{1 - p}{b}$$

$$\text{Half-Kelly Sizing Fraction } (K) = \max\left(0.0, \frac{f^*}{2}\right) \times M_{\text{drawdown}} \times M_{\text{vix}}$$

$$\text{Position Sizing Percentage } (\text{Alloc}_{\%}) = \mathbf{\min(K, 5.0\%)}$$

$$\text{Allocated Dollars } (\$) = \min(\text{Alloc}_{\%} \times \text{Portfolio Value}, \text{Available Cash})$$
$$\text{Max Integer Shares} = \lfloor \frac{\text{Allocated Dollars}}{P_{\text{entry}}} \rfloor$$

---

## 9. Trade Lifecycle & Execution Engine

```
[ PENDING ] ── (Morning Open Check) ──► Gaps Up > 3%? ──► [ CANCELLED_GAP_UP ]
     │
     └────── Flat / Normal Open ────► [ OPEN ] (Entry adjusted to Open)
                                         │
                   ┌─────────────────────┼─────────────────────┐
                   ▼                     ▼                     ▼
          Price Hits Stop?        Price Hits T1?         Price Hits T2?
                   │                     │                     │
                   ▼                     ▼                     ▼
             [ STOPPED ]            Sell 50%              Sell 30%
            (Full Exit)         Ratchet Stop to BE      Maintain Runner
                                         │                     │
                                         └──────────┬──────────┘
                                                    ▼
                                             Price Hits T3?
                                                    │
                                                    ▼
                                               [ HIT_T3 ]
                                              (Sell Final 20%)
```

### Execution Rules ([`market-evaluator.ts`](file:///c:/Users/acer/Documents/stock-recommendation-engine/frontend/src/lib/market-evaluator.ts)):
1. **Morning Open Transition (`pending` $\to$ `open`)**:
   - If $\text{Open Price} > P_{\text{entry}} \times 1.03 \implies \text{Cancel Order}$ (`cancelled_gap_up`).
   - If normal open: Adjust $P_{\text{entry}} = \text{Open Price}$ and shift $P_{\text{stop}} = \text{Open Price} - (P_{\text{entry, orig}} - P_{\text{stop, orig}})$ to preserve exact risk buffer.
2. **T1 Exit & Breakeven Ratchet**:
   - When $\text{High} \ge T_1$: Exit $50\%$ of shares.
   - Adjust Stop Loss: $P_{\text{stop}} = P_{\text{entry}}$ (Guaranteed zero-risk runner).
3. **T2 Exit**:
   - When $\text{High} \ge T_2$: Exit $30\%$ of initial position.
4. **T3 Exit**:
   - When $\text{High} \ge T_3$: Exit remaining $20\%$ of position. Trade marked as `hit_t3` in `signals_history`.

---

## 10. Real-World Calculation Walkthroughs (Live Production Data)

Below are exact mathematical walkthroughs from live production signals generated by the engine on a **\$10,000 Portfolio**:

### Example 1: Palantir Technologies (Ticker: `PLTR`)
* **Strategy**: `Trend Following` | **Regime**: `Bull`
* **Entry Price**: $\$186.32$
* **Calculated ATR(14)**: $\$6.42$

#### 1. Stop Loss Calculation:
$$\text{Raw Stop} = \min(\text{Low}_{10}, 186.32 - 2.5 \times 6.42) = \$170.27$$
$$\text{7% Loss Ceiling} = \text{round}(186.32 \times 0.93, 2) = \$173.28$$
$$\text{Clamped Stop Loss} = \mathbf{\$173.30 \text{ (-6.99\% Risk)}}$$

#### 2. Target Hierarchy & Weighted R:R:
$$\begin{aligned}
T_1 &= \text{round}(186.32 \times 1.12, 2) = \mathbf{\$208.24 \text{ (+12.0\%)}} \\
T_2 &= \text{round}(186.32 \times 1.22, 2) = \mathbf{\$226.83 \text{ (+22.0\%)}} \\
T_3 &= \text{round}(186.32 \times 1.35, 2) = \mathbf{\$251.01 \text{ (+35.0\%)}}
\end{aligned}$$

$$\text{Dollar Risk per Share} = \$186.32 - \$173.30 = \$13.02$$
$$\overline{\text{Reward}} = 0.5(208.24 - 186.32) + 0.3(226.83 - 186.32) + 0.2(251.01 - 186.32) = \$36.05$$
$$\text{Weighted R:R} = \frac{\$36.05}{\$13.02} = \mathbf{2.77 \implies 2.8}$$

#### 3. Position Sizing:
- **Composite Score**: $47.25 \implies \text{Base Win Probability } p = 0.35$
- **Kelly Fraction**:
  $$f^* = 0.35 - \frac{1.0 - 0.35}{2.77} = 0.35 - 0.2346 = 0.1154 \text{ (11.54\%)}$$
- **Half-Kelly**: $K = \frac{11.54\%}{2} = 5.77\%$
- **5% Hard Portfolio Cap**: $\min(5.77\%, 5.0\%) = \mathbf{5.0\%}$
- **Dollar Allocation**: $5.0\% \times \$10,000.00 = \mathbf{\$500.00}$
- **Integer Share Count**: $\lfloor \frac{\$500.00}{\$186.32} \rfloor = \mathbf{2 \text{ Shares}}$

#### 4. Context Breakdown:
$$\text{Analyst: } +15.0 \text{ pts} \quad | \quad \text{Earnings: } +30.0 \text{ pts} \quad | \quad \text{Fundamental: } +20.0 \text{ pts} \quad | \quad \text{News: } 0.0 \text{ pts}$$
$$\text{Total Context Score} = \mathbf{65.0 \text{ / } 100.0}$$

---

### Example 2: DoorDash Inc. (Ticker: `DASH`)
* **Strategy**: `Trend Following` | **Regime**: `Bull`
* **Entry Price**: $\$233.18$
* **Stop Loss**: $\$216.95$ (-6.96% Risk)
* **Targets**: $T_1 = \$259.72$ (+12%), $T_2 = \$282.91$ (+22%), $T_3 = \$313.05$ (+35%)
* **Weighted R:R**: $2.8$
* **Position Sizing**: $5.0\%$ Cap $\implies \mathbf{\$500.00 \text{ (2 Shares)}}$
* **Context Breakdown**: Analyst: $+30.0$, Fundamental: $+10.0 \implies \mathbf{40.0 \text{ pts}}$

---

### Example 3: Charles River Laboratories (Ticker: `CRL`)
* **Strategy**: `Cross-Sectional Momentum` | **Regime**: `Bull`
* **Entry Price**: $\$286.58$
* **Stop Loss**: $\$266.93$ (-6.86% Risk)
* **Targets**: $T_1 = \$326.05$ (+10%), $T_2 = \$349.76$ (+18%), $T_3 = \$370.51$ (+25%)
* **Weighted R:R**: $2.2$
* **Position Sizing**: Half-Kelly calculation yields $2.73\% \implies \mathbf{\$272.73}$ (Fractional position executed via frontend lot management).

---

## 11. Quantitative Expectancy & Probabilistic Edge

### 11.1 Empirical Backtest Distribution (515 Tickers / 9,648 Trades)
- **Sample Size**: 9,648 historical signals
- **Aggregate Win Rate**: $34.48\%$
- **Average Trade Expectancy**: $+1.69\%$

### 11.2 The Edge Proof
Even under conservative assumptions ($P_{\text{win}} = 35\%$, Average Stop $= -6.0\%$, Average Scale-Out Win $= +17.5\%$):

$$\begin{aligned}
\mathbb{E}[\text{Return}] &= (0.35 \times +17.5\%) + (0.65 \times -6.0\%) \\
&= +6.125\% - 3.900\% \\
&= \mathbf{+2.225\% \text{ per completed trade setup}}
\end{aligned}$$

With position sizing capped at $5.0\%$ of total capital, the portfolio-level mathematical expectancy per closed signal is:
$$\mathbb{E}[\text{Portfolio Return}] = 5.0\% \times (+2.225\%) = \mathbf{+0.111\% \text{ per signal}}$$

Running an average of 40 trades per year:
$$\text{Projected Geometric Edge} \approx 40 \times 0.111\% = \mathbf{+4.44\% \text{ Alpha over cash baseline, before market beta}}$$

---

## 12. Database Schema & Data Flow Specification

```sql
-- Active / Pending Trades Table
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    industry TEXT,
    strategy TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    tier_label TEXT NOT NULL,          -- 'Strong Buy' | 'Buy'
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'open'
    price NUMERIC NOT NULL,
    entry_price NUMERIC NOT NULL,
    stop_loss NUMERIC NOT NULL,
    target_1 NUMERIC NOT NULL,
    target_2 NUMERIC NOT NULL,
    target_3 NUMERIC NOT NULL,
    target_1_pct NUMERIC NOT NULL,
    target_2_pct NUMERIC NOT NULL,
    target_3_pct NUMERIC NOT NULL,
    weighted_rr NUMERIC NOT NULL,
    allocated_dollars NUMERIC NOT NULL,
    max_shares INTEGER NOT NULL,
    position_sizing TEXT NOT NULL,
    composite_score NUMERIC NOT NULL,
    context_score NUMERIC DEFAULT 0,
    context_analyst NUMERIC DEFAULT 0,
    context_earnings NUMERIC DEFAULT 0,
    context_fundamental NUMERIC DEFAULT 0,
    context_news NUMERIC DEFAULT 0,
    regime TEXT NOT NULL,
    narrative TEXT
);

-- Historical & Closed Trades Table
CREATE TABLE signals_history (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    scan_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    outcome TEXT NOT NULL,             -- 'open' | 'stopped' | 'hit_t1' | 'hit_t2' | 'hit_t3' | 'cancelled_gap_up'
    outcome_date DATE,
    entry_price NUMERIC,
    exit_price NUMERIC,
    allocated_dollars NUMERIC,
    composite_score NUMERIC,
    tier_label TEXT
);
```

---

## Summary for LLM Ingestion
When integrating with this repository, LLM agents should adhere to the following invariants:
1. **Never bypass the 5% single-stock allocation cap** in position sizing modules.
2. **Preserve the 4% stop floor and 7% stop ceiling** across all strategy modules.
3. **Maintain integer storage for `max_shares`** in Supabase and handle fractional precision in the frontend runtime.
4. **Always validate parquet files** before writing to `data/cache/by_date/` to prevent `NaN` indicator poison.
