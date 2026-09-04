"""
Quantitative Configuration Module
==================================
Canonical Single Source of Truth for Quantitative Parameters
Master Architecture & Quantitative Specification v2.3+ Compliant
"""

from typing import Dict, Any

# ==============================================================================
# 1. STRATEGY WEIGHT VECTORS (Section 10.2)
# Momentum (mom), Expectancy (exp), Win Rate (wr), Regime (reg), Context (ctx)
# Each vector must sum exactly to 1.0.
# ==============================================================================
STRATEGY_WEIGHT_VECTORS: Dict[str, Dict[str, float]] = {
    "trend_following": {
        "mom": 0.45,
        "exp": 0.20,
        "wr": 0.15,
        "reg": 0.10,
        "ctx": 0.10,
    },
    "52w_high_breakout": {
        "mom": 0.50,
        "exp": 0.15,
        "wr": 0.15,
        "reg": 0.10,
        "ctx": 0.10,
    },
    "pullback_recovery": {
        "mom": 0.25,
        "exp": 0.35,
        "wr": 0.15,
        "reg": 0.10,
        "ctx": 0.15,
    },
    "pead": {
        "mom": 0.30,
        "exp": 0.25,
        "wr": 0.15,
        "reg": 0.10,
        "ctx": 0.20,
    },
    "cross_sectional_momentum": {
        "mom": 0.40,
        "exp": 0.20,
        "wr": 0.20,
        "reg": 0.10,
        "ctx": 0.10,
    },
    "sector_rotation": {
        "mom": 0.35,
        "exp": 0.25,
        "wr": 0.15,
        "reg": 0.10,
        "ctx": 0.15,
    },
    "mean_reversion": {
        "mom": 0.10,
        "exp": 0.20,
        "wr": 0.15,
        "reg": 0.15,
        "ctx": 0.40,
    },
}

# ==============================================================================
# 2. EXACT REGIME SCORE MATRIX (Section 4.2)
# Strategy alignment scores across Bull, Sideways, and Bear regimes.
# ==============================================================================
REGIME_SCORE_MATRIX: Dict[str, Dict[str, float]] = {
    "trend_following":          {"bull": 100.0, "sideways": 70.0, "bear": 20.0},
    "52w_high_breakout":        {"bull": 100.0, "sideways": 60.0, "bear": 10.0},
    "cross_sectional_momentum": {"bull": 85.0,  "sideways": 75.0, "bear": 30.0},
    "sector_rotation":          {"bull": 80.0,  "sideways": 90.0, "bear": 40.0},
    "pullback_recovery":        {"bull": 70.0,  "sideways": 85.0, "bear": 50.0},
    "pead":                     {"bull": 75.0,  "sideways": 70.0, "bear": 70.0},
    "mean_reversion":           {"bull": 30.0,  "sideways": 65.0, "bear": 100.0},
}

# ==============================================================================
# 3. HISTORICAL STRATEGY EXPECTANCY & HAIRCUTS (Section 9.2 & 10.3)
# Survivorship bias haircut: 15% reduction (0.85 multiplier)
# Formula: S_exp = 30.0 + 20.0 * E_adjusted (E_adjusted in percentage points)
# ==============================================================================
SURVIVORSHIP_BIAS_HAIRCUT: float = 0.85

STRATEGY_HISTORICAL_EXPECTANCY: Dict[str, float] = {
    "trend_following":          0.0169 * SURVIVORSHIP_BIAS_HAIRCUT,  # +1.4365% -> 1.44%
    "52w_high_breakout":        0.0210 * SURVIVORSHIP_BIAS_HAIRCUT,  # +1.7850% -> 1.79%
    "pullback_recovery":        0.0145 * SURVIVORSHIP_BIAS_HAIRCUT,  # +1.2325% -> 1.23%
    "cross_sectional_momentum": 0.0180 * SURVIVORSHIP_BIAS_HAIRCUT,  # +1.5300% -> 1.53%
    "pead":                     0.0225 * SURVIVORSHIP_BIAS_HAIRCUT,  # +1.9125% -> 1.91%
    "sector_rotation":          0.0120 * SURVIVORSHIP_BIAS_HAIRCUT,  # +1.0200% -> 1.02%
    "mean_reversion":           0.0085 * SURVIVORSHIP_BIAS_HAIRCUT,  # +0.7225% -> 0.72%
}

# Base and multiplier for expectancy scoring: S_exp = EXPECTANCY_BASE + EXPECTANCY_SLOPE * E_adjusted
EXPECTANCY_BASE: float = 30.0
EXPECTANCY_SLOPE: float = 20.0

# ==============================================================================
# 4. CONTEXT VETO THRESHOLDS (Section 10.4)
# Balance Sheet Distress: D/E > 2.5 AND Current Ratio < 1.0 -> cap context at 30.0
# Negative News Sentiment: FinBERT < -0.30 -> cap context at 40.0
# Severe Earnings Miss: Surprise < -10.0% -> penalty -20.0
# Analyst Downside: Target < Price -> penalty -15.0
# ==============================================================================
CONTEXT_VETO_THRESHOLDS = {
    "de_ratio_max": 2.5,
    "current_ratio_min": 1.0,
    "context_cap_balance_sheet": 30.0,
    "finbert_sentiment_min": -0.30,
    "context_cap_news": 40.0,
    "earnings_surprise_min_pct": -10.0,  # in percent (-10.0%)
    "earnings_surprise_penalty": 20.0,
    "analyst_downside_penalty": 15.0,
}

