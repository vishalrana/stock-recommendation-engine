"""
Signal Ranker — Strategy 1.3 Rev B
===================================
Composite-normalized, tiered ranking engine for Strategy 1.3.

Weights:
  - Technical Momentum (25%): RSI, Proximity to 50 DMA, Volume Ratio, MACD histogram
  - Risk-Adjusted Expectancy (35%): Z-score in pool, with negative expectancy penalty
  - Historical Win Rate (15%): Percentile rank
  - Regime Adjustment (10%): Bull/Bear/Sideways specific bonus
  - Context Score (15%): Analyst, earnings, fundamentals, news, price/volume events
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
from src.position_sizer import (
    assign_tier,
    allocate_capital,
    calculate_p_win,
    calculate_half_kelly,
    calculate_normalized_sizing,
    validate_candidate_for_allocation,
)

logger = logging.getLogger(__name__)


# Import canonical single source of truth configuration
from src.quant_config import (
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
    STRATEGY_HISTORICAL_EXPECTANCY,
    SURVIVORSHIP_BIAS_HAIRCUT,
    EXPECTANCY_BASE,
    EXPECTANCY_SLOPE,
    CONTEXT_VETO_THRESHOLDS,
)

# Backward-compatibility aliases
STRATEGY_OPTIMAL_REGIME = {
    'trend_following': 100,
    '52w_high_breakout': 100,
    'pullback_recovery': 70,
    'cross_sectional_momentum': 85,
    'pead': 75,
    'sector_rotation': 80,
    'mean_reversion': 30,
}
MARKET_REGIME_SCORE = {'bull': 100.0, 'sideways': 70.0, 'bear': 20.0}


def normalize_strategy_key(strategy: str) -> str:
    """Standardize strategy names to internal dictionary keys."""
    if not strategy:
        return 'trend_following'
    s = str(strategy).strip().lower().replace('-', '_').replace(' ', '_')
    if '52' in s or 'breakout' in s or 'high' in s:
        return '52w_high_breakout'
    if 'trend' in s:
        return 'trend_following'
    if 'pullback' in s:
        return 'pullback_recovery'
    if 'cross' in s or 'momentum' in s:
        return 'cross_sectional_momentum'
    if 'pead' in s or 'earnings' in s:
        return 'pead'
    if 'sector' in s or 'rotation' in s:
        return 'sector_rotation'
    if 'mean' in s or 'reversion' in s:
        return 'mean_reversion'
    return s


def compute_expectancy_score(strategy: str, adjusted_expectancy_pct: Optional[float] = None) -> float:
    """
    Master Spec v2.3+ formula: S_exp = 30 + 20 * E_adjusted
    where E_adjusted is expressed in percentage points (e.g. +1.44% -> 1.44 -> 58.8).
    """
    if adjusted_expectancy_pct is not None:
        e_val = float(adjusted_expectancy_pct)
    else:
        strat_key = normalize_strategy_key(strategy)
        hist_exp = STRATEGY_HISTORICAL_EXPECTANCY.get(strat_key, 0.0169 * SURVIVORSHIP_BIAS_HAIRCUT)
        e_val = round(hist_exp * 100.0, 2)
    return round(EXPECTANCY_BASE + EXPECTANCY_SLOPE * e_val, 4)


def compute_regime_alignment(strategy: str, market_regime: str) -> float:
    """
    Master Spec v2.3+ Exact Regime Score Matrix.
    Direct discrete matrix lookup across Bull, Sideways, and Bear regimes.
    """
    strat_key = normalize_strategy_key(strategy)
    regime_key = str(market_regime).strip().lower()
    if strat_key not in REGIME_SCORE_MATRIX:
        strat_key = "trend_following"
    regime_dict = REGIME_SCORE_MATRIX[strat_key]
    return float(regime_dict.get(regime_key, regime_dict.get("sideways", 70.0)))


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
) -> float:
    """
    Master Spec v2.3+ Context Score with Veto Gates:
    - Balance Sheet Distress: D/E > 2.5 AND Current Ratio < 1.0 -> cap context at 30.0
    - Negative News Sentiment: FinBERT < -0.30 -> cap context at 40.0
    - Severe Earnings Miss: Surprise < -10.0% -> penalty -20.0
    - Analyst Downside: Target < Price -> penalty -15.0
    """
    raw = float(analyst_pts or 0.0) + float(earnings_pts or 0.0) + float(fundamental_pts or 0.0) + float(news_pts or 0.0)

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

    # Veto Gate 3: Severe earnings miss (Surprise < -10.0%)
    if earnings_surprise_pct is not None:
        try:
            s_val = float(earnings_surprise_pct)
            # Handle both decimal ratio (e.g. -0.101) and percentage (e.g. -10.1)
            pct_val = s_val * 100.0 if -1.0 <= s_val <= 1.0 and s_val != 0.0 else s_val
            if pct_val < -10.0:
                raw = max(0.0, raw - 20.0)
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


def calculate_p_win(composite_score: float) -> float:
    """
    Fix 1: Replace coarse win-probability buckets with smooth sigmoid mapping:
    p_win = 0.35 + 0.40 / (1 + e^(-0.15 * (S_composite - 65)))
    Clamped to [0.35, 0.75], rounded to 4 decimal places.
    """
    s = float(composite_score)
    z = -0.15 * (s - 65.0)
    if z > 50.0:
        sigmoid_val = 0.0
    elif z < -50.0:
        sigmoid_val = 1.0
    else:
        sigmoid_val = 1.0 / (1.0 + math.exp(z))

    p = 0.35 + 0.40 * sigmoid_val
    return max(0.35, min(0.75, round(p, 4)))


def calculate_half_kelly(composite_score: float, honest_rr: float) -> float:
    """
    Calculate Half-Kelly fraction using sigmoid p_win and honest R:R.
    R_honest is used ONLY here, not in composite score.
    """
    p_win = calculate_p_win(composite_score)
    r = float(honest_rr) if float(honest_rr) > 0 else 1.0
    full_kelly = p_win - (1.0 - p_win) / r
    half_kelly = max(0.0, full_kelly / 2.0)
    return round(half_kelly, 4)


def compute_momentum_score(row: dict) -> float:
    """
    P0-2: Explicit continuous technical momentum score (0-100).
    Uses RSI, DMA 50 proximity, Volume Ratio, and MACD Histogram.
    """
    rsi = row.get("current_rsi")
    price = row.get("price") if row.get("price") is not None else row.get("entry_price")
    dma_50 = row.get("dma_50") if row.get("dma_50") is not None else row.get("ema20")
    volume_ratio = row.get("volume_ratio")
    macd_hist = row.get("macd_histogram", 0.0)

    if rsi is None or price is None or dma_50 is None or volume_ratio is None:
        raise ValueError(
            f"Missing required technical momentum features: rsi={rsi}, price={price}, dma_50={dma_50}, volume_ratio={volume_ratio}"
        )

    rsi_val = float(rsi)
    p_val = float(price)
    d_val = float(dma_50)
    v_val = float(volume_ratio)
    m_val = float(macd_hist or 0.0)

    # RSI score (peaks at 50, decreases as it moves away)
    rsi_score = max(0.0, min(100.0, 100.0 - abs(rsi_val - 50.0) * 4.0))

    # Proximity score to DMA 50
    proximity = abs(p_val / d_val - 1.0) if d_val > 0 else 0.0
    proximity_score = max(0.0, min(100.0, 100.0 - proximity * 500.0))

    # Volume score
    volume_score = max(0.0, min(100.0, v_val * 50.0))

    # MACD score
    macd_score = max(0.0, min(100.0, 50.0 + m_val * 200.0))

    raw_momentum = (rsi_score + proximity_score + volume_score + macd_score) / 4.0

    # Sigmoid weighting around 55.0
    sigmoid_weight = 1.0 / (1.0 + math.exp(-(raw_momentum - 55.0) / 5.0))
    momentum_score = raw_momentum * (0.5 + 0.5 * sigmoid_weight)
    return round(max(0.0, min(100.0, momentum_score)), 4)


def validate_candidate_features(row: dict) -> tuple[bool, str]:
    """
    P0-2: Validate that all required production features exist before composite scoring.
    """
    ticker = row.get("ticker")
    if not ticker:
        return False, "Missing ticker"

    strategy = row.get("strategy") or row.get("strategy_name")
    if not strategy:
        return False, "Missing strategy"

    # Momentum check: must either have momentum_score or technical inputs to compute it
    if row.get("momentum_score") is None:
        has_tech = (
            row.get("current_rsi") is not None
            and (row.get("price") is not None or row.get("entry_price") is not None)
            and (row.get("dma_50") is not None or row.get("ema20") is not None)
            and row.get("volume_ratio") is not None
        )
        if not has_tech:
            return False, "Missing momentum_score and required technical momentum features"

    # Win rate check: must have winrate_score or win_rate or past_win_rate
    if row.get("winrate_score") is None and row.get("win_rate") is None and row.get("past_win_rate") is None:
        return False, "Missing winrate_score / past_win_rate"

    return True, "Valid"


class SignalRanker:
    """
    Composite-normalized ranking engine with tiered fallback.
    """

    WEIGHT_MOMENTUM = 0.25
    WEIGHT_EXPECTANCY = 0.35
    WEIGHT_WIN_RATE = 0.15
    WEIGHT_REGIME = 0.10
    WEIGHT_CONTEXT = 0.15

    def __init__(self, min_expectancy: float = 0, min_win_rate: float = 25, min_trades: int = 5):
        # Kept for backward compatibility
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
        Delegates to continuous compute_regime_alignment if strategy is provided,
        otherwise preserves original defensive/momentum heuristic for backward compatibility.
        """
        strat = stock_metrics.get("strategy_name") or stock_metrics.get("strategy")
        if strat:
            return compute_regime_alignment(strat, regime)

        rsi = stock_metrics.get("current_rsi", 50.0)
        price = stock_metrics.get("price", 0.0)
        dma_50 = stock_metrics.get("dma_50", 0.0)
        industry = stock_metrics.get("industry", "")
        beta = stock_metrics.get("beta", 1.0)

        if regime == "bull":
            if (50.0 <= rsi <= 70.0) and (price > dma_50):
                return 100.0
            return 0.0
        elif regime == "bear":
            defensive_industries = {
                "Utilities", "Consumer Staples", "Health Care", 
                "Insurance", "Telecommunication Services"
            }
            ind_clean = str(industry).strip()
            is_defensive = (ind_clean in defensive_industries) or any(
                d in ind_clean for d in defensive_industries
            )
            if is_defensive or (beta < 1.0):
                return 100.0
            return 0.0
        elif regime == "sideways":
            if abs(rsi - 50.0) < 8.0:
                return 100.0
            return 0.0
        
        return 0.0

    def compute_composite_score(self, row, regime: str, pool_stats: dict = None) -> dict:
        """
        Compute the final composite score and breakdown for a candidate.
        Uses Strategy-Specific Weights (Fix 4), Historical Expectancy (Fix 2),
        Veto-Gated Context (Fix 3), and Continuous Regime Alignment (Fix 5).
        P0-2: No silent neutral/zero defaults for missing features.
        """
        strategy = row.get("strategy_name") or row.get("strategy")
        if not strategy:
            raise ValueError("Missing required field 'strategy' in row for composite scoring")
        strat_key = normalize_strategy_key(strategy)

        # 1. Momentum score (P0-2: No silent 50.0 fallback)
        if "momentum_score" in row and row["momentum_score"] is not None:
            momentum_score = float(row["momentum_score"])
        else:
            momentum_score = compute_momentum_score(row)

        # 2. Historical Strategy Expectancy (Fix 2: No circular R:R)
        if "expectancy_score" in row and row["expectancy_score"] is not None:
            expectancy_score = float(row["expectancy_score"])
        else:
            expectancy_score = compute_expectancy_score(strat_key)

        # 3. Historical Win Rate score (P0-2: No silent 50.0 fallback)
        winrate_val = row.get("winrate_score")
        if winrate_val is None:
            winrate_val = row.get("win_rate")
        if winrate_val is None:
            winrate_val = row.get("past_win_rate")
        if winrate_val is None:
            raise ValueError(f"Missing required field 'winrate_score'/'win_rate' for {row.get('ticker', 'unknown')}")
        winrate_score = float(winrate_val)

        # 4. Continuous Strategy-Dependent Regime Alignment (Fix 5)
        if "regime_score" in row and row["regime_score"] is not None:
            regime_score = float(row["regime_score"])
        else:
            regime_score = compute_regime_alignment(strat_key, regime)

        # 5. Context Score with Veto Gates (Fix 3)
        c_analyst = float(row.get("context_analyst", 0.0) or 0.0)
        c_earnings = float(row.get("context_earnings", 0.0) or 0.0)
        c_fundamental = float(row.get("context_fundamental", 0.0) or 0.0)
        c_news = float(row.get("context_news", 0.0) or 0.0)

        context_score = compute_context_score(
            analyst_pts=c_analyst,
            earnings_pts=c_earnings,
            fundamental_pts=c_fundamental,
            news_pts=c_news,
            de_ratio=row.get("de_ratio"),
            current_ratio=row.get("current_ratio"),
            earnings_surprise_pct=row.get("earnings_surprise_pct"),
            finbert_sentiment=row.get("finbert_sentiment"),
            target_consensus=row.get("target_consensus"),
            price=row.get("price") or row.get("entry_price"),
        )
        if "context_score" in row and row["context_score"] is not None and float(row["context_score"]) > 0:
            if c_analyst == 0.0 and c_earnings == 0.0 and c_fundamental == 0.0 and c_news == 0.0:
                context_score = float(row["context_score"])
            else:
                context_score = max(context_score, float(row["context_score"]))

        # Strategy-Specific Weight Vector (Fix 4)
        w = STRATEGY_WEIGHT_VECTORS.get(strat_key, STRATEGY_WEIGHT_VECTORS["trend_following"])

        # Assert weights sum to 1.0
        assert abs(sum(w.values()) - 1.0) < 1e-9, f"Weights for {strat_key} must sum to 1.0!"

        total = (
            w["mom"] * momentum_score
            + w["exp"] * expectancy_score
            + w["wr"] * winrate_score
            + w["reg"] * regime_score
            + w["ctx"] * context_score
        )

        honest_rr = float(row.get("weighted_rr_honest") or row.get("weighted_rr") or row.get("risk_reward") or 2.0)
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
                "context": round(context_score, 4),
            },
            "strategy": strat_key,
            "weights": w,
        }

    def composite_rank(self, df: pd.DataFrame, regime: str, top_n: int = 5) -> pd.DataFrame:
        """
        Full composite ranking pipeline with tiered fallback.
        """
        if df.empty:
            return df.copy()

        df_filtered = df.copy()

        # 1. Compute Technical Momentum (30% weight)
        # RSI score (peaks at 50, decreases as it moves away)
        rsi_vals = df_filtered["current_rsi"]
        rsi_score = 100.0 - (rsi_vals - 50.0).abs() * 4.0
        rsi_score = rsi_score.clip(lower=0.0, upper=100.0)

        # 50 DMA Proximity score
        price_vals = df_filtered["price"]
        dma_50_vals = df_filtered["dma_50"]
        proximity = (price_vals / dma_50_vals - 1.0).abs()
        proximity_score = 100.0 - proximity * 500.0
        proximity_score = proximity_score.clip(lower=0.0, upper=100.0)

        # Volume score
        vol_ratio_vals = df_filtered["volume_ratio"]
        volume_score = vol_ratio_vals * 50.0
        volume_score = volume_score.clip(lower=0.0, upper=100.0)

        # MACD score
        macd_hist_vals = df_filtered.get("macd_histogram", pd.Series(0.0, index=df_filtered.index))
        macd_score = 50.0 + macd_hist_vals * 200.0
        macd_score = macd_score.clip(lower=0.0, upper=100.0)

        raw_momentum = (rsi_score + proximity_score + volume_score + macd_score) / 4.0
        
        # Sigmoid transition instead of hard floor (Task 6.4)
        pct_normalized = self.normalize_percentile(raw_momentum)
        sigmoid_weights = 1.0 / (1.0 + np.exp(-(raw_momentum - 55.0) / 5.0))
        df_filtered["momentum_score"] = pct_normalized * sigmoid_weights

        # 2. Risk-Adjusted Expectancy (40% weight)
        mean_exp = df_filtered["expectancy_pct"].mean()
        std_exp = df_filtered["expectancy_pct"].std()
        if pd.isna(std_exp) or std_exp < 0.0001:
            z_scores = pd.Series(0.0, index=df_filtered.index)
        else:
            z_scores = (df_filtered["expectancy_pct"] - mean_exp) / std_exp

        # Map to 0-100 using sigmoid
        exp_score = 100.0 / (1.0 + np.exp(-z_scores))

        # Increase the negative expectancy penalty from -20 to -30:
        # If expectancy_pct < 0, raw = max(5, raw - 30)
        neg_mask = df_filtered["expectancy_pct"] < 0
        exp_score[neg_mask] = (exp_score[neg_mask] - 30.0).clip(lower=5.0)
        df_filtered["expectancy_score"] = exp_score

        # 3. Historical Win Rate (15% weight)
        df_filtered["winrate_score"] = self.normalize_percentile(df_filtered["win_rate"])

        # 4. Regime Adjustment & 5. Preliminary Composite Score (using old weights)
        regime_scores = []
        preliminary_scores = []
        for _, row in df_filtered.iterrows():
            reg_score = self.regime_adjustment(row["momentum_score"], regime, row)
            regime_scores.append(reg_score)
            
            # Compute preliminary using old weights (30% momentum, 40% expectancy, 20% winrate, 10% regime)
            m = row.get("momentum_score", 50.0)
            e = row.get("expectancy_score", 50.0)
            w = row.get("winrate_score", 50.0)
            
            prelim = 0.30 * m + 0.40 * e + 0.20 * w + 0.10 * reg_score
            
            # Apply absolute composite floor (commented out per filter relaxation)
            # expectancy_pct = row.get("expectancy_pct", 0.0)
            # win_rate = row.get("win_rate", 0.0)
            # if expectancy_pct < 0.0 and win_rate < 25.0:
            #     prelim = min(prelim, 40.0)
                
            preliminary_scores.append(prelim)

        df_filtered["regime_score"] = regime_scores
        df_filtered["preliminary_score"] = preliminary_scores

        # Sort candidates by preliminary score to pick top 50 (expanded from 30)
        df_sorted_prelim = df_filtered.sort_values("preliminary_score", ascending=False)
        top_50_tickers = set(df_sorted_prelim.head(50)["ticker"].tolist())
        logger.info("Selected top 50 candidates for Context/NLP scoring: %s", list(top_50_tickers))

        # Check if NLP should be skipped (for debugging)
        skip_nlp = os.getenv("SKIP_NLP", "false").lower() == "true"
        if skip_nlp:
            logger.info("SKIP_NLP=true - skipping context scoring")
            top_50_tickers = set()

        # 6. Context Scoring & Final Composite Score (Parallelized)
        def compute_context_score(ticker: str, row: dict) -> tuple:
            """Compute context score and components for a single ticker."""
            c_score = 0.0
            c_analyst = 0.0
            c_earnings = 0.0
            c_fundamental = 0.0
            c_news = 0.0
            try:
                price_df = self._fetch_price_history(ticker)
                if price_df is not None and not price_df.empty:
                    ctx = self.context_aggregator.get_aggregated(ticker, price_df)
                    current_price = row.get("price") or (price_df['Close'].iloc[-1] if not price_df.empty else 0.0)
                    tech_data = {
                        'rsi': row.get('current_rsi', 50),
                        'adx': row.get('adx_value', 20),
                        'volume_ratio': row.get('volume_ratio', 1.0),
                    }
                    c_score, c_analyst, c_earnings, c_fundamental, c_news = self.context_scorer.calculate_with_breakdown(ctx, float(current_price), tech_data)
                    
                    # If it was a cache miss, save the computed score to cache
                    if ctx.cached_score is None:
                        from src.providers.context.aggregator import save_context_to_cache
                        save_context_to_cache(ticker, c_score, ctx)
            except Exception as e:
                logger.warning("Failed context aggregation for %s: %s", ticker, e)
            return ticker, c_score, c_analyst, c_earnings, c_fundamental, c_news

        # Parallel context scoring for top 50 candidates
        context_score_map = {}
        if top_50_tickers and not skip_nlp:
            logger.info("Starting parallel context scoring for %d candidates", len(top_50_tickers))
            from concurrent.futures import as_completed, TimeoutError
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    executor.submit(compute_context_score, row["ticker"], row.to_dict()): row["ticker"]
                    for _, row in df_filtered.iterrows()
                    if row["ticker"] in top_50_tickers
                }
                
                try:
                    for future in as_completed(futures, timeout=60.0):
                        t_name = futures[future]
                        try:
                            ticker, c_score, c_analyst, c_earnings, c_fundamental, c_news = future.result(timeout=10.0)
                            context_score_map[ticker] = (c_score, c_analyst, c_earnings, c_fundamental, c_news)
                        except Exception as future_err:
                            logger.warning("Context scoring failed for %s: %s", t_name, future_err)
                except TimeoutError:
                    logger.warning("Parallel context scoring batch timed out after 60s; proceeding with available scores.")

        # Build final scores
        context_scores = []
        context_analysts = []
        context_earnings_list = []
        context_fundamentals = []
        context_news_list = []
        composite_scores = []
        score_breakdowns = []

        for _, row in df_filtered.iterrows():
            ticker = row["ticker"]
            c_data = context_score_map.get(ticker, (0.0, 0.0, 0.0, 0.0, 0.0))
            c_score, c_analyst, c_earnings, c_fundamental, c_news = c_data
            
            context_scores.append(c_score)
            context_analysts.append(c_analyst)
            context_earnings_list.append(c_earnings)
            context_fundamentals.append(c_fundamental)
            context_news_list.append(c_news)
            
            # Compute final composite score using shifted weights (25/35/15/10/15) for all candidates
            row_dict = row.to_dict()
            row_dict["context_score"] = c_score
            row_dict["regime_score"] = row["regime_score"]
            
            res = self.compute_composite_score(row_dict, regime)
            final_score = res["total"]
            breakdown = res["breakdown"]
                
            composite_scores.append(final_score)
            score_breakdowns.append(breakdown)

        df_filtered["context_score"] = context_scores
        df_filtered["context_analyst"] = context_analysts
        df_filtered["context_earnings"] = context_earnings_list
        df_filtered["context_fundamental"] = context_fundamentals
        df_filtered["context_news"] = context_news_list
        df_filtered["composite_score"] = composite_scores
        df_filtered["score_breakdown"] = score_breakdowns

        # Log context scores for all evaluated candidates in top 50
        computed_scores_log = [
            f"{t}: {s:.1f}" for t, s in zip(df_filtered["ticker"], df_filtered["context_score"]) if t in top_50_tickers
        ]
        logger.info("Context scores computed for top candidates: %s", ", ".join(computed_scores_log))

        # TASK 4: Regime-Aware Tier 1 Threshold
        TIER1_THRESHOLDS = {
            "bull":     80,
            "sideways": 75,
            "bear":     75,   # also require ctx_score > 50
        }
        threshold = TIER1_THRESHOLDS.get(regime.lower(), 75)
        logger.info(f"[SCORING] Regime={regime}, Tier1 threshold={threshold}")

        # 6. Assign Tier Labels using assign_tier
        tiers = []
        for _, row in df_filtered.iterrows():
            score = float(row["composite_score"])
            rr = float(row.get("weighted_rr_honest") or row.get("weighted_rr") or row.get("risk_reward") or 2.0)
            t_label = assign_tier(score, rr)
            if t_label == "Strong Buy":
                tiers.append(1)
            elif t_label == "Buy":
                tiers.append(2)
            elif t_label == "Watch":
                tiers.append(3)
            else:
                tiers.append(4)

        df_filtered["temp_tier"] = tiers

        # Save tier counts for scan_log tracking
        self.signals_strong_buy = int(sum(df_filtered["temp_tier"] == 1))
        self.signals_buy = int(sum(df_filtered["temp_tier"] == 2))
        self.signals_watch = int(sum(df_filtered["temp_tier"] == 3))
        self.signals_speculative = int(sum(df_filtered["temp_tier"] == 4))

        # Map temp_tier to tier_label
        tier_map = {1: "Strong Buy", 2: "Buy", 3: "Watch", 4: "Rejected"}
        df_filtered["tier_label"] = df_filtered["temp_tier"].map(tier_map)

        # Log all composite scores for debugging
        for _, r in df_filtered.iterrows():
            logger.info(f"[RANKER DEBUG] {r['ticker']}: Score={r['composite_score']:.1f}, Tier={r['tier_label']}, exp={r['expectancy_pct']:.2f}%, win={r['win_rate']:.1f}%, trades={r['total_trades']}")

        # Split by tier (only Tier 1 and Tier 2 are kept)
        t1_eligible = df_filtered[df_filtered["temp_tier"] == 1]
        t2_eligible = df_filtered[df_filtered["temp_tier"] == 2]

        t1_sorted = t1_eligible.sort_values("composite_score", ascending=False)
        t2_sorted = t2_eligible.sort_values("composite_score", ascending=False)

        # Auto-relax selection:
        # If < 3 Strong Buy candidates: relax to include Buy candidates
        # If 0 total (Strong Buy + Buy): return empty list
        total_eligible_count = len(t1_eligible) + len(t2_eligible)
        if total_eligible_count == 0:
            logger.info("No high-confidence setups tonight. Cash is a position.")
            result = pd.DataFrame(columns=df_filtered.columns)
            if "temp_tier" in result.columns:
                result = result.drop(columns=["temp_tier"])
            return result.reset_index(drop=True)

        if len(t1_eligible) >= 3:
            selected = pd.concat([t1_sorted, t2_sorted])
        else:
            selected = pd.concat([t1_eligible, t2_eligible]).sort_values("composite_score", ascending=False)

        result = selected.head(top_n).copy()

        # On-the-fly fallback context scoring for candidates in final recommendations that missed top 30
        fallback_happened = False
        if not skip_nlp:
            for idx, row in result.iterrows():
                ticker = row["ticker"]
                if row.get("context_score", 0.0) == 0.0 and ticker not in top_50_tickers:
                    logger.info("Triggering fallback on-the-fly context scoring for final recommended setup: %s", ticker)
                    c_score = 0.0
                    try:
                        price_df = self._fetch_price_history(ticker)
                        if price_df is not None and not price_df.empty:
                            ctx = self.context_aggregator.get_aggregated(ticker, price_df)
                            current_price = row.get("price") or (price_df['Close'].iloc[-1] if not price_df.empty else 0.0)
                            tech_data = {
                                'rsi': row.get('current_rsi', 50),
                                'adx': row.get('adx_value', 20),
                                'volume_ratio': row.get('volume_ratio', 1.0),
                            }
                            c_score, c_analyst, c_earnings, c_fundamental, c_news = self.context_scorer.calculate_with_breakdown(ctx, float(current_price), tech_data)
                    except Exception as e:
                        logger.warning("Failed context aggregation for fallback ticker %s: %s", ticker, e)
                        c_score = 0.0
                        c_analyst = 0.0
                        c_earnings = 0.0
                        c_fundamental = 0.0
                        c_news = 0.0

                    result.at[idx, "context_score"] = c_score
                    result.at[idx, "context_analyst"] = c_analyst
                    result.at[idx, "context_earnings"] = c_earnings
                    result.at[idx, "context_fundamental"] = c_fundamental
                    result.at[idx, "context_news"] = c_news
                    
                    # Recompute composite score
                    row_dict = result.loc[idx].to_dict()
                    row_dict["context_score"] = c_score
                    row_dict["regime_score"] = row["regime_score"]
                    res = self.compute_composite_score(row_dict, regime)
                    result.at[idx, "composite_score"] = res["total"]
                    result.at[idx, "score_breakdown"] = res["breakdown"]
                    fallback_happened = True

        if fallback_happened:
            result = result.sort_values("composite_score", ascending=False)

        result = result.drop(columns=["temp_tier"])
        return result.reset_index(drop=True)

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

    def rank(self, signals_df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
        """Backward compatibility wrapper mapping to composite_rank with default bull regime."""
        df = signals_df.copy()
        if "dma_50" not in df.columns:
            df["dma_50"] = df["price"]
        if "macd_histogram" not in df.columns:
            df["macd_histogram"] = 0.0
        return self.composite_rank(df, "bull", top_n)


def calculate_normalized_sizing(signals: list, portfolio_value: float, available_cash: float) -> list:
    """
    Backwards-compatible wrapper calling allocate_capital sequential funding.
    """
    from src.position_sizer import calculate_normalized_sizing as _cns
    return _cns(signals, portfolio_value, available_cash)





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
    
    # TSLA should be Tier 1 (Strong Buy) because scores are high and expectancy/winrate positive
    tsla_row = result_bull[result_bull["ticker"] == "TSLA"].iloc[0]
    nvda_row = result_bull[result_bull["ticker"] == "NVDA"].iloc[0]
    assert tsla_row["tier_label"] == "Strong Buy", f"TSLA expected Strong Buy, got {tsla_row['tier_label']}"
    assert nvda_row["tier_label"] == "Strong Buy", f"NVDA expected Strong Buy, got {nvda_row['tier_label']}"
    
    # AEE and BALL should be filtered out since they are Watch/Speculative tier
    assert "AEE" not in tickers_ranked
    assert "BALL" not in tickers_ranked
    
    print("  All assertions passed!")
    print("=" * 60)
