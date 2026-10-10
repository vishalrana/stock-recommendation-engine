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
# 3. STRATEGY EVIDENCE (win rate & expectancy)
# Per-strategy win rate and expectancy come from the production-pipeline backtest
# on the production universe (backtest_production_universe.yml, adopted into
# config/strategy_performance.json by scripts/adopt_strategy_evidence.py), shrunk toward
# neutral priors (50% win rate, 0% expectancy) by trade count. See src/strategy_evidence.py.
# There are no hard-coded expectancy assumptions.
#
# Reach probability fallback haircut: 8% reduction (0.92 multiplier) when delisted sector proxies
# are unavailable (applies only to the ticker base-rate fallback, see survivorship_bias.py).
# Formula: S_exp = 30.0 + 20.0 * E (E = shrunk per-trade expectancy in percentage points)
# ==============================================================================
REACH_PROB_FALLBACK_HAIRCUT: float = 0.92

# Base and multiplier for expectancy scoring: S_exp = EXPECTANCY_BASE + EXPECTANCY_SLOPE * E
EXPECTANCY_BASE: float = 30.0
EXPECTANCY_SLOPE: float = 20.0

# Minimum completed trades of a strategy before its backtest target-hit rates replace the
# ticker base-rate reach probability (setup-conditional reach probability).
MIN_STRATEGY_REACH_TRADES: int = 30

# ==============================================================================
# SELECTION SWITCHES (shared by the nightly scan and the backtest)
# ==============================================================================
# Momentum sub-score model:
#   "technical"              RSI / 50-DMA proximity / volume / MACD blend (original)
#   "relative_strength_12m"  percentile (0-100) of the 12-1 month return (t-252 -> t-21) among
#                            liquid US stocks on the same date (sector ETFs among ETFs)
MOMENTUM_MODEL: str = "technical"
RS_LOOKBACK_BARS: int = 252
RS_SKIP_BARS: int = 21

# Entry-location filter: "gate" blocks WAIT / REJECT verdicts; "info" records the verdict for
# display without blocking.
ENTRY_LOCATION_MODE: str = "gate"

# Context components that count toward the context score (and their veto gates). A component
# left out is still fetched and displayed but cannot move the composite score. Fundamentals (D/E,
# current ratio) are display-only: in the point-in-time SEC test their points and the distress
# veto carried no information about forward returns. () = no context in the score.
CONTEXT_SCORE_COMPONENTS: tuple = ("analyst", "news")

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
    "all_three": {"t1": 0.50, "t2": 0.30, "t3": 0.20, "runner": 0.0, "label": "50/30/20"},
    "t1_t2_only": {"t1": 0.60, "t2": 0.40, "t3": 0.0, "runner": 0.0, "label": "60/40/0"},
    "t1_only": {"t1": 0.70, "t2": 0.0, "t3": 0.0, "runner": 0.30, "label": "70/30/0"},
}

# Minimum valid historical sliding windows required for empirical reach probability
MIN_REACH_PROB_WINDOWS: int = 20

# ==============================================================================
# 8. STRATEGY STOP LOSS CONFIGURATION
# Each strategy sets its own structural stop (ATR multiple, swing low, 50 DMA, gap low, ...).
# That stop is used as issued: no minimum-distance widening and no maximum-risk clamp.
# ==============================================================================
STRATEGY_STOP_CONFIG: Dict[str, Dict[str, float]] = {
    "trend_following": {"atr_multiplier": 2.5},
    "52w_high_breakout": {"atr_multiplier": 2.0},
    "pullback_recovery": {"atr_multiplier": 1.5},
    "pead": {"atr_multiplier": 2.0},
    "cross_sectional_momentum": {"atr_multiplier": 2.0},
    "sector_rotation": {"atr_multiplier": 1.8},
    "mean_reversion": {"atr_multiplier": 1.0},
}


# ==============================================================================
# CANONICAL STRATEGY & REGIME REGISTRIES (Section 3 & 4)
# ==============================================================================
CANONICAL_STRATEGIES: set[str] = {
    "trend_following",
    "52w_high_breakout",
    "pullback_recovery",
    "cross_sectional_momentum",
    "pead",
    "sector_rotation",
    "mean_reversion",
}

STRATEGY_ALIASES: Dict[str, str] = {
    # trend_following
    "trend_following": "trend_following",
    "trend following": "trend_following",
    "trend": "trend_following",
    # 52w_high_breakout
    "52w_high_breakout": "52w_high_breakout",
    "52_week_high_breakout": "52w_high_breakout",
    "52-week high breakout": "52w_high_breakout",
    "52-week high": "52w_high_breakout",
    "52_week_high": "52w_high_breakout",
    "52w_high": "52w_high_breakout",
    "52w-high": "52w_high_breakout",
    "52w-high-breakout": "52w_high_breakout",
    "week_52_high": "52w_high_breakout",
    "52 week high": "52w_high_breakout",
    # pullback_recovery
    "pullback_recovery": "pullback_recovery",
    "pullback recovery": "pullback_recovery",
    "pullback": "pullback_recovery",
    # cross_sectional_momentum
    "cross_sectional_momentum": "cross_sectional_momentum",
    "cross-sectional momentum": "cross_sectional_momentum",
    "cross_sectional": "cross_sectional_momentum",
    "cross sectional momentum": "cross_sectional_momentum",
    "cross-sectional": "cross_sectional_momentum",
    "cross sectional": "cross_sectional_momentum",
    # pead
    "pead": "pead",
    "post_earnings_drift": "pead",
    "post-earnings drift": "pead",
    "post earnings drift": "pead",
    # sector_rotation
    "sector_rotation": "sector_rotation",
    "sector rotation": "sector_rotation",
    # mean_reversion
    "mean_reversion": "mean_reversion",
    "mean reversion": "mean_reversion",
}


