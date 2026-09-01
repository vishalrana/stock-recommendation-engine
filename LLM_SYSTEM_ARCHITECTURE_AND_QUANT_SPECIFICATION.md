# Stock Recommendation Engine — Master Architecture & Quantitative Specification

> **Document Type**: Comprehensive Quantitative & Technical Architecture Specification  
> **Target Audience**: Quantitative Developers, Trading Systems Architects, and Large Language Models (LLMs)  
> **System Classification**: Multi-Strategy Systematic Equity Momentum & Mean-Reversion CTA Engine  
> **Primary Asset Universe**: S&P 500 (~502 constituents) + Selected Sector ETFs (~15 tickers)  
> **Version**: 2.1 (Post-Target Refactor & Live Recalculation Engine)  
> **Last Updated**: August 31, 2026  

---

## Table of Contents
1. [Executive Summary & Operational Paradigm](#1-executive-summary--operational-paradigm)
2. [End-to-End System Architecture](#2-end-to-end-system-architecture)
3. [Data Ingestion & Storage Architecture](#3-data-ingestion--storage-architecture)
4. [Macro Market Regime Detection](#4-macro-market-regime-detection)
5. [Strategy Mathematical Specifications](#5-strategy-mathematical-specifications)
6. [Strategy-Specific ATR Targets & Reach Probability Filtering](#6-strategy-specific-atr-targets--reach-probability-filtering)
7. [Multi-Factor Composite Scoring Engine](#7-multi-factor-composite-scoring-engine)
8. [Context & Multimodal NLP Scoring Pipeline](#8-context--multimodal-nlp-scoring-pipeline)
9. [Risk Management, Sizing & Capital Allocation Math](#9-risk-management-sizing--capital-allocation-math)
10. [Trade Lifecycle & Live Recalculation Engine](#10-trade-lifecycle--live-recalculation-engine)
11. [Dynamic Scale-Out Dollar Exit Breakdown](#11-dynamic-scale-out-dollar-exit-breakdown)
12. [Real-World Production Calculation Walkthroughs](#12-real-world-production-calculation-walkthroughs)
13. [Quantitative Expectancy & Probabilistic Edge Proof](#13-quantitative-expectancy--probabilistic-edge-proof)
14. [Database Schema & Data Flow Specification](#14-database-schema--data-flow-specification)
15. [Master Rules & Invariants for LLM Agents](#15-master-rules--invariants-for-llm-agents)

---

## 1. Executive Summary & Operational Paradigm

The **Stock Recommendation Engine** is an institutional-grade, nightly-cadence algorithmic trading system designed to identify high-probability swing and trend-following opportunities across US equities.

### Core Mathematical Philosophy:
1. **Asymmetric Payoff Structure (CTA Expectancy)**:
   $$\text{Expectancy} = (P_{\text{win}} \times \overline{\text{Win}}) - (P_{\text{loss}} \times \overline{\text{Loss}})$$
   The engine targets a **33%–40% win rate** with an average risk-to-reward ratio of **1:2.5 to 1:3.5**, producing strong positive mathematical expectancy without curve-fitting to fragile high-win-rate regimes.
2. **Strict Capital Preservation**:
   - Single-stock allocation is hard-capped at **5.0% of total portfolio value** at entry.
   - Stop losses are strictly clamped between a **4.0% noise floor** and a **7.0% hard loss ceiling**.
   - Position sizing utilizes **Half-Kelly Criterion** constrained by portfolio drawdown and VIX volatility.
3. **Volatility-Aware Empirical Target Scaling**:
   - Profit targets are scaled by strategy-specific Average True Range ($\text{ATR}_{14}$) multiples bounded by fixed minimum percentage floors.
   - Each target level ($T_1, T_2, T_3$) must pass a 504-day sliding-window **Empirical Reach Probability ($P(\text{reach})$)** threshold.
   - Unreachable "ghost targets" are pruned, preventing inflated R:R ratios from distorting Half-Kelly position sizing.
4. **Dynamic Multi-Horizon Scale-Outs**:
   - **Scale "50/30/20"**: Full 3-target tier (50% exit at $T_1$, 30% at $T_2$, 20% at $T_3$).
   - **Scale "60/30/10"**: When $T_3$ is pruned (60% exit at $T_1$, 30% at $T_2$, 10% breakeven trailing runner).
   - **Scale "70/30/0"**: When $T_2$ and $T_3$ are pruned (70% exit at $T_1$, 30% breakeven trailing runner).

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
          │ data/cache/by_date/*.parquet│           │ frontend/api/signals/recalc │
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
            Context Enrichment Layer                               │
            (Analyst + Earn + Fund + FinBERT)                      │
                         │                                         │
                         ▼                                         │
          Strategy ATR Targets & Reach Prob                        │
          (Prunes Ghost Targets & Computes Honest R:R)             │
                         │                                         │
                         ▼                                         │
            Half-Kelly Position Sizing                             │
            (5% Portfolio Cap + Cash Normalization)                │
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
          - Trade Lifecycle Evaluator (Morning Open & Scale-Out Exits)
          - Live Recalculate & Reconcile Engine
          - Dollar-Exit Breakdown & Exit Plan Visualizer
```

---

## 3. Data Ingestion & Storage Architecture

### 3.1 Date-Partitioned Parquet Cache
Market data is stored partitioned in `data/cache/by_date/{YYYY-MM-DD}.parquet`. Each file contains a MultiIndex DataFrame indexed by `(Ticker, Date)` containing:
$$\text{Columns} = [\text{OPEN}, \text{HIGH}, \text{LOW}, \text{CLOSE}, \text{VOLUME}]$$

### 3.2 Data Hygiene & Corruption Protection
To prevent incomplete market snapshots from poisoning technical indicators:
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
- **VIX < 20 (Normal)**: $M_{\text{vix}} = 1.0\times$ sizing multiplier.
- **20 ≤ VIX < 30 (Elevated)**: $M_{\text{vix}} = 0.8\times$ sizing multiplier.
- **VIX ≥ 30 (High Risk)**: $M_{\text{vix}} = 0.5\times$ sizing multiplier, Mean Reversion disabled.

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

## 6. Strategy-Specific ATR Targets & Reach Probability Filtering

### 6.1 Layer 1: Strategy ATR Multiples & Fixed Floors

Profit targets are calculated as the maximum between the ATR expansion and the strategy's fixed minimum percentage floor:

$$T_{k, \text{atr}} = P_{\text{entry}} + (M_k \times \text{ATR}_{14})$$
$$T_{k, \text{floor}} = P_{\text{entry}} \times (1 + F_k)$$
$$T_k = \max(T_{k, \text{atr}}, T_{k, \text{floor}})$$

#### Multiplier ($M$) & Floor ($F$) Parameter Matrix:
| Strategy | $M_1$ (ATR) | $F_1$ (Floor) | $M_2$ (ATR) | $F_2$ (Floor) | $M_3$ (ATR) | $F_3$ (Floor) | Max Hold ($H$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Pullback Recovery** | $2.5\times$ | $+6.0\%$ | $4.0\times$ | $+12.0\%$ | $6.0\times$ | $+18.0\%$ | 15 days |
| **Trend Following** | $3.0\times$ | $+8.0\%$ | $5.0\times$ | $+15.0\%$ | $8.0\times$ | $+25.0\%$ | 20 days |
| **52-Week High Breakout** | $3.0\times$ | $+8.0\%$ | $5.5\times$ | $+16.0\%$ | $9.0\times$ | $+28.0\%$ | 20 days |
| **Cross-Sectional Momentum** | $2.5\times$ | $+6.0\%$ | $4.5\times$ | $+12.0\%$ | $7.0\times$ | $+20.0\%$ | 15 days |
| **Sector Rotation** | $2.0\times$ | $+5.0\%$ | $3.5\times$ | $+10.0\%$ | $5.0\times$ | $+15.0\%$ | 15 days |
| **Post-Earnings Drift (PEAD)** | $2.5\times$ | $+6.0\%$ | $4.5\times$ | $+14.0\%$ | $7.0\times$ | $+22.0\%$ | 15 days |
| **Mean Reversion** | $2.0\times$ | $+5.0\%$ | $3.5\times$ | $+10.0\%$ | $5.0\times$ | $+15.0\%$ | 10 days |

---

### 6.2 Layer 2: Empirical Reach Probability Filtering

For a candidate stock with 504 trading days of history ($W = 504$), the empirical reach probability for target $T_k$ over maximum holding horizon $H$ is computed by simulating rolling forward windows:

$$P(\text{reach}_k) = \frac{1}{W - H} \sum_{t=1}^{W - H} \mathbb{I}\left(\max_{1 \le j \le H} \text{High}_{t+j} \ge \text{Close}_t \times (1 + \text{target\_pct}_k)\right)$$

#### Acceptance & Pruning Rules:
1. **Target 1 Gate**:
   - If $P(\text{reach}_1) < 0.50 \implies \mathbf{REJECT\ SIGNAL}$. The setup lacks sufficient statistical probability to reach minimum target.
2. **Target 2 Filter**:
   - If $P(\text{reach}_2) < 0.30 \implies \mathbf{PRUNE\ T_2}$ ($T_2 = \text{null}$).
3. **Target 3 Filter**:
   - If $P(\text{reach}_3) < 0.15 \implies \mathbf{PRUNE\ T_3}$ ($T_3 = \text{null}$).

---

### 6.3 Dynamic Scale-Out Allocations & Honest Weighted R:R

Based on which targets survive probability filtering, scale-out weights ($w_1, w_2, w_3, w_{\text{runner}}$) are assigned dynamically:

| Surviving Targets | Scale-Out Format | $w_1$ (T1) | $w_2$ (T2) | $w_3$ (T3) | $w_{\text{runner}}$ (Trailing Runner) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **All 3 Survive** | `"50/30/20"` | $50\%$ | $30\%$ | $20\%$ | $0\%$ |
| **$T_1$ & $T_2$ Survive ($T_3$ pruned)** | `"60/30/10"` | $60\%$ | $30\%$ | $0\%$ | $10\%$ |
| **Only $T_1$ Survives ($T_2, T_3$ pruned)**| `"70/30/0"` | $70\%$ | $0\%$ | $0\%$ | $30\%$ |

#### Honest Weighted Risk-to-Reward Ratio ($R_{\text{honest}}$):
$$\overline{\text{Reward}}_{\text{honest}} = \sum_{k \in \text{surviving}} w_k \times (T_k - P_{\text{entry}})$$
$$R_{\text{honest}} = \frac{\overline{\text{Reward}}_{\text{honest}}}{P_{\text{entry}} - P_{\text{stop}}}$$

*This prevents "ghost targets" from artificially inflating the R:R ratio, which would otherwise trick Half-Kelly into taking oversized positions on unrealistic price expectations.*

---

## 7. Multi-Factor Composite Scoring Engine

Candidate signals are ranked on a scale from $0.0$ to $100.0$ using regime-weighted linear combinations:

$$\text{Composite Score} = w_{\text{mom}} S_{\text{mom}} + w_{\text{exp}} S_{\text{exp}} + w_{\text{wr}} S_{\text{wr}} + w_{\text{reg}} S_{\text{reg}} + w_{\text{ctx}} S_{\text{ctx}}$$

### Regime Weight Vectors ($\mathbf{w}$):
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

## 8. Context & Multimodal NLP Scoring Pipeline

The context score ($S_{\text{ctx}} \in [0, 100]$) integrates fundamental solvency, analyst consensus targets, earnings surprises, and FinBERT sentiment:

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
   $$\text{Upside}_{\%} = \frac{\text{Target}_{\text{consensus}} - P_{\text{entry}}}{P_{\text{entry}}} \times 100$$
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
   - Evaluated using `ProsusAI/finbert` NLP transformer model on latest 10 news headlines.
   - Compound polarity score $> +0.10 \implies +10 \text{ pts}$
   - Compound polarity score $< -0.10 \implies -10 \text{ pts}$

---

## 9. Risk Management, Sizing & Capital Allocation Math

### 9.1 Stop Loss Clamping Bounds
For entry price $P_{\text{entry}}$ and initial strategy stop $P_{\text{stop, raw}}$:
1. **7.0% Max Loss Ceiling**:
   $$P_{\text{stop, max}} = \max\left(P_{\text{stop, raw}}, \text{round}(P_{\text{entry}} \times 0.93, 2)\right)$$
2. **4.0% Minimum Noise Floor**:
   $$P_{\text{stop, final}} = \min\left(P_{\text{stop, max}}, \text{round}(P_{\text{entry}} \times 0.96, 2)\right)$$

$$\text{Final Stop Range: } \mathbf{0.93 \times P_{\text{entry}} \le P_{\text{stop}} \le 0.96 \times P_{\text{entry}}}$$

---

### 9.2 Position Sizing: Half-Kelly Criterion with 5.0% Portfolio Cap

For candidate score $S$, win probability estimate $p$ is mapped as:
$$p = \begin{cases}
0.75, & \text{if } S \ge 90.0 \\
0.68, & \text{if } 80.0 \le S < 90.0 \\
0.60, & \text{if } 70.0 \le S < 80.0 \\
0.52, & \text{if } 60.0 \le S < 70.0 \\
0.45, & \text{if } 50.0 \le S < 60.0 \\
0.35, & \text{if } S < 50.0
\end{cases}$$

Using the **Honest Weighted R:R** ($R_{\text{honest}}$):
$$f^* = p - \frac{1 - p}{R_{\text{honest}}}$$
$$K = \max\left(0.0, \frac{f^*}{2}\right) \times M_{\text{drawdown}} \times M_{\text{vix}}$$

$$\text{Raw Dollar Demand} = \min(K \times \text{Portfolio Value}, 0.05 \times \text{Portfolio Value})$$

#### Cash-Constrained Cross-Sectional Normalization:
If total capital demand for top $N$ signals exceeds available cash $C_{\text{avail}}$:
$$\text{Multiplier } M_{\text{cash}} = \begin{cases}
1.0, & \text{if } \sum \text{Demand} \le C_{\text{avail}} \\
\frac{C_{\text{avail}}}{\sum \text{Demand}}, & \text{if } \sum \text{Demand} > C_{\text{avail}}
\end{cases}$$

$$\text{Allocated Dollars} = \text{round}(\text{Raw Dollar Demand} \times M_{\text{cash}}, 2)$$
$$\text{Exact Shares} = \frac{\text{Allocated Dollars}}{P_{\text{entry}}}$$
$$\text{Max Integer Shares} = \lfloor \text{Exact Shares} \rfloor$$

---

## 10. Trade Lifecycle & Live Recalculation Engine

```
[ PENDING ] ── (Morning Open Check) ──► Open > Entry * 1.03? ──► [ CANCELLED_GAP_UP ]
     │
     └────── Flat / Normal Open ────► [ OPEN ] (Entry adjusted to Open)
                                         │
                   ┌─────────────────────┼─────────────────────┐
                   ▼                     ▼                     ▼
          Price Hits Stop?        Price Hits T1?         Price Hits T2?
                   │                     │                     │
                   ▼                     ▼                     ▼
             [ STOPPED ]            Sell w1 %             Sell w2 %
            (Full Exit)         Ratchet Stop to BE     Maintain Runner
                                         │                     │
                                         └──────────┬──────────┘
                                                    ▼
                                             Price Hits T3?
                                                    │
                                                    ▼
                                               [ HIT_T3 ]
                                              (Sell w3 %)
```

### Recalculation Engine Logic ([`app/api/signals/recalculate/route.ts`](file:///c:/Users/acer/Documents/stock-recommendation-engine/frontend/src/app/api/signals/recalculate/route.ts)):
1. **Morning Open Transition (`pending` $\to$ `open`)**:
   - If $\text{Open Price} > P_{\text{entry}} \times 1.03 \implies \text{Cancel Setup}$ (`cancelled_gap_up`).
   - If normal open: Adjust $P_{\text{entry}} = \text{Open Price}$ and shift $P_{\text{stop}} = \text{Open Price} - (P_{\text{entry, orig}} - P_{\text{stop, orig}})$ to preserve the exact risk dollar buffer.
2. **Target 1 Hit & Breakeven Ratchet**:
   - When $\text{High} \ge T_1$: Mark as `hit_t1`.
   - Ratchet Stop Loss: $P_{\text{stop}} = P_{\text{entry}}$ (Guarantees zero downside risk on remaining shares).
3. **Target 2 Hit**:
   - When $\text{High} \ge T_2$: Mark as `hit_t2`.
4. **Target 3 Hit**:
   - When $\text{High} \ge T_3$: Mark as `hit_t3` (Full trade scale-out cycle completed).
5. **Stop Loss Hit**:
   - When $\text{Low} \le P_{\text{stop}}$: Mark as `stopped` and archive to `signals_history`.

---

## 11. Dynamic Scale-Out Dollar Exit Breakdown

For any signal with allocated capital $A = \text{allocated\_dollars}$ and scale-out format `"w1/w2/w3"`:

$$\text{Dollar Exit}(T_1) = A \times \frac{w_1}{100}$$
$$\text{Dollar Exit}(T_2) = \begin{cases} A \times \frac{w_2}{100}, & \text{if } T_2 \ne \text{null} \\ \$0.00, & \text{if } T_2 = \text{null} \end{cases}$$
$$\text{Dollar Exit}(T_3) = \begin{cases} A \times \frac{w_3}{100}, & \text{if } T_3 \ne \text{null} \\ \$0.00, & \text{if } T_3 = \text{null} \end{cases}$$
$$\text{Runner Dollar Allocation} = A \times \frac{w_{\text{runner}}}{100}$$

### Cumulative Cash Recovery & Remaining Capital:
- **After $T_1$ Exit**:
  $$\text{Cash Recovered} = \text{Dollar Exit}(T_1)$$
  $$\text{Capital Remaining in Trade} = A - \text{Dollar Exit}(T_1) \quad (\text{Protected at Breakeven})$$
- **After $T_2$ Exit**:
  $$\text{Cash Recovered} = \text{Dollar Exit}(T_1) + \text{Dollar Exit}(T_2)$$
  $$\text{Capital Remaining in Trade} = A - (\text{Dollar Exit}(T_1) + \text{Dollar Exit}(T_2))$$

---

## 12. Real-World Production Calculation Walkthroughs

Below are exact mathematical walkthroughs from live production signals generated on August 31, 2026 for a **\$10,000.00 Portfolio**:

---

### Example 1: Palantir Technologies (Ticker: `PLTR`)
* **Strategy**: `Trend Following` | **Regime**: `Bull`
* **Entry Price ($P_{\text{entry}}$)**: $\$185.93$
* **14-day ATR**: $\$6.42$

#### 1. Stop Loss Calculation:
$$\text{Strategy Stop} = \min(\text{Low}_{10}, \$185.93 - 2.5 \times \$6.42) = \$170.27$$
$$\text{7.0% Max Loss Ceiling} = \text{round}(\$185.93 \times 0.93, 2) = \$172.91$$
$$\text{Clamped Stop Loss} = \mathbf{\$172.91 \text{ (-7.00\% Risk)}}$$

#### 2. Target Calculation & Reach Probability Filtering:
- **Target 1 ($3.0\times \text{ATR}$, 8% Floor)**:
  $$T_{1, \text{atr}} = 185.93 + (3.0 \times 6.42) = \$205.19 \quad (+10.36\%)$$
  $$T_1 = \max(\$205.19, \$185.93 \times 1.08) = \mathbf{\$208.24 \text{ (+12.00\%)}}$$
  $$P(\text{reach}_1) = 0.62 \ge 0.50 \implies \mathbf{\text{PASS}}$$
- **Target 2 ($5.0\times \text{ATR}$, 15% Floor)**:
  $$P(\text{reach}_2) = 0.24 < 0.30 \implies \mathbf{\text{PRUNED (Ghost Target)} \to T_2 = \text{null}}$$
- **Target 3 ($8.0\times \text{ATR}$, 25% Floor)**:
  $$P(\text{reach}_3) = 0.08 < 0.15 \implies \mathbf{\text{PRUNED (Ghost Target)} \to T_3 = \text{null}}$$

#### 3. Dynamic Scale-Out & Honest Weighted R:R:
- **Scale-Out Weights**: `"70/30/0"` ($w_1 = 70\%$, Runner $= 30\%$)
- **Dollar Risk per Share**: $\$185.93 - \$172.91 = \$13.02$
- **Weighted Reward**: $0.70 \times (\$208.24 - \$185.93) = \$15.62$
- **Honest Weighted R:R**:
  $$R_{\text{honest}} = \frac{\$15.62}{\$13.02} = \mathbf{1.20}$$

#### 4. Half-Kelly Position Sizing:
- **Composite Score**: $47.25 \implies p = 0.35$
- **Kelly Sizing**:
  $$f^* = 0.35 - \frac{1 - 0.35}{1.20} = 0.35 - 0.5417 = -0.1917 < 0 \implies \mathbf{K = 0\%}$$
  *(Signals with low composite scores receive \$0.00 capital allocation, strictly preserving cash until high-conviction scores $\ge 60$ occur).*

---

### Example 2: Charles River Laboratories (Ticker: `CRL`)
* **Strategy**: `Cross-Sectional Momentum` | **Regime**: `Bull`
* **Entry Price ($P_{\text{entry}}$)**: $\$296.41$
* **14-day ATR**: $\$7.85$

#### 1. Stop Loss Calculation:
$$\text{Strategy Stop} = \$296.41 - 2.0 \times \$7.85 = \$280.71$$
$$\text{7.0% Max Loss Ceiling} = \text{round}(\$296.41 \times 0.93, 2) = \$275.66$$
$$\text{Clamped Stop Loss} = \mathbf{\$275.66 \text{ (-7.00\% Risk)}}$$

#### 2. Target Calculation & Reach Probability Filtering:
- **Target 1 ($2.5\times \text{ATR}$, 6% Floor)**:
  $$T_1 = \mathbf{\$326.05 \text{ (+10.00\%)}}, \quad P(\text{reach}_1) = 0.74 \ge 0.50 \implies \mathbf{\text{PASS}}$$
- **Target 2 ($4.5\times \text{ATR}$, 12% Floor)**:
  $$T_2 = \mathbf{\$343.84 \text{ (+16.00\%)}}, \quad P(\text{reach}_2) = 0.48 \ge 0.30 \implies \mathbf{\text{PASS}}$$
- **Target 3 ($7.0\times \text{ATR}$, 20% Floor)**:
  $$T_3 = \mathbf{\$367.55 \text{ (+24.00\%)}}, \quad P(\text{reach}_3) = 0.22 \ge 0.15 \implies \mathbf{\text{PASS}}$$

#### 3. Dynamic Scale-Out & Honest Weighted R:R:
- **Scale-Out Weights**: `"50/30/20"` ($w_1 = 50\%, w_2 = 30\%, w_3 = 20\%$)
- **Dollar Risk per Share**: $\$296.41 - \$275.66 = \$20.75$
- **Weighted Reward**:
  $$\overline{R} = 0.50(326.05 - 296.41) + 0.30(343.84 - 296.41) + 0.20(367.55 - 296.41) = \$43.28$$
- **Honest Weighted R:R**:
  $$R_{\text{honest}} = \frac{\$43.28}{\$20.75} = \mathbf{2.09}$$

#### 4. Dollar Exit Breakdown (Based on \$500.00 Allocation):
- **T1 Exit (50%)**: Sell **\$250.00** at **\$326.05** ($\to$ \$250.00 remaining protected at breakeven stop \$296.41)
- **T2 Exit (30%)**: Sell **\$150.00** at **\$343.84** ($\to$ \$100.00 remaining)
- **T3 Exit (20%)**: Sell **\$100.00** at **\$367.55** (Final exit)

---

## 13. Quantitative Expectancy & Probabilistic Edge Proof

### 13.1 Empirical Backtest Distribution (515 Tickers / 9,648 Trades)
- **Sample Size**: 9,648 historical signals
- **Aggregate Win Rate**: $34.48\%$
- **Average Trade Expectancy**: $+1.69\%$

### 13.2 Mathematical Edge Proof
Under conservative operational assumptions ($P_{\text{win}} = 35\%$, Average Stop $= -6.0\%$, Average Scale-Out Win $= +17.5\%$):

$$\begin{aligned}
\mathbb{E}[\text{Return}] &= (0.35 \times +17.5\%) + (0.65 \times -6.0\%) \\
&= +6.125\% - 3.900\% \\
&= \mathbf{+2.225\% \text{ per completed trade cycle}}
\end{aligned}$$

With position sizing capped at $5.0\%$ of total capital, the portfolio-level mathematical expectancy per closed signal is:
$$\mathbb{E}[\text{Portfolio Return}] = 5.0\% \times (+2.225\%) = \mathbf{+0.111\% \text{ per signal}}$$

Running an average of 40 trades per year:
$$\text{Projected Geometric Edge} \approx 40 \times 0.111\% = \mathbf{+4.44\% \text{ Alpha over cash baseline, before market beta}}$$

---

## 14. Database Schema & Data Flow Specification

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
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'open' | 'hit_t1' | 'hit_t2'
    price NUMERIC NOT NULL,
    entry_price NUMERIC NOT NULL,
    stop_loss NUMERIC NOT NULL,
    target_1 NUMERIC NOT NULL,
    target_2 NUMERIC,                  -- Nullable when pruned by reach probability
    target_3 NUMERIC,                  -- Nullable when pruned by reach probability
    target_1_pct NUMERIC,
    target_2_pct NUMERIC,
    target_3_pct NUMERIC,
    target_1_atr NUMERIC,
    target_2_atr NUMERIC,
    target_3_atr NUMERIC,
    reach_prob_t1 NUMERIC,
    reach_prob_t2 NUMERIC,
    reach_prob_t3 NUMERIC,
    scale_out_weights TEXT NOT NULL DEFAULT '50/30/20',
    weighted_rr NUMERIC NOT NULL,
    weighted_rr_honest NUMERIC,
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
    narrative TEXT,
    entry_date DATE,
    exit_date DATE,
    sell_signal BOOLEAN DEFAULT FALSE,
    sell_signal_reason TEXT,
    sell_price NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Historical & Closed Trades Table
CREATE TABLE signals_history (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    scan_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    outcome TEXT NOT NULL,             -- 'open' | 'stopped' | 'hit_t1' | 'hit_t2' | 'hit_t3' | 'cancelled_gap_up'
    outcome_date DATE,
    outcome_return_pct NUMERIC,
    outcome_holding_days INTEGER,
    entry_price NUMERIC,
    exit_price NUMERIC,
    allocated_dollars NUMERIC,
    composite_score NUMERIC,
    tier_label TEXT,
    scale_out_weights TEXT DEFAULT '50/30/20',
    weighted_rr_honest NUMERIC
);
```

---

## 15. Master Rules & Invariants for LLM Agents

When reading, modifying, or extending this codebase, any LLM agent must strictly observe these core rules:

1. **5.0% Allocation Cap Invariant**:
   - No single stock position may receive more than $5.0\%$ of total portfolio value (`0.05 * portfolio_value`) under any circumstances.
2. **Stop Loss Clamping Bounds**:
   - Initial stop losses must always satisfy: $0.93 \times P_{\text{entry}} \le P_{\text{stop}} \le 0.96 \times P_{\text{entry}}$.
3. **Ghost Target Pruning**:
   - Never set targets without verifying empirical reach probabilities ($P(T_1) \ge 0.50, P(T_2) \ge 0.30, P(T_3) \ge 0.15$). If pruned, set $T_2/T_3 = \text{null}$ and assign dynamic scale-out weights.
4. **Honest Half-Kelly**:
   - Always size using $R_{\text{honest}}$ (weighted only over surviving targets) to prevent low-probability setups from inflating bet size.
5. **Database Type Safety**:
   - `max_shares` in Supabase is strictly typed as an `INTEGER`. Always pass `int()` when writing to database tables. Fractional precision is handled in the frontend runtime.
6. **Data Hygiene**:
   - Always validate that market parquet cache files have $\le 50\%$ null close values before using or saving them to `data/cache/by_date/`.
