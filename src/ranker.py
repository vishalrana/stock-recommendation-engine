"""
Signal Ranker — Canonical Quantitative Specification v2.3+
=============================================================
Absolute continuous, strategy-specific composite ranking engine.
All strategy weight vectors and discrete regime score matrices are
sourced exclusively from src.quant_config (single source of truth).

Sub-Scores (each normalized to continuous [0.0, 100.0]):
  - Technical Momentum: RSI, Proximity to 50 DMA, Volume Ratio, ATR-normalized MACD Histogram
  - Historical Expectancy: S_exp = 30 + 20 * E_adjusted (empirically derived)
  - Historical Win Rate: Empirical strategy win rate
  - Continuous Regime Alignment: Discrete matrix lookup per strategy and regime
  - Context Score: Analyst, Fundamental, News sentiment with hard veto gates
"""

import logging
import math
import os
from typing import Optional
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime

from src.providers.context.aggregator import ContextAggregator
from src.scorers.context_scorer import ContextScorer
from src.data.cache_manager import get_cache_manager

logger = logging.getLogger(__name__)


# Import canonical single source of truth configuration
from src.quant_config import (
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
    EXPECTANCY_BASE,
    EXPECTANCY_SLOPE,
    CONTEXT_VETO_THRESHOLDS,
    CANONICAL_STRATEGIES,
    CANONICAL_REGIMES,
    normalize_strategy_key,
    normalize_regime_key,
)


def compute_expectancy_score(strategy: str, adjusted_expectancy_pct: Optional[float] = None) -> float:
    """
    Master Spec v2.3+ formula: S_exp = 30 + 20 * E
    where E is the per-trade expectancy in percentage points (e.g. +1.44% -> 1.44 -> 58.8).
    Strictly clamped to [0.0, 100.0]. When no value is given (None/NaN) it falls back to the
    strategy's shrunk backtest expectancy (src.strategy_evidence; neutral 0% without evidence).
    Rejects infinite values with ValueError rather than concealing via clamping.
    """
    e_val = float("nan")
    if adjusted_expectancy_pct is not None:
        try:
            e_val = float(adjusted_expectancy_pct)
        except (ValueError, TypeError):
            e_val = float("nan")
        if math.isinf(e_val):
            raise ValueError(f"adjusted_expectancy_pct must be finite, got {adjusted_expectancy_pct}")
    if math.isnan(e_val):
        from src.strategy_evidence import get_strategy_evidence
        e_val = float(get_strategy_evidence(strategy).shrunk_expectancy)
    raw_score = EXPECTANCY_BASE + EXPECTANCY_SLOPE * e_val
    clamped_score = max(0.0, min(100.0, raw_score))
    return round(clamped_score, 4)


def compute_regime_alignment(strategy: str, market_regime: str) -> float:
    """
    Master Spec v2.3+ Exact Regime Score Matrix.
    Direct discrete matrix lookup across Bull, Sideways, and Bear regimes.
    Fails closed on missing or unknown strategy or market regime.
    """
    strat_key = normalize_strategy_key(strategy)
    regime_key = normalize_regime_key(market_regime)
    if strat_key not in REGIME_SCORE_MATRIX:
        raise ValueError(f"Strategy '{strat_key}' not found in REGIME_SCORE_MATRIX")
    regime_dict = REGIME_SCORE_MATRIX[strat_key]
    if regime_key not in regime_dict:
        raise ValueError(f"Regime '{regime_key}' not found for strategy '{strat_key}'")
    return float(regime_dict[regime_key])


