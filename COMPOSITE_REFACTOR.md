# Composite Scoring Engine Refactor Specification & Validation (v2.2)

## Executive Summary
This refactor resolves five known mathematical, statistical, and architectural flaws in the composite scoring and position sizing pipeline of the Stock Recommendation Engine.

---

## 1. The 5 Architectural & Mathematical Fixes

### Fix 1: Sigmoid Win-Probability Interpolation (Smooth Kelly Curve)
* **Problem**: Six coarse step-buckets collapsed 14-point score ranges into identical probabilities (e.g. scores 80 to 89 all received 0.68, while 47.25 dropped to a floor of 0.35).
* **Fix**: Replaced step-wise bucket logic with a continuous logistic sigmoid function centered at score 65:
  $$p_{\text{win}} = 0.35 + \frac{0.40}{1 + e^{-0.15 \times (S_{\text{composite}} - 65)}}$$
  Clamped strictly to $[0.35, 0.75]$ and rounded to 4 decimal places.
* **Validation**:
  - Score 47.25 (PLTR) $\implies p_{\text{win}} = 0.3761$ (was 0.3500).
  - Score 42.00 (CRL) $\implies p_{\text{win}} = 0.3623$ (was 0.3500).
  - Score 65.00 $\implies p_{\text{win}} = 0.5500$.
  - Score 80.00 $\implies p_{\text{win}} = 0.7121$.

---

### Fix 2: Break Circular R:R Dependency
* **Problem**: Expectancy sub-score $S_{\text{exp}}$ previously incorporated $R_{\text{honest}}$, which is derived from the signal's targets and stop loss. Because $S_{\text{composite}}$ dictates Half-Kelly sizing, incorporating $R_{\text{honest}}$ into $S_{\text{composite}}$ inflated scores for high R:R setups and distorted sizing.
* **Fix**: Removed $R_{\text{honest}}$ from composite scoring entirely. $R_{\text{honest}}$ is used **only** in the Half-Kelly position sizer ($f^* = p - \frac{1-p}{b}$). $S_{\text{exp}}$ is computed exclusively from historical strategy backtest performance:
  $$S_{\text{exp}} = \text{clip}\left(\frac{\text{StrategyHistExpectancy}_{\%} + 2.0}{10.0} \times 100, 0, 100\right)$$
* **Historical Strategy Lookup**:
  | Strategy | Historical Expectancy % | Expectancy Sub-Score $S_{\text{exp}}$ |
  | :--- | :---: | :---: |
  | PEAD | +2.25% | 42.5 |
  | 52-Week High Breakout | +2.10% | 41.0 |
  | Cross-Sectional Momentum | +1.80% | 38.0 |
  | Trend Following | +1.69% | 36.9 |
  | Pullback Recovery | +1.45% | 34.5 |
  | Sector Rotation | +1.20% | 32.0 |
  | Mean Reversion | +0.85% | 28.5 |

---

### Fix 3: Context Score Veto Gates
* **Problem**: Raw context score was an unconstrained additive sum ($0 \dots 100$) that allowed high analyst upside to mask dangerous financial leverage, poor liquidity, or severe earnings misses.
* **Fix**: Added 4 deterministic veto gates:
  1. **Leverage & Liquidity Veto**: If $\text{Debt-to-Equity} > 2.0$ and $\text{Current Ratio} < 0.8 \implies \text{Context Score} = \min(\text{Raw}, 30.0)$.
  2. **FinBERT News Sentiment Veto**: If $\text{FinBERT Sentiment} < -0.20 \implies \text{Context Score} = \min(\text{Raw}, 40.0)$.
  3. **Earnings Miss Penalty**: If $\text{Earnings Surprise} < -5.0\% \implies \text{Context Score} = \text{Raw} - 20.0$.
  4. **Analyst Downside Penalty**: If $\text{Consensus Target} < \text{Current Price} \implies \text{Context Score} = \text{Raw} - 15.0$.
  Final score is clamped to $[0, 100]$.

---

### Fix 4: Strategy-Specific Composite Weight Vectors
* **Problem**: Global regime-wide weights assumed every strategy reacted identically to momentum vs fundamentals.
* **Fix**: Replaced regime-level vectors with per-strategy weight vectors tailored to each strategy's alpha drivers:
  | Strategy | Momentum ($w_{\text{mom}}$) | Expectancy ($w_{\text{exp}}$) | Win Rate ($w_{\text{wr}}$) | Regime ($w_{\text{reg}}$) | Context ($w_{\text{ctx}}$) | Total |
  | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
  | **Trend Following** | 0.45 | 0.20 | 0.15 | 0.10 | 0.10 | **1.00** |
  | **52-Week High Breakout** | 0.35 | 0.20 | 0.20 | 0.15 | 0.10 | **1.00** |
  | **Pullback Recovery** | 0.20 | 0.25 | 0.20 | 0.10 | 0.25 | **1.00** |
  | **Cross-Sectional Momentum** | 0.40 | 0.20 | 0.20 | 0.10 | 0.10 | **1.00** |
  | **PEAD** | 0.15 | 0.30 | 0.15 | 0.10 | 0.30 | **1.00** |
  | **Sector Rotation** | 0.25 | 0.25 | 0.20 | 0.20 | 0.10 | **1.00** |
  | **Mean Reversion** | 0.10 | 0.20 | 0.15 | 0.15 | 0.40 | **1.00** |

