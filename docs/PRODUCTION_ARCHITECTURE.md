# Production Architecture: Stock Recommendation Engine

**Repository**: `vishalrana/stock-recommendation-engine`  
**Deployment**: [https://stock-recommendation-engine-rouge.vercel.app/](https://stock-recommendation-engine-rouge.vercel.app/)  
**Role**: Single-Purpose Stock Recommendation Engine  

---

## 1. Absolute Product Boundaries

This system is strictly a **Stock Recommendation Engine**.

The product lifecycle is:
1. **Market Scan**: Ingests daily market data across broad US-listed common equities (5,600+ equities).
2. **Strategy Qualification**: Evaluates 6 primary quantitative swing strategies.
3. **Composite Scoring & Ranking**: Multi-factor scoring (Momentum, Expectancy, Win Rate, Regime, Context).
4. **Quality & Risk Filtering**: Recommendation threshold (Score $\ge 65.0$), Strong Buy ($\ge 80.0$).
5. **Entry Location Validation**: Requires `BUY` state. `WAIT`, `REJECT`, and `NO_SETUP` are never recommendations.
6. **Indicative Levels & Analytical Probabilities**: T1, T2, T3, and Stop Loss are presented for manual evaluation.
7. **Manual Decision**: The end user manually reviews stock ideas and decides whether to invest.

### Strict Exclusions
The engine contains:
* **ZERO** portfolio management or capital allocation
* **ZERO** position sizing, shares calculation, or Kelly/Half-Kelly logic
* **ZERO** cash constraint or cash management logic
* **ZERO** brokerage execution or automated trading
* **ZERO** R:R or reach-probability rejection gates (R:R and probabilities are strictly analytical)

---

## 2. Quantitative Architecture & Formulas

### A. Recommendation Thresholds
* **Strong Buy**: Composite Score $\ge 80.0$
* **Buy**: Composite Score $\ge 65.0$
* **Not Recommended**: Composite Score $< 65.0$

### B. Primary Strategies (6 Canonical Setups)
1. **Trend Following**: 20/50/200 DMA momentum alignment with ADX confirmation.
2. **52-Week High Breakout**: Consolidation near 52-week highs with volume expansion.
3. **Pullback Recovery**: Pullback to rising 20/50 DMA with RSI oversold recovery.
4. **Post-Earnings Announcement Drift (PEAD)**: Earnings surprise beat $\ge 5\%$ with post-earnings continuation.
5. **Cross-Sectional Momentum**: Top percentile relative momentum across broad market.
6. **Sector Rotation**: Relative strength leaders in leading sectors.

*(Note: Mean Reversion is a conditional hedge strategy activated during bear/high-volatility regimes).*

### C. Composite Scoring Formula
Composite score combines 5 orthogonal dimensions (0–100 scale):
$$\text{Composite Score} = w_m \cdot S_m + w_e \cdot S_e + w_w \cdot S_w + w_r \cdot S_r + w_c \cdot S_c$$
* $S_m$: Continuous Technical Momentum (RSI distance from 50, DMA 50 proximity, Volume ratio, ATR-normalized MACD histogram)
* $S_e$: Historical Expectancy (Empirical Bayes shrunk expectancy)
* $S_w$: Historical Win Rate (Empirical Bayes Beta-Binomial shrunk win rate, neutral 50% prior, $\alpha = 5.0$)
* $S_r$: Regime Alignment (SPY 200 DMA trend & volatility alignment)
* $S_c$: Fundamental & News Context (Analyst consensus, D/E, Current ratio, Earnings surprise, FinBERT sentiment)

### D. Indicative Levels & Analytical Probabilities
* **Indicative Targets**: Calculated from strategy-specific ATR multiples with fixed percentage floors.
* **Scale-Out Model**:
  * Target 1 (T1): 50%
  * Target 2 (T2): 30%
  * Target 3 (T3): 20%
* **Realized Scale-Out Return**:
  $$\text{Return}_{\text{T3}} = 0.50 \cdot r_1 + 0.30 \cdot r_2 + 0.20 \cdot r_3$$
  For canonical targets $+10\%$, $+20\%$, $+30\%$, realized return on full target reach is $+17.0\%$ (not $+30.0\%$).
* **Same-Day Ambiguity Policy**: Conservative `STOP_FIRST`. If daily bar Low $\le \text{Stop}$ and High $\ge \text{Target}$, stop is filled first.

---

## 3. Recommendation Lifecycle & Exact-Instance Identity

### A. Instance Identity
* Every recommendation is tracked by exact database primary keys:
  * Active Recommendation: `signals.id` (UUID)
  * Historical Outcome: `signals_history.id` (BigINT), foreign key `signals_history.signal_id -> signals.id`
* **Hard Rule**: Ticker-only mutations are strictly prohibited. Updates require exact `signal_id` or `history_id`.

### B. Lifecycle States
1. **`pending`**: Qualified recommendation awaiting next market session entry.
2. **`open`**: Active recommendation open in the market.
3. **`stopped`**: Stopped out (Low $\le \text{Stop Loss}$).
4. **`hit_t3`**: Complete target exit reached (High $\ge \text{Target 3}$).
5. **`invalidated`**: Ticker no longer qualifies on a subsequent full market scan.
6. **`manually_removed`**: User dismissed recommendation from active ideas.

---

## 4. Market Date vs Execution Date Semantics

* **Market Data Date (`market_data_date`)**: Date of the US regular trading session (Eastern Time, `America/New_York`). On weekends, rolls back to Friday.
* **Execution Timestamp (`scan_execution_timestamp`)**: Exact UTC ISO 8601 timestamp of scan execution.
* **Recommendation Date (`recommendation_date`)**: Strictly equals `market_data_date`.

---

## 5. Supporting Evidence Safeguards

The frontend renders supporting evidence badges only when backed by genuine data:
* **"Positive earnings"**: Requires verified `earnings_surprise_pct > 0.0` or active catalyst.
* **"Positive news"**: Strictly requires `finbert_sentiment > 0.20`.
* **"Fundamentally strong"**: Strictly requires $\text{D/E} < 1.0$ AND $\text{Current Ratio} > 1.5$.
* **"Next earnings"**: Displays confirmed upcoming date and days buffer.
* Generic context scores are never converted into fake factual claims.