# ==============================================================================
# 5. STRATEGY EARNINGS BLACKOUT WINDOWS (Section 8.2)
# Trading days prior to earnings release. PEAD has special post-earnings handling.
# ==============================================================================
EARNINGS_BLACKOUT_DAYS: Dict[str, int] = {
    "trend_following": 5,
    "52w_high_breakout": 5,
    "pullback_recovery": 3,
    "cross_sectional_momentum": 3,
    "sector_rotation": 4,
    "pead": 0,  # Exempt from pre-earnings blackout; trades post-earnings window
    "mean_reversion": 3,
}

# Earnings cache TTL: 24 hours (86,400 seconds)
EARNINGS_CACHE_TTL_SECONDS: int = 86400

# ==============================================================================
# 6. TARGET ATR MULTIPLIERS & MINIMUM TARGET FLOORS (Section 6.1)
# Formula: Tk = max(P_entry + Mk * ATR_14, P_entry * (1 + Fk))
# ==============================================================================
STRATEGY_TARGET_CONFIG: Dict[str, Dict[str, Any]] = {
    "trend_following": {
        "atr_k1": 2.5,
        "atr_k2": 5.0,
        "atr_k3": 8.0,
        "fixed_t1": 0.06,  # 6% floor
        "fixed_t2": 0.14,  # 14% floor
        "fixed_t3": 0.22,  # 22% floor
        "hold_days": 20,
        "t1_min": 0.30,
        "t2_min": 0.12,
        "t3_min": 0.15,
    },
    "52w_high_breakout": {
        "atr_k1": 2.0,
        "atr_k2": 4.0,
        "atr_k3": 7.0,
        "fixed_t1": 0.05,  # 5% floor
        "fixed_t2": 0.12,  # 12% floor
        "fixed_t3": 0.20,  # 20% floor
        "hold_days": 25,
        "t1_min": 0.25,
        "t2_min": 0.10,
        "t3_min": 0.15,
    },
    "pullback_recovery": {
        "atr_k1": 1.5,
        "atr_k2": 3.0,
        "atr_k3": 5.0,
        "fixed_t1": 0.04,  # 4% floor
        "fixed_t2": 0.09,  # 9% floor
        "fixed_t3": 0.15,  # 15% floor
        "hold_days": 10,
        "t1_min": 0.40,
        "t2_min": 0.20,
        "t3_min": 0.15,
    },
    "pead": {
        "atr_k1": 2.0,
        "atr_k2": 4.5,
        "atr_k3": 7.5,
        "fixed_t1": 0.05,  # 5% floor
        "fixed_t2": 0.13,  # 13% floor
        "fixed_t3": 0.22,  # 22% floor
        "hold_days": 5,
        "t1_min": 0.45,
        "t2_min": 0.25,
        "t3_min": 0.15,
    },
    "cross_sectional_momentum": {
        "atr_k1": 2.0,
        "atr_k2": 4.0,
        "atr_k3": 6.5,
        "fixed_t1": 0.05,  # 5% floor
        "fixed_t2": 0.11,  # 11% floor
        "fixed_t3": 0.18,  # 18% floor
        "hold_days": 15,
        "t1_min": 0.35,
        "t2_min": 0.15,
        "t3_min": 0.15,
    },
    "sector_rotation": {
        "atr_k1": 1.8,
        "atr_k2": 3.5,
        "atr_k3": 6.0,
        "fixed_t1": 0.045,  # 4.5% floor
        "fixed_t2": 0.10,   # 10% floor
        "fixed_t3": 0.16,   # 16% floor
        "hold_days": 20,
        "t1_min": 0.35,
        "t2_min": 0.18,
        "t3_min": 0.15,
    },
    "mean_reversion": {
        "atr_k1": 1.0,
        "atr_k2": 2.0,
        "atr_k3": 3.5,
        "fixed_t1": 0.03,  # 3% floor
        "fixed_t2": 0.06,  # 6% floor
        "fixed_t3": 0.10,  # 10% floor
        "hold_days": 5,
        "t1_min": 0.40,
        "t2_min": 0.20,
        "t3_min": 0.15,
    },
}

# ==============================================================================
# 7. REACH PROBABILITY & SCALE-OUT LOGIC (Section 6.2 & 7.1)
# T3 survival threshold: 15.0% (0.1500)
# Scale-out weights:
# - All 3 survive (T1, T2, T3): 50% at T1, 30% at T2, 20% at T3 ("50/30/20")
# - T1 and T2 survive (T3 pruned): 60% at T1, 40% at T2, 0% at T3 ("60/40/0")
# - Only T1 survives: 70% at T1, 30% runner ("70/30/0")
# ==============================================================================
T3_REACH_PROB_SURVIVAL_THRESHOLD: float = 0.15  # 15.0%

SCALE_OUT_WEIGHTS = {
    "all_three": {"t1": 0.50, "t2": 0.30, "t3": 0.20, "label": "50/30/20"},
    "t1_t2_only": {"t1": 0.60, "t2": 0.40, "t3": 0.0, "label": "60/40/0"},
    "t1_only": {"t1": 0.70, "t2": 0.0, "t3": 0.0, "label": "70/30/0"},
}

# Minimum valid historical sliding windows required for empirical reach probability
MIN_REACH_PROB_WINDOWS: int = 20