def compute_context_score(
    analyst_pts: float = 0.0,
    earnings_pts: float = 0.0,
    fundamental_pts: float = 0.0,
    news_pts: float = 0.0,
    de_ratio: Optional[float] = None,
    current_ratio: Optional[float] = None,
    earnings_surprise_pct: Optional[float] = None,
    finbert_sentiment: Optional[float] = None,
    target_consensus: Optional[float] = None,
    price: Optional[float] = None,
    base_score: Optional[float] = None,
    max_points: Optional[float] = None,
) -> float:
    """
    Master Spec Context Score (Earnings Decoupled):
    Calculates context score from non-earnings factors (Analyst, Fundamental, News)
    normalized to a 0-100 scale, with Veto Gates:
    - Balance Sheet Distress: D/E > 2.5 AND Current Ratio < 1.0 -> cap context at 30.0
    - Negative News Sentiment: FinBERT < -0.30 -> cap context at 40.0
    - Analyst Downside: Target < Price -> penalty -15.0

    Earnings data is completely excluded from the initial score.
    (Veto Gate 3 severe earnings miss is evaluated downstream in earnings_risk_filter).
    """
    if base_score is not None:
        raw = min(100.0, max(0.0, float(base_score)))
    elif max_points is not None and float(max_points) > 0:
        # Share of the points AVAILABLE for this stock (components with data), so a missing
        # component (e.g. no analyst coverage) is excluded rather than scored as zero.
        earned = float(analyst_pts or 0.0) + float(fundamental_pts or 0.0) + float(news_pts or 0.0)
        raw = min(100.0, max(0.0, earned / float(max_points) * 100.0))
    else:
        raw_non_earnings = float(analyst_pts or 0.0) + float(fundamental_pts or 0.0) + float(news_pts or 0.0)
        # Normalize non-earnings components: unscaled ceiling is 80 (Analyst 40, Fundamental 20, News 20)
        # Scaled by 100 / 80 = 1.25 to map cleanly to [0, 100].
        raw = min(100.0, raw_non_earnings * 1.25) if raw_non_earnings > 0 else 0.0

    # Veto Gate 1: Dangerous leverage + poor liquidity (D/E > 2.5 AND Current Ratio < 1.0)
    if de_ratio is not None and current_ratio is not None:
        try:
            if float(de_ratio) > 2.5 and float(current_ratio) < 1.0:
                raw = min(raw, 30.0)
        except (ValueError, TypeError):
            pass

    # Veto Gate 2: Negative news sentiment (FinBERT < -0.30)
    if finbert_sentiment is not None:
        try:
            if float(finbert_sentiment) < -0.30:
                raw = min(raw, 40.0)
        except (ValueError, TypeError):
            pass

    # Veto Gate 4: Analyst downside (Target < Entry Price)
    if target_consensus is not None and price is not None:
        try:
            p = float(price)
            tc = float(target_consensus)
            if p > 0 and tc < p:
                raw = max(0.0, raw - 15.0)
        except (ValueError, TypeError):
            pass

    return max(0.0, min(100.0, float(raw)))


def assign_tier(composite_score: float, honest_rr: float = 2.0, has_strategy_setup: bool = True) -> str:
    """
    Assign recommendation tier based strictly on composite score and strategy qualification.
    R:R is preserved as an analytical output and for signature compatibility only,
    and NEVER qualifies or disqualifies a recommendation.
    If has_strategy_setup is False, returns 'NO_SETUP' (preventing unmerited Buy tiers).
    - Strong Buy: composite_score >= 80.0
    - Buy: composite_score >= 65.0
    - Rejected: all others
    """
    if not has_strategy_setup:
        return "NO_SETUP"
    score = float(composite_score)
    if score >= 80.0:
        return "Strong Buy"
    elif score >= 65.0:
        return "Buy"
    else:
        return "Rejected"


# Strategies whose setups are defined by strength (momentum score rewards strength for these)
STRENGTH_SEEKING_STRATEGIES = {
    "trend_following",
    "52w_high_breakout",
    "cross_sectional_momentum",
    "sector_rotation",
    "pead",
}