def normalize_strategy_key(strategy: str) -> str:
    """
    Authoritative Strategy Normalizer (Single Source of Truth).
    Explicitly maps supported strategies and canonical aliases.
    Fails closed on missing or unknown strategies (raises ValueError).
    No substring heuristics, no silent default to trend_following.
    """
    if strategy is None:
        raise ValueError("Missing strategy: strategy name cannot be None")
    s = str(strategy).strip().lower()
    if s in STRATEGY_ALIASES:
        return STRATEGY_ALIASES[s]
    s_clean = s.replace("-", "_").replace(" ", "_")
    if s_clean in STRATEGY_ALIASES:
        return STRATEGY_ALIASES[s_clean]
    raise ValueError(
        f"Unknown or unsupported strategy: '{strategy}'. Supported canonical strategies: {sorted(CANONICAL_STRATEGIES)}"
    )


CANONICAL_REGIMES: set[str] = {"bull", "sideways", "bear"}


def normalize_regime_key(regime: str) -> str:
    """
    Authoritative Market Regime Normalizer (Single Source of Truth).
    Explicitly validates and normalizes market regime string (bull, sideways, bear).
    Fails closed on missing or unknown market regime (raises ValueError).
    No silent substitution to sideways.
    """
    if regime is None:
        raise ValueError("Missing market regime: regime cannot be None")
    r = str(regime).strip().lower()
    if r not in CANONICAL_REGIMES:
        raise ValueError(
            f"Unknown or unsupported market regime: '{regime}'. Supported regimes: {sorted(CANONICAL_REGIMES)}"
        )
    return r


# ==============================================================================
# 11. US UNIVERSE & LIQUIDITY FILTERS (Section 11)
# Configurable universe-level filters for broad US equity discovery
# ==============================================================================
US_UNIVERSE_MIN_PRICE: float = 5.0                  # Minimum stock price ($)
US_UNIVERSE_MIN_DOLLAR_VOLUME: float = 5_000_000.0  # 20-day average daily dollar volume ($5M)
US_UNIVERSE_MIN_HISTORY_DAYS: int = 252             # 1 trading year of history required
US_UNIVERSE_DOLLAR_VOLUME_WINDOW: int = 20          # 20 trading sessions for dollar volume window

# ==============================================================================
# 12. BAYESIAN SHRINKAGE CONFIGURATION (Section 2)
# ==============================================================================
BAYESIAN_SHRINKAGE_ALPHA: float = 5.0
BAYESIAN_PRIOR_WIN_RATE: float = 50.0  # 50.0% neutral prior
BAYESIAN_PRIOR_EXPECTANCY_PCT: float = 0.0  # Neutral prior: no assumed edge
MIN_SAMPLE_SIZE_EVIDENCE: int = 5

# ==============================================================================
# 13. EMPIRICAL SCORE CALIBRATION BANDS (Section 3 & 4)
# ==============================================================================
SCORE_CALIBRATION_BANDS = [
    {"band": "50-54.99", "min": 50.0, "max": 54.99},
    {"band": "55-59.99", "min": 55.0, "max": 59.99},
    {"band": "60-64.99", "min": 60.0, "max": 64.99},
    {"band": "65-69.99", "min": 65.0, "max": 69.99},
    {"band": "70-74.99", "min": 70.0, "max": 74.99},
    {"band": "75-79.99", "min": 75.0, "max": 79.99},
    {"band": "80+",      "min": 80.0, "max": 100.0},
]

# ==============================================================================
# 14. EARNINGS & CATALYST REASON CODES (Section 6)
# ==============================================================================
REASON_EARNINGS_POSITIVE_CATALYST_OVERRIDE = "EARNINGS_POSITIVE_CATALYST_OVERRIDE"
REASON_EARNINGS_NEGATIVE_CATALYST_BLOCK = "EARNINGS_NEGATIVE_CATALYST_BLOCK"
REASON_EARNINGS_DATE_UNKNOWN_NO_CATALYST = "EARNINGS_DATE_UNKNOWN_NO_CATALYST"
REASON_EARNINGS_DATE_UNKNOWN_POSITIVE = "EARNINGS_DATE_UNKNOWN_POSITIVE_CATALYST"
REASON_EARNINGS_DATE_UNKNOWN_NEGATIVE = "EARNINGS_DATE_UNKNOWN_NEGATIVE_CATALYST_BLOCK"
REASON_EARNINGS_OUTSIDE_BLACKOUT = "EARNINGS_OUTSIDE_BLACKOUT"
REASON_EARNINGS_BLACKOUT_BLOCK = "EARNINGS_BLACKOUT_BLOCK"
REASON_SECTOR_ETF_EXEMPT = "SECTOR_ETF_EXEMPT"

# ==============================================================================
# 15. CATALYST RECENCY CONFIGURATION & AMBIGUITY POLICY
# ==============================================================================
EARNINGS_CATALYST_MAX_AGE_DAYS: int = 45   # Positive earnings surprise must be <= 45 days old to override blackout
NEWS_CATALYST_MAX_AGE_DAYS: int = 14       # Positive news sentiment must be recent (<= 14 days)
SAME_DAY_AMBIGUITY_POLICY: str = "STOP_FIRST"