---

### Fix 5: Continuous Strategy-Dependent Regime Alignment
* **Problem**: Binary regime step rewards penalized strategies that perform well in non-bull markets (e.g. Mean Reversion in Bear).
* **Fix**: Continuous distance metric comparing strategy's optimal environment ($S_{\text{opt}}$) against market regime ($S_{\text{mkt}}$):
  $$S_{\text{reg}} = \text{clip}\left(100 - |S_{\text{opt}} - S_{\text{mkt}}| \times 0.6, 0, 100\right)$$
  - Bull: $S_{\text{mkt}} = 100$
  - Sideways: $S_{\text{mkt}} = 50$
  - Bear: $S_{\text{mkt}} = 0$

* **Optimal Regime Profile & Alignment Scores**:
  | Strategy | Optimal Regime Score | Bull Alignment | Sideways Alignment | Bear Alignment |
  | :--- | :---: | :---: | :---: | :---: |
  | Trend Following | 100 | 100.0 | 70.0 | 40.0 |
  | 52-Week High Breakout | 100 | 100.0 | 70.0 | 40.0 |
  | Cross-Sectional Momentum | 75 | 85.0 | 85.0 | 55.0 |
  | Pullback Recovery | 50 | 70.0 | 100.0 | 70.0 |
  | PEAD | 50 | 70.0 | 100.0 | 70.0 |
  | Sector Rotation | 75 | 85.0 | 85.0 | 55.0 |
  | Mean Reversion | 0 | 40.0 | 70.0 | 100.0 |

---

## 2. Fractional Shares Sizing (`exact_shares`)
* `exact_shares` (`NUMERIC(10, 4)`): Canonical sizing metric computed as `round(allocated_dollars / entry_price, 4)`.
* `max_shares` (`INTEGER`): Maintained for database backward compatibility as `int(floor(exact_shares))`.
* **Example**:
  - CRL allocated $\$30.42$ at entry $\$296.41$:
    - `exact_shares = 0.1026`
    - `max_shares = 0`

---

## 3. Test Suite Verification
Test suite `scripts/test_composite_refactor.py` was executed with all 5 acceptance scenarios passing:
```
================================================================================
  COMPOSITE SCORING REFACTOR -- ACCEPTANCE VERIFICATION SUITE
================================================================================

[Scenario A] PLTR (Trend Following, Composite Score = 47.25)
  * Sigmoid Win Probability: p_win = 0.3761 (Old bucket was 0.3500)
  * Strategy Expectancy Score: S_exp = 36.9 (from +1.69% historical expectancy)
  * Strategy Weight Vector: {'mom': 0.45, 'exp': 0.2, 'wr': 0.15, 'reg': 0.1, 'ctx': 0.1}
  * Bull Regime Alignment: S_reg = 100.0
  * Healthy Context (D/E=0.5, Current=2.1): 75.0 (no veto)
  * Dangerous Context (D/E=3.0, Current=0.5): 30.0 (capped at 30.0)
  --> PASS Scenario A: PLTR parameters, weights, expectancy, and context veto validated.

[Scenario B] CRL (Cross-Sectional Momentum, Composite Score = 42.0)
  * Sigmoid Win Probability: p_win = 0.3623 (Old bucket was 0.3500)
  * Strategy Expectancy Score: S_exp = 38.0 (from +1.80% historical expectancy)
  * Strategy Weight Vector: {'mom': 0.4, 'exp': 0.2, 'wr': 0.2, 'reg': 0.1, 'ctx': 0.1}
  * Bull Regime Alignment: S_reg = 85.0
  --> PASS Scenario B: CRL weights and historical expectancy validated.

[Scenario C] Context Veto Gate Matrix
  * Raw Context: 70.0 -> After Veto (D/E=3.0, Current=0.5): 30.0
  * Raw Context: 75.0 -> After News Sentiment Veto (-0.35): 40.0
  * Raw Context: 75.0 -> After Earnings Miss Penalty (-0.12): 55.0
  * Raw Context: 75.0 -> After Analyst Downside Penalty (Target $90 vs Price $100): 60.0
  --> PASS Scenario C: Context Veto Gates 1, 2, 3, and 4 validated.

[Scenario D] Continuous Regime Alignment Scores
  * Trend Following in Sideways: S_reg = 70.0 (Old system was 65)
  * PEAD in Bear: S_reg = 70.0 (Old system was 30)
  * Mean Reversion in Bear: S_reg = 100.0 (Old system was 30)
  --> PASS Scenario D: Continuous Regime Alignment validated.

[Scenario E] Fractional Shares & Capital Sizing
  * Allocated: $30.42 on Entry $296.41
  * Exact Shares (canonical NUMERIC): 0.1026
  * Max Shares (INTEGER fallback): 0
  --> PASS Scenario E: Exact fractional shares and integer fallback validated.

[Integration Test] Full SignalRanker.compute_composite_score()
  * PLTR Composite Output: 61.88 (Weights: {'mom': 0.45, 'exp': 0.2, 'wr': 0.15, 'reg': 0.1, 'ctx': 0.1})
  * Breakdown: {'momentum': 65.0, 'expectancy': 36.9, 'winrate': 55.0, 'regime': 100.0, 'context': 70.0}

================================================================================
  ALL 5 ACCEPTANCE SCENARIOS PASSED WITH ZERO ERRORS!
================================================================================
```