def compute_momentum_score(row: dict) -> float:
    """
    P0-2 & P1-1 & P1-2: Explicit continuous technical momentum score (0-100).
    Uses RSI, DMA 50 proximity, Volume Ratio, and MACD Histogram (normalized by ATR).
    Strictly requires valid ATR, DMA 50, and MACD histogram; never substitutes arbitrary percentages or proxies.
    Fails closed if any required feature is missing, invalid, or non-finite.
    """
    rsi = row.get("current_rsi")
    price = row.get("price") if row.get("price") is not None else row.get("entry_price")
    dma_50 = row.get("dma_50")
    volume_ratio = row.get("volume_ratio")
    macd_hist = row.get("macd_histogram")
    atr_val = row.get("atr_14") or row.get("atr")

    if (
        rsi is None
        or not np.isfinite(rsi)
        or price is None
        or not np.isfinite(price)
        or float(price) <= 0
        or dma_50 is None
        or not np.isfinite(dma_50)
        or float(dma_50) <= 0
        or volume_ratio is None
        or not np.isfinite(volume_ratio)
        or float(volume_ratio) < 0
        or macd_hist is None
        or not np.isfinite(macd_hist)
        or atr_val is None
        or not np.isfinite(atr_val)
        or float(atr_val) <= 0
    ):
        raise ValueError(
            f"Missing or invalid required technical momentum features: rsi={rsi}, price={price}, dma_50={dma_50}, volume_ratio={volume_ratio}, macd_histogram={macd_hist}, atr={atr_val}"
        )

    rsi_val = float(rsi)
    p_val = float(price)
    d_val = float(dma_50)
    v_val = float(volume_ratio)
    m_val = float(macd_hist)
    atr = float(atr_val)

    strat = row.get("strategy") or row.get("strategy_name")
    try:
        strat_key = normalize_strategy_key(strat) if strat else None
    except ValueError:
        strat_key = None

    if strat_key in STRENGTH_SEEKING_STRATEGIES:
        # Trend / breakout / momentum / drift setups: reward strength, not neutrality.
        # RSI peaks at 65 (strong but not exhausted); price is best moderately above the
        # 50 DMA (~5%), scoring 0 below it and fading when over-extended.
        rsi_score = max(0.0, min(100.0, 100.0 - abs(rsi_val - 65.0) * 4.0))
        extension = p_val / d_val - 1.0 if d_val > 0 else 0.0
        if extension < 0:
            proximity_score = 0.0
        else:
            proximity_score = max(0.0, min(100.0, 100.0 - abs(extension - 0.05) * 500.0))
    else:
        # Pullback / mean-reversion setups: best near the RSI midline and close to the 50 DMA.
        rsi_score = max(0.0, min(100.0, 100.0 - abs(rsi_val - 50.0) * 4.0))
        proximity = abs(p_val / d_val - 1.0) if d_val > 0 else 0.0
        proximity_score = max(0.0, min(100.0, 100.0 - proximity * 500.0))

    # Volume score
    volume_score = max(0.0, min(100.0, v_val * 50.0))

    # MACD score normalized by ATR:
    macd_norm = m_val / atr
    macd_score = max(0.0, min(100.0, 50.0 + macd_norm * 200.0))

    raw_momentum = (rsi_score + proximity_score + volume_score + macd_score) / 4.0

    # Sigmoid weighting around 55.0
    sigmoid_weight = 1.0 / (1.0 + math.exp(-(raw_momentum - 55.0) / 5.0))
    momentum_score = raw_momentum * (0.5 + 0.5 * sigmoid_weight)
    return round(max(0.0, min(100.0, momentum_score)), 4)


def validate_candidate_features(row: dict) -> tuple[bool, str]:
    """
    P0-2: Validate that all required production features exist before composite scoring.
    Fail-closed: all required features must be present, finite, and valid.
    """
    ticker = row.get("ticker")
    if not ticker:
        return False, "Missing ticker"

    strategy = row.get("strategy") or row.get("strategy_name")
    if not strategy:
        return False, "Missing strategy"
    try:
        normalize_strategy_key(strategy)
    except ValueError as e:
        return False, f"Invalid strategy: {e}"

    # Technical momentum check
    if row.get("momentum_score") is not None:
        ms = row.get("momentum_score")
        if not np.isfinite(ms) or float(ms) < 0.0 or float(ms) > 100.0:
            return False, f"Invalid precomputed momentum_score: {ms} (must be finite and in [0.0, 100.0])"
    else:
        rsi = row.get("current_rsi")
        if rsi is None or not np.isfinite(rsi) or float(rsi) < 0.0 or float(rsi) > 100.0:
            return False, f"Missing or invalid current_rsi: {rsi}"

        price = row.get("price") if row.get("price") is not None else row.get("entry_price")
        if price is None or not np.isfinite(price) or float(price) <= 0:
            return False, f"Missing or non-positive price/entry_price: {price}"

        dma_50 = row.get("dma_50")
        if dma_50 is None or not np.isfinite(dma_50) or float(dma_50) <= 0:
            return False, f"Missing or non-positive dma_50: {dma_50}"

        vol = row.get("volume_ratio")
        if vol is None or not np.isfinite(vol) or float(vol) < 0:
            return False, f"Missing or invalid volume_ratio: {vol}"

        macd = row.get("macd_histogram")
        if macd is None or not np.isfinite(macd):
            return False, f"Missing or non-finite macd_histogram: {macd}"

        atr_raw = row.get("atr_14") if row.get("atr_14") is not None else row.get("atr")
        if atr_raw is None or not np.isfinite(atr_raw) or float(atr_raw) <= 0:
            return False, f"Missing or non-positive atr_14: {atr_raw}"

    # Win rate check: must have winrate_score or win_rate or past_win_rate in [0.0, 100.0]
    winrate_val = row.get("winrate_score")
    if winrate_val is None:
        winrate_val = row.get("win_rate")
    if winrate_val is None:
        winrate_val = row.get("past_win_rate")
    if winrate_val is None or not np.isfinite(winrate_val) or float(winrate_val) < 0.0 or float(winrate_val) > 100.0:
        return False, f"Missing or invalid winrate_score / past_win_rate: {winrate_val} (must be finite and in [0.0, 100.0])"

    return True, "Valid"


