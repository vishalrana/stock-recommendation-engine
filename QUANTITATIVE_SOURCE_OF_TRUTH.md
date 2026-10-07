# QUANTITATIVE SOURCE OF TRUTH
## Stock Recommendation Engine — Canonical Quantitative Architecture

**Repository:** `vishalrana/stock-recommendation-engine`  
**Status:** Canonical Single Source of Truth  
**Architecture:** Analytical Stock Recommendation Engine (Zero Automated Trading / Zero Portfolio Management)

---

## 1. Executive Summary & Core Quantitative Principles

This document defines the single authoritative source of truth for all quantitative calculations, indicators, metrics, risk definitions, lifecycle models, and scoring mechanics across the stock recommendation engine.

### Core Product Boundary
The engine produces **analytical stock ideas** with indicative reference levels, historical statistics, and risk-to-reward ratios. It is **NOT** a brokerage execution system, portfolio optimizer, or automated trading bot. Under no circumstances are position sizing gates, capital constraints, Kelly sizing, or portfolio management concepts to be re-introduced.

---

## 2. Canonical Source-of-Truth Mapping

| Quantity | Canonical Module | Units | Mathematical Formula / Definition | Primary Consumers |
|---|---|---|---|---|
| **RSI (14-period)** | [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`calculate_rsi`) | Index $[0.0, 100.0]$ | Wilder's Exponential Smoothing (RMA) with $\alpha = \frac{1}{N}$. Seeded by simple mean of first $N$ changes. $100 - \frac{100}{1 + RS}$. Edge cases: $100.0$ for all gains, $0.0$ for all losses, $50.0$ for flat. | Strategies, Momentum Scorer, Technical Filters |
| **ATR (14-period)** | [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`calculate_atr`) | Price currency (\$) | $TR_t = \max(H_t - L_t, \lvert H_t - C_{t-1} \rvert, \lvert L_t - C_{t-1} \rvert)$. Initial ATR at $N-1$ is mean of first $N$ TRs. Subsequent: $\text{ATR}_t = \frac{\text{ATR}_{t-1} \times (N-1) + TR_t}{N}$. First $N-1$ are NaN. | Target Calculator, Stop Loss, MACD Normalizer |
| **ADX (14-period)** | [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`compute_adx`) | Index $[0.0, 100.0]$ | Wilder Directional Movement Index: $+DM, -DM, TR$ smoothed over $N$. $+DI = 100 \times \frac{+DM}{TR}$, $-DI = 100 \times \frac{-DM}{TR}$. $DX = 100 \times \frac{\lvert +DI - -DI \rvert}{+DI + -DI}$. Seed ADX at $2N-1$ is mean of first $N$ DX values ($DX[N : 2N]$). RMA recursion thereafter. First $2N-2$ are NaN. | Trend Following, Breakout, PEAD, Cross-Sectional |
| **EMA 20** | [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`compute_ema`) | Price currency (\$) | Exponential Moving Average with $\text{span} = 20, \text{adjust} = \text{False}$, $\alpha = \frac{2}{21}$. Strictly EMA20; never substituted with SMA50. | Strategy Filters, Momentum Scorer |
| **DMA 50 (SMA 50)** | [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`calculate_dma`) | Price currency (\$) | Simple Moving Average: $\frac{1}{50} \sum_{i=0}^{49} Close_{t-i}$ with `min_periods=50` (zero partial-window contamination). | Trend Filter, Proximity Scorer, Breakout Gate |
| **DMA 200 (SMA 200)**| [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`calculate_dma`) | Price currency (\$) | Simple Moving Average: $\frac{1}{200} \sum_{i=0}^{199} Close_{t-i}$ with `min_periods=200` (zero partial-window contamination). | Regime Filter, Long-Term Trend Qualification |
| **Volume MA (20-day)**| [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`calculate_volume_ma`) | Share volume | Simple Moving Average: $\frac{1}{20} \sum_{i=0}^{19} Volume_{t-i}$ with `min_periods=20`. | Volume Ratio, Liquidity Filter, Momentum Scorer |
| **MACD Line & Hist**| [`src/indicators.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/indicators.py) (`compute_macd`) | Price currency (\$) | $\text{MACD Line} = \text{EMA}_{12}(Close) - \text{EMA}_{26}(Close)$. $\text{Signal} = \text{EMA}_9(\text{MACD Line})$. $\text{Hist} = \text{MACD Line} - \text{Signal}$. | Momentum Scorer, Strategy Technical Gates |
| **Market Regime** | [`src/regime.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/regime.py) & [`src/quant_config.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/quant_config.py) | Discrete ('bull', 'sideways', 'bear') | Deterministic evaluation of SPY vs 200-DMA, VIX volatility thresholds, and market breadth. Mapped to score matrix in $[10.0, 100.0]$. | Strategy Filter, Composite Ranker |
| **Momentum Score** | [`src/ranker.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/ranker.py) (`compute_momentum_score`) | Bounded $[0.0, 100.0]$ | Continuous combination of RSI distance from 50, DMA-50 proximity, volume ratio, and ATR-normalized MACD histogram, weighted with sigmoid centering at 55.0. | Composite Ranker |
| **Context Score** | [`src/ranker.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/ranker.py) (`compute_context_score`) | Bounded $[0.0, 100.0]$ | Unscaled raw non-earnings context (Analyst 40, Fundamental 20, News 20) scaled $\times 1.25$ to $[0, 100]$, with veto gates for balance sheet distress, negative sentiment, and price target downside. | Composite Ranker |
| **Expectancy Score** | [`src/ranker.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/ranker.py) (`compute_expectancy_score`) | Bounded $[0.0, 100.0]$ | $S_{exp} = 30.0 + 20.0 \times E_{adjusted}$, where $E_{adjusted}$ is expressed in percentage points (e.g. $+1.44\% \rightarrow 1.44$). Clamped strictly to $[0.0, 100.0]$. | Composite Ranker |
| **Shrunk Win Rate** | [`src/utils/metrics_pipeline.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/utils/metrics_pipeline.py) (`calculate_shrunk_win_rate`) | Percentage $[0.0, 100.0]$ | Empirical Bayes Beta-Binomial shrinkage: $\frac{\text{wins} \times 100.0 + \alpha \times \text{prior}}{\text{wins} + \text{losses} + \alpha}$. Sample size 0 preserves prior without synthetic trade fabrication. | Composite Ranker |
| **Composite Score** | [`src/ranker.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/ranker.py) (`compute_composite_score`) | Bounded $[0.0, 100.0]$ | $\sum (w_i \times \text{score}_i)$ across Momentum, Expectancy, Win Rate, Regime, and Context. Weights sum strictly to $1.0$. Output clamped to $[0.0, 100.0]$. | Recommendation Ranker, Tier Classifier |
| **Stop Loss** | [`src/strategies/target_calculator.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/strategies/target_calculator.py) & [`src/quant_config.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/quant_config.py) | Price currency (\$) | Canonical strategy ATR multiplier with noise floor ($\ge 4\% - 6\%$) and hard risk ceiling ($7\%$). Strict invariant: $0 < stop < entry$. Invalid stop is hard-rejected. | Recommendation Output, Lifecycle Monitor |
| **Target Hierarchy (T1, T2, T3)** | [`src/strategies/target_calculator.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/strategies/target_calculator.py) (`calculate_targets`) | Price currency (\$) | $T_k = \max(Entry + M_k \times \text{ATR}_{14}, Entry \times (1 + F_k))$. Enforces strict monotonic ordering: $Entry < T_1 < T_2 < T_3$. Non-monotonic levels are rejected. | Recommendation Output, Reach Evaluator |
| **Reach Probability**| [`src/strategies/target_calculator.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/strategies/target_calculator.py) (`get_reach_prob`) | Probability $[0.0, 1.0]$ | Joint OHLC target-before-stop reach probability over 504 lookback days ($P = \frac{\text{successes}}{\text{valid windows}}$). Conservative same-bar STOP_FIRST policy. | Scale-Out Truning, Honest R:R |
| **Scale-Out Weights**| [`src/strategies/target_calculator.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/strategies/target_calculator.py) & [`src/quant_config.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/quant_config.py) | Categorical string | '50/30/20' (all 3 survive reach threshold), '60/40/0' (T3 pruned), '70/30/0' (T1 only survives, 30% runner to breakeven). | Weighted R:R, Analytical Sizing |
| **Weighted Honest R:R**| [`src/strategies/target_calculator.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/src/strategies/target_calculator.py) | Ratio $\ge 0.0$ | $\frac{\sum (w_k \times (T_k - Entry))}{Entry - Stop}$. Uses scale-out weights and unmasked positive risk. | Recommendation Output, Tier Assignment |
| **Recommendation Lifecycle** | [`jobs/generate_signals.py`](file:///c:/Users/acer/Documents/stock-recommendation-engine/jobs/generate_signals.py) (`reconcile_recommendation_lifecycle`)| Lifecycle Status | Day $D$ generation activates on $D+1$. Evaluates 'stopped' (Low $\le$ Stop), 'hit_t3' (High $\ge$ T3), 'invalidated' (no longer qualified), or 'open'. Keyed by exact `signal_id`. | Database State, Portfolio Audit |

---

## 3. Data Validation & Critical Hard Fail Invariants

Before any calculation or strategy evaluation, data MUST satisfy these non-negotiable invariants:

1. **OHLCV Physical Invariants:**
   - $Price > 0$ for all bars ($Open > 0, High > 0, Low > 0, Close > 0$).
   - $Volume \ge 0$ for all bars.
   - $High \ge Low$ on every bar.
   - $High \ge Open$ and $High \ge Close$ on every bar.
   - $Low \le Open$ and $Low \le Close$ on every bar.
2. **Temporal Integrity:**
   - Index must be strictly monotonic increasing without duplicates.
   - Zero look-ahead: As-of date $D$ includes only bars $\le D$.
3. **Indicator Availability (No Silent Substitutes):**
   - Missing ATR: Candidate REJECTED. Never substituted with $Price \times 2\%$.
   - Missing EMA20: Candidate REJECTED. Never substituted with SMA50.
   - Missing DMA50 or DMA200: Candidate REJECTED.
   - Insufficient History ($< 60$ bars for daily indicators, $< 200$ bars for DMA200): Candidate REJECTED.

---

## 4. Win-Rate Provenance Definitions

Every win-rate metric reports its provenance according to the following taxonomy:

- `ticker_observed`: Genuine closed trade observations for the specific ticker ($wins > 0$ or $losses > 0$, $sample\_size = wins + losses$). Shrunk toward prior.
- `strategy_prior`: Zero ticker observations ($sample\_size = 0$). Sourced from strategy backtest prior.
- `candidate_provided`: Sourced from incoming setup payload with zero completed trades.
- `ticker_prior`: Generic unseeded ticker-level prior.
- `generic_prior`: System neutral default ($50.0\%$).

Synthetic pseudo-count trade inflation (e.g. `round(win_rate / 20)`) is strictly prohibited. Zero trade observations are recorded honestly as `sample_size = 0`.

---

## 5. Survivorship Bias Mitigation & Limitations

1. **Methodology:**
   - Historical universe statistics reflect surviving common equities from standard data feeds.
   - Strategy historical expectancies incorporate a canonical $15\%$ haircut ($0.85\times$) to account for historical constituent attrition.
   - Reach probabilities incorporate an $8\%$ fallback haircut ($0.92\times$) when delisted sector proxies are unavailable.
2. **Known Limitations:**
   - Tick-level simulation of historical survivorship cannot be fully eliminated without commercial survivorship-free point-in-time constituent databases (e.g. CRSP / Compustat).
   - The engine transparently documents this limitation and discounts backtested expectancies accordingly.