class SignalRanker:
    """
    Composite ranking engine for Strategy 1.3 Rev B / Master Spec v2.3+.
    Weights and discrete regime scores are sourced exclusively from src.quant_config.
    """

    def __init__(self, min_expectancy: float = 0, min_win_rate: float = 25, min_trades: int = 5):
        self.min_expectancy = min_expectancy
        self.min_win_rate = min_win_rate
        self.min_trades = min_trades
        self.signals_strong_buy = 0
        self.signals_buy = 0
        self.signals_watch = 0
        self.signals_speculative = 0
        
        # Context Providers & Scorer
        self.context_aggregator = ContextAggregator()
        self.context_scorer = ContextScorer()

    def normalize_percentile(self, series: pd.Series) -> pd.Series:
        """Convert a numeric Series to percentile ranks in [0, 100]."""
        if len(series) <= 1:
            return pd.Series([100.0] * len(series), index=series.index)
        return series.rank(pct=True, method="average") * 100.0

    def regime_adjustment(self, score: float, regime: str, stock_metrics: dict) -> float:
        """
        Calculate regime adjustment score (0-100).
        Fails closed: requires strategy and delegates strictly to compute_regime_alignment().
        """
        strat = stock_metrics.get("strategy_name") or stock_metrics.get("strategy")
        if not strat:
            raise ValueError("Missing required strategy in stock_metrics for regime_adjustment")
        return compute_regime_alignment(strat, regime)

    def compute_composite_score(self, row, regime: str, pool_stats: dict = None) -> dict:
        """
        Compute the final composite score and breakdown for a candidate.
        Uses Strategy-Specific Weights from src.quant_config, Historical Expectancy,
        Veto-Gated Context, and Continuous Regime Alignment.
        Fails closed: no silent neutral/zero defaults for missing or invalid features.
        """
        strategy = row.get("strategy_name") or row.get("strategy")
        if not strategy:
            raise ValueError("Missing required strategy in candidate row")
        strat_key = normalize_strategy_key(strategy)
        regime_key = normalize_regime_key(regime)

        # 1. Momentum score (must be finite and in [0.0, 100.0])
        if "momentum_score" in row and row["momentum_score"] is not None:
            ms_val = float(row["momentum_score"])
            if not np.isfinite(ms_val) or ms_val < 0.0 or ms_val > 100.0:
                raise ValueError(f"Invalid precomputed momentum_score: {ms_val} (must be finite in [0.0, 100.0])")
            momentum_score = ms_val
        else:
            momentum_score = compute_momentum_score(row)

        # 2. Historical Strategy Expectancy (must be finite and in [0.0, 100.0])
        if "expectancy_score" in row and row["expectancy_score"] is not None:
            exp_val = float(row["expectancy_score"])
            if not np.isfinite(exp_val) or exp_val < 0.0 or exp_val > 100.0:
                raise ValueError(f"Invalid precomputed expectancy_score: {exp_val}")
            expectancy_score = exp_val
        else:
            exp_val = row.get("expectancy_pct") if row.get("expectancy_pct") is not None else row.get("adjusted_expectancy_pct")
            expectancy_score = compute_expectancy_score(strat_key, exp_val)

        # 3. Historical Win Rate score (must be finite and in [0.0, 100.0])
        winrate_val = row.get("winrate_score")
        if winrate_val is None:
            winrate_val = row.get("win_rate")
        if winrate_val is None:
            winrate_val = row.get("past_win_rate")
        if winrate_val is None or not np.isfinite(winrate_val) or float(winrate_val) < 0.0 or float(winrate_val) > 100.0:
            raise ValueError(f"Missing or invalid winrate_score/win_rate for {row.get('ticker', 'unknown')}: {winrate_val}")
        winrate_score = float(winrate_val)

        # 4. Continuous Strategy-Dependent Regime Alignment
        if "regime_score" in row and row["regime_score"] is not None:
            rs_val = float(row["regime_score"])
            if not np.isfinite(rs_val) or rs_val < 0.0 or rs_val > 100.0:
                raise ValueError(f"Invalid precomputed regime_score: {rs_val}")
            regime_score = rs_val
        else:
            regime_score = compute_regime_alignment(strat_key, regime_key)

        # 5. Context Score with Veto Gates (Fix 3)
        # context_available=False means the context data could not be obtained; the context
        # component is then excluded and the other weights renormalized (missing != zero).
        context_available = row.get("context_available") is not False
        c_analyst = float(row.get("context_analyst", 0.0) or 0.0)
        c_earnings = float(row.get("context_earnings", 0.0) or 0.0)
        c_fundamental = float(row.get("context_fundamental", 0.0) or 0.0)
        c_news = float(row.get("context_news", 0.0) or 0.0)

        has_breakdown = any(
            float(row.get(k) or 0.0) > 0
            for k in ("context_analyst", "context_fundamental", "context_news")
        )
        context_max_points = row.get("context_max_points")

        if context_max_points is not None and float(context_max_points) > 0:
            context_score = compute_context_score(
                analyst_pts=c_analyst,
                fundamental_pts=c_fundamental,
                news_pts=c_news,
                de_ratio=row.get("de_ratio"),
                current_ratio=row.get("current_ratio"),
                finbert_sentiment=row.get("finbert_sentiment"),
                target_consensus=row.get("target_consensus"),
                price=row.get("price") or row.get("entry_price"),
                max_points=context_max_points,
            )
        elif not has_breakdown and "context_score" in row and row["context_score"] is not None:
            # If only raw context_score was passed, apply veto gates directly to that base score
            context_score = compute_context_score(
                base_score=float(row["context_score"]),
                de_ratio=row.get("de_ratio"),
                current_ratio=row.get("current_ratio"),
                finbert_sentiment=row.get("finbert_sentiment"),
                target_consensus=row.get("target_consensus"),
                price=row.get("price") or row.get("entry_price"),
            )
        else:
            context_score = compute_context_score(
                analyst_pts=c_analyst,
                fundamental_pts=c_fundamental,
                news_pts=c_news,
                de_ratio=row.get("de_ratio"),
                current_ratio=row.get("current_ratio"),
                finbert_sentiment=row.get("finbert_sentiment"),
                target_consensus=row.get("target_consensus"),
                price=row.get("price") or row.get("entry_price"),
            )

        # Strategy-Specific Weight Vector (Fix 4)
        if strat_key not in STRATEGY_WEIGHT_VECTORS:
            raise ValueError(f"Strategy '{strat_key}' weight vector not defined in STRATEGY_WEIGHT_VECTORS")
        w = STRATEGY_WEIGHT_VECTORS[strat_key]

        # Assert weights sum to 1.0
        assert abs(sum(w.values()) - 1.0) < 1e-9, f"Weights for {strat_key} must sum to 1.0!"

        # Explicitly guard and clamp all component scores to canonical [0.0, 100.0] range
        subscores = [
            ("momentum", momentum_score),
            ("expectancy", expectancy_score),
            ("winrate", winrate_score),
            ("regime", regime_score),
            ("context", context_score),
        ]
        for name, val in subscores:
            if val is None or np.isnan(val) or np.isinf(val):
                raise ValueError(f"Invalid non-finite subscore for {name}: {val}")

        momentum_score = max(0.0, min(100.0, float(momentum_score)))
        expectancy_score = max(0.0, min(100.0, float(expectancy_score)))
        winrate_score = max(0.0, min(100.0, float(winrate_score)))
        regime_score = max(0.0, min(100.0, float(regime_score)))
        context_score = max(0.0, min(100.0, float(context_score)))

        non_ctx_total = (
            w["mom"] * momentum_score
            + w["exp"] * expectancy_score
            + w["wr"] * winrate_score
            + w["reg"] * regime_score
        )
        if context_available:
            total = non_ctx_total + w["ctx"] * context_score
        else:
            total = non_ctx_total / (1.0 - w["ctx"])
        total = max(0.0, min(100.0, total))

        honest_rr = float(row.get("weighted_scaleout_rr") or row.get("weighted_rr_honest") or row.get("weighted_rr") or row.get("risk_reward") or 2.0)
        tier_label = assign_tier(total, honest_rr)

        return {
            "total": round(total, 4),
            "composite_score": round(total, 4),
            "tier_label": tier_label,
            "breakdown": {
                "momentum": round(momentum_score, 4),
                "expectancy": round(expectancy_score, 4),
                "winrate": round(winrate_score, 4),
                "regime": round(regime_score, 4),
                "context": round(context_score, 4) if context_available else None,
            },
            "context_available": context_available,
            "strategy": strat_key,
            "weights": w,
        }

    def composite_rank(self, df: pd.DataFrame, regime: str, top_n: int = 5) -> pd.DataFrame:
        """
        Canonical composite ranking pipeline delegating directly to compute_composite_score().
        Preserves backward compatibility while strictly enforcing the frozen quantitative specification.
        """
        if df.empty:
            return df.copy()

        result = df.copy()
        scored_rows = []

        self.signals_strong_buy = 0
        self.signals_buy = 0
        self.signals_watch = 0
        self.signals_speculative = 0

        for _, row in result.iterrows():
            row_dict = row.to_dict()
            try:
                res = self.compute_composite_score(row_dict, regime)
                tier = res["tier_label"]
                if tier == "Strong Buy":
                    self.signals_strong_buy += 1
                elif tier == "Buy":
                    self.signals_buy += 1
                elif tier == "Watch":
                    self.signals_watch += 1
                else:
                    self.signals_speculative += 1

                row_dict["composite_score"] = res["composite_score"]
                row_dict["quality_score"] = res["composite_score"]
                row_dict["tier_label"] = tier
                row_dict["tier"] = tier
                row_dict["score_breakdown"] = res["breakdown"]
                row_dict["momentum_score"] = res["breakdown"]["momentum"]
                row_dict["expectancy_score"] = res["breakdown"]["expectancy"]
                row_dict["winrate_score"] = res["breakdown"]["winrate"]
                row_dict["regime_score"] = res["breakdown"]["regime"]
                row_dict["context_score"] = res["breakdown"]["context"]
                scored_rows.append(row_dict)
            except Exception as e:
                logger.warning("Error scoring candidate %s: %s", row_dict.get("ticker", "unknown"), e)
                self.signals_speculative += 1

        if not scored_rows:
            return pd.DataFrame(columns=result.columns)

        out_df = pd.DataFrame(scored_rows)
        # Quality Filter: Only Strong Buy and Buy qualify as actionable recommendation ideas
        qualifying = out_df[out_df["tier_label"].isin(["Strong Buy", "Buy"])].copy()
        if qualifying.empty:
            logger.info("No qualifying stock ideas tonight.")
            return pd.DataFrame(columns=out_df.columns)

        qualifying = qualifying.sort_values("composite_score", ascending=False).reset_index(drop=True)
        if top_n is not None and top_n > 0 and len(qualifying) > top_n:
            return qualifying.head(top_n).reset_index(drop=True)
        return qualifying

    def _fetch_price_history(self, ticker: str) -> Optional[pd.DataFrame]:
        # Try new date-partitioned cache first (supports preloaded memory lookups)
        try:
            cache_manager = get_cache_manager()
            end_date = datetime.date.today()
            start_date = end_date - datetime.timedelta(days=120)
            ticker_data = cache_manager.get_ticker_history(ticker, start_date.isoformat(), end_date.isoformat())
            if ticker_data is not None and not ticker_data.empty:
                return ticker_data
        except Exception as e:
            logger.warning(f"Failed to fetch price history from date-partitioned cache for {ticker}: {e}")

        # Fallback to old per-ticker cache
        cache_path = os.path.join("data", "cache", f"{ticker.upper()}.parquet")
        if os.path.exists(cache_path):
            try:
                return pd.read_parquet(cache_path, engine="pyarrow")
            except Exception:
                pass
        
        # Fallback: Download via YahooProvider
        try:
            from src.providers.price.yahoo_provider import YahooProvider
            provider = YahooProvider()
            end_date = datetime.date.today().isoformat()
            start_date = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
            return provider.get_historical([ticker], start=start_date, end=end_date)
        except Exception as e:
            logger.warning("Failed to fetch price history for %s: %s", ticker, e)
            return None

    def rank(self, signals_df: pd.DataFrame, regime: Optional[str] = None, top_n: int = 5) -> pd.DataFrame:
        """
        Ranking interface delegating to composite_rank().
        Fails closed: requires explicit canonical regime ('bull', 'sideways', 'bear').
        Never fabricates missing indicator columns.
        """
        if not regime:
            raise ValueError("Missing required regime parameter in rank(). Must be 'bull', 'sideways', or 'bear'.")
        regime_key = normalize_regime_key(regime)
        return self.composite_rank(signals_df, regime_key, top_n)





if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    print("=" * 60)
    print("  SIGNAL COMPOSITE RANKER TEST (Strategy 1.3 Rev A)")
    print("=" * 60)

    # Test data mimicking actual conditions
    test_data = pd.DataFrame(
        {
            "ticker": ["ABNB", "AEE", "BALL", "TSLA", "NVDA", "INVALID", "XYZ"],
            "win_rate": [20.0, 18.5, 24.1, 45.0, 38.0, 10.0, 40.0],
            "expectancy_pct": [-3.41, -0.59, -0.60, 5.1, 4.2, -5.0, 3.5],
            "upside_pct": [32.5, 6.9, 33.2, 8.0, 15.0, 2.0, 10.0],
            "price": [140.0, 80.0, 60.0, 250.0, 120.0, 10.0, 100.0],
            "dma_50": [135.0, 82.0, 58.0, 240.0, 110.0, 15.0, 95.0],
            "current_rsi": [56.1, 53.9, 60.3, 52.0, 68.0, 30.0, 55.0],
            "volume_ratio": [1.10, 1.17, 2.33, 1.5, 2.0, 0.5, 1.2],
            "macd_histogram": [0.12, -0.05, 0.22, 0.45, 0.35, -0.20, 0.20],
            "total_trades": [15, 12, 10, 22, 30, 2, 15],
            "industry": ["Hotels, Resorts & Cruise Lines", "Multi-Utilities", "Metal, Glass & Plastic Containers", "Automobile Manufacturers", "Semiconductors", "Unknown", "Semiconductors"],
        }
    )

    print("\nInput data:")
    print(test_data[["ticker", "win_rate", "expectancy_pct", "total_trades"]].to_string(index=False))
    print()

    ranker = SignalRanker()
    
    # Test Bull regime
    result_bull = ranker.composite_rank(test_data, "bull", top_n=5)
    print("\nRanked output (BULL):")
    display_cols = ["ticker", "composite_score", "tier_label", "momentum_score", "expectancy_score", "winrate_score", "regime_score"]
    print(result_bull[display_cols].to_string(index=False))
    print()

    # Assertions
    tickers_ranked = result_bull["ticker"].tolist()
    assert len(tickers_ranked) == 3, f"Expected 3 ranked signals, got {len(tickers_ranked)}"
    
    tsla_row = result_bull[result_bull["ticker"] == "TSLA"].iloc[0]
    nvda_row = result_bull[result_bull["ticker"] == "NVDA"].iloc[0]
    assert tsla_row["tier_label"] in ("Strong Buy", "Buy"), f"TSLA expected Strong Buy or Buy, got {tsla_row['tier_label']}"
    assert nvda_row["tier_label"] in ("Strong Buy", "Buy"), f"NVDA expected Strong Buy or Buy, got {nvda_row['tier_label']}"
    
    # AEE and BALL should be filtered out since they are Watch/Speculative tier
    assert "AEE" not in tickers_ranked
    assert "BALL" not in tickers_ranked
    
    print("  All assertions passed!")
    print("=" * 60)
